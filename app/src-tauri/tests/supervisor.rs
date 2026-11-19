//! The supervisor against a fake engine: tests/fake_engine.py, run as `assistant.cli` by the `python` on PATH.

use pocket_assistant_lib::backoff::RestartPolicy;
use pocket_assistant_lib::engine::{EngineState, Supervisor, SupervisorConfig};
use pocket_assistant_lib::settings::AppSettings;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

static NEXT: AtomicUsize = AtomicUsize::new(0);

fn secs(n: u64) -> Duration {
    Duration::from_secs(n)
}

fn ms(n: u64) -> Duration {
    Duration::from_millis(n)
}

struct Fake {
    dir: PathBuf,
    sup: Supervisor,
    seen: Arc<Mutex<Vec<EngineState>>>,
}

/// A scratch folder with the fake as `assistant/cli.py`, a config naming its behaviour, and an app.json for it.
fn fake(behaviour: &str, tune: impl FnOnce(SupervisorConfig) -> SupervisorConfig) -> Fake {
    let dir = std::env::temp_dir()
        .join(format!("pocket-supervisor-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::SeqCst)));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(dir.join("assistant")).unwrap();
    fs::write(dir.join("assistant").join("__init__.py"), "").unwrap();
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests").join("fake_engine.py");
    fs::copy(script, dir.join("assistant").join("cli.py")).unwrap();
    fs::write(dir.join("config.toml"), behaviour).unwrap();
    let settings =
        AppSettings { python: PathBuf::from("python"), config: dir.join("config.toml"), pythonpath: Some(dir.clone()) };
    fs::write(dir.join("app.json"), serde_json::to_string(&settings).unwrap()).unwrap();
    let config = tune(SupervisorConfig::new(dir.join("app.json"), dir.join("pocket-assistant.exe"), dir.join("app.log")));
    let seen = Arc::new(Mutex::new(Vec::new()));
    let record = seen.clone();
    let sup = Supervisor::new(config, move |state| record.lock().unwrap().push(state.clone()));
    Fake { dir, sup, seen }
}

impl Fake {
    fn read(&self, name: &str) -> String {
        fs::read_to_string(self.dir.join(name)).unwrap_or_default()
    }

    /// The pid of each engine the supervisor started, in order.
    fn runs(&self) -> Vec<u32> {
        self.read("runs.txt").split_whitespace().map(|pid| pid.parse().unwrap()).collect()
    }

    fn running(&self) -> bool {
        matches!(self.sup.state(), EngineState::Running { .. })
    }
}

fn wait_until(limit: Duration, mut done: impl FnMut() -> bool) -> bool {
    let until = Instant::now() + limit;
    while Instant::now() < until {
        if done() {
            return true;
        }
        std::thread::sleep(ms(50));
    }
    done()
}

/// A policy that has already seen 4 crashes: one more and it gives up.
fn nearly_exhausted() -> RestartPolicy {
    let mut policy = RestartPolicy::new();
    let now = Instant::now();
    for _ in 0..4 {
        policy.on_crash(now);
    }
    policy
}

fn alive(pid: u32) -> bool {
    let out = std::process::Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/NH", "/FO", "CSV"])
        .output()
        .unwrap();
    String::from_utf8_lossy(&out.stdout).contains(&format!("\"{pid}\""))
}

#[test]
fn the_password_reaches_the_engine_on_stdin_and_ready_makes_it_running() {
    let f = fake("ready", |c| c);
    f.sup.start();
    assert!(wait_until(secs(15), || f.running()), "{:?}", f.sup.state());
    assert_eq!(f.sup.state(), EngineState::Running { port: 47999 });
    let (port, password) = f.sup.connection().unwrap();
    assert_eq!(port, 47999);
    assert!(password.len() == 64 && password.chars().all(|c| c.is_ascii_hexdigit()), "{password}");
    assert_eq!(f.read("password.txt"), password);
    let args = f.read("args.txt");
    assert!(args.contains(&format!("--parent-pid {}", std::process::id())), "{args}");
    assert!(args.contains("--password-stdin") && !args.contains(&password), "{args}");
    f.sup.stop(secs(10));
    assert!(!f.read("app.log").contains(&password));
}

#[test]
fn stop_waits_for_a_slow_engine_and_leaves_no_process() {
    let f = fake("slow", |c| c);
    f.sup.start();
    assert!(wait_until(secs(15), || f.running()), "{:?}", f.sup.state());
    let pid = f.runs()[0];
    let began = Instant::now();
    f.sup.stop(secs(150));
    let took = began.elapsed();
    assert!(took >= ms(2500) && took < secs(20), "{took:?}");
    assert_eq!(f.sup.state(), EngineState::Stopped);
    assert!(!alive(pid));
}

#[test]
fn stop_kills_an_engine_that_does_not_finish_in_time() {
    let f = fake("stubborn", |c| c);
    f.sup.start();
    assert!(wait_until(secs(15), || f.running()), "{:?}", f.sup.state());
    let pid = f.runs()[0];
    let began = Instant::now();
    f.sup.stop(ms(800));
    assert!(began.elapsed() < secs(10), "{:?}", began.elapsed());
    assert_eq!(f.sup.state(), EngineState::Stopped);
    assert!(!alive(pid));
    let seen = f.seen.lock().unwrap().clone();
    let stopping = seen.iter().position(|s| *s == EngineState::Stopping).expect("the tray said stopping");
    assert_eq!(seen[stopping + 1..], [EngineState::Stopped], "{seen:?}");
}

#[test]
fn a_restart_waiting_when_quit_comes_starts_nothing() {
    let f = fake("slow", |c| c);
    f.sup.start();
    assert!(wait_until(secs(15), || f.running()), "{:?}", f.sup.state());
    let sup = f.sup.clone();
    let restart = std::thread::spawn(move || sup.restart()); // waits ~3 s for the job
    std::thread::sleep(ms(300));
    f.sup.shutdown(secs(30)); // Quit, meanwhile
    restart.join().unwrap();
    std::thread::sleep(ms(1500)); // a new engine would have started by now
    assert_eq!(f.runs().len(), 1);
    assert_eq!(f.sup.state(), EngineState::Stopped);
    f.sup.start(); // and after Quit nothing starts one either
    assert_eq!(f.runs().len(), 1);
}

#[test]
fn a_worktree_build_runs_its_own_engine_source() {
    // app.json names no source folder: the exe's place in a worktree supplies it at every start
    let f = fake("ready", |c| c);
    let worktree = f.dir.join("wt");
    fs::create_dir_all(worktree.join("src").join("assistant")).unwrap();
    fs::copy(f.dir.join("assistant").join("__init__.py"), worktree.join("src").join("assistant").join("__init__.py")).unwrap();
    fs::copy(f.dir.join("assistant").join("cli.py"), worktree.join("src").join("assistant").join("cli.py")).unwrap();
    let settings = AppSettings { python: PathBuf::from("python"), config: f.dir.join("config.toml"), pythonpath: None };
    fs::write(f.dir.join("app.json"), serde_json::to_string(&settings).unwrap()).unwrap();
    let exe = worktree.join("app").join("src-tauri").join("target").join("release").join("pocket-assistant.exe");
    let sup = Supervisor::new(SupervisorConfig::new(f.dir.join("app.json"), exe, f.dir.join("app.log")), |_| {});
    sup.start();
    assert!(wait_until(secs(15), || matches!(sup.state(), EngineState::Running { .. })), "{:?}", sup.state());
    sup.stop(secs(10));
}

#[test]
fn a_crash_is_restarted_per_the_policy() {
    let f = fake("crash", |c| c);
    let began = Instant::now();
    f.sup.start();
    assert!(wait_until(secs(15), || f.runs().len() >= 2), "{:?}", f.sup.state());
    assert!(began.elapsed() >= secs(1), "relaunched after {:?}", began.elapsed());
    std::thread::sleep(ms(500)); // the next delay is 5 s
    assert_eq!(f.runs().len(), 2);
    assert_eq!(f.sup.state(), EngineState::Starting);
    assert_eq!(f.sup.connection(), Err("starting"));
    f.sup.stop(secs(5));
    assert_eq!(f.sup.state(), EngineState::Stopped);
}

#[test]
fn a_crash_with_the_policy_exhausted_gives_up() {
    let f = fake("crash", |c| SupervisorConfig { policy: nearly_exhausted(), ..c });
    f.sup.start();
    assert!(wait_until(secs(15), || f.sup.state() == EngineState::GaveUp), "{:?}", f.sup.state());
    assert_eq!(f.runs().len(), 1);
    assert_eq!(f.sup.connection(), Err("stopped"));
}

#[test]
fn a_held_lock_is_not_a_crash_and_is_retried() {
    // with 4 crashes already counted, a fifth would give up: Running proves the lock exits counted none
    let f = fake("lock", |c| SupervisorConfig { policy: nearly_exhausted(), lock_retry: ms(200), ..c });
    f.sup.start();
    assert!(wait_until(secs(20), || f.running()), "{:?}", f.sup.state());
    assert_eq!(f.runs().len(), 3);
    let seen = f.seen.lock().unwrap().clone();
    let held = seen.iter().position(|s| *s == EngineState::LockHeld).expect("LockHeld was reported");
    let running = seen.iter().position(|s| matches!(s, EngineState::Running { .. })).unwrap();
    assert!(held < running, "{seen:?}");
    f.sup.stop(secs(10));
}

#[test]
fn a_held_lock_tells_the_dashboard() {
    let f = fake("lock", |c| SupervisorConfig { lock_retry: secs(60), ..c });
    f.sup.start();
    assert!(wait_until(secs(15), || f.sup.state() == EngineState::LockHeld), "{:?}", f.sup.state());
    assert_eq!(f.sup.connection(), Err("lock_held"));
    f.sup.stop(secs(5));
}

#[test]
fn stop_cancels_a_pending_retry() {
    let f = fake("lock", |c| SupervisorConfig { lock_retry: ms(300), ..c });
    f.sup.start();
    assert!(wait_until(secs(15), || f.sup.state() == EngineState::LockHeld), "{:?}", f.sup.state());
    f.sup.stop(secs(5));
    let runs = f.runs().len();
    std::thread::sleep(secs(1));
    assert_eq!(f.runs().len(), runs);
    assert_eq!(f.sup.state(), EngineState::Stopped);
}

#[test]
fn a_chatty_engine_is_not_blocked_by_its_pipe() {
    let f = fake("chatty", |c| c);
    f.sup.start();
    assert!(wait_until(secs(30), || f.dir.join("chatted.txt").exists()), "the engine blocked on its stdout");
    assert!(f.running(), "{:?}", f.sup.state());
    let pid = f.runs()[0];
    f.sup.stop(secs(10));
    assert_eq!(f.sup.state(), EngineState::Stopped);
    assert!(!alive(pid));
}

#[test]
fn an_engine_that_never_says_ready_is_ended_and_started_again() {
    let f = fake("silent", |c| SupervisorConfig { ready_timeout: ms(800), ..c });
    f.sup.start();
    assert!(wait_until(secs(15), || f.runs().len() >= 2), "{:?}", f.sup.state());
    assert!(!alive(f.runs()[0]));
    f.sup.stop(secs(5));
}

#[test]
fn a_missing_python_gives_missing_with_the_settings_file_named() {
    let f = fake("ready", |c| c);
    let nowhere = AppSettings {
        python: f.dir.join("nowhere").join("python.exe"),
        config: f.dir.join("config.toml"),
        pythonpath: None,
    };
    fs::write(f.dir.join("app.json"), serde_json::to_string(&nowhere).unwrap()).unwrap();
    f.sup.start();
    match f.sup.state() {
        EngineState::Missing(why) => assert!(why.contains(&f.dir.join("app.json").display().to_string()), "{why}"),
        other => panic!("{other:?}"),
    }
    assert_eq!(f.sup.connection(), Err("stopped"));
}
