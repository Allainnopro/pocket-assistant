//! The engine as a child process: started with a fresh launch password on stdin, running once it prints READY,
//! restarted after a crash, stopped by closing its stdin (the current job finishes) and killed only after a wait.

use crate::backoff::RestartPolicy;
use crate::settings::{worktree_source, AppSettings};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::thread;
use std::time::{Duration, Instant};

/// A model call can take 120 s and a mail step 30 s: Quit and Restart wait this long before killing the engine.
pub const STOP_WAIT: Duration = Duration::from_secs(150);
/// The engine's exit code when engine.lock is held (a terminal engine runs): not a crash.
const ENGINE_RUNNING_EXIT: i32 = 3;
const LOG_LIMIT: u64 = 1024 * 1024;
const POLL: Duration = Duration::from_millis(100);
const KILL_WAIT: Duration = Duration::from_secs(10);
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Clone, PartialEq)]
pub enum EngineState {
    Starting,
    Running { port: u16 },
    Stopping,
    Stopped,
    GaveUp,
    LockHeld,
    Missing(String),
}

pub struct SupervisorConfig {
    /// app.json, read at every start: an edit takes effect on "Restart engine".
    pub settings_file: PathBuf,
    /// Where discovery starts when app.json doesn't exist yet.
    pub exe: PathBuf,
    /// app.log: the engine's output and the app's own notes; never the password.
    pub log_file: PathBuf,
    pub policy: RestartPolicy,
    /// How often to try again while a terminal engine holds engine.lock.
    pub lock_retry: Duration,
    /// How long a starting engine has to print READY before it counts as a crash.
    pub ready_timeout: Duration,
}

impl SupervisorConfig {
    pub fn new(settings_file: PathBuf, exe: PathBuf, log_file: PathBuf) -> Self {
        SupervisorConfig {
            settings_file,
            exe,
            log_file,
            policy: RestartPolicy::new(),
            lock_retry: Duration::from_secs(10),
            ready_timeout: Duration::from_secs(30),
        }
    }
}

#[derive(Clone)]
pub struct Supervisor {
    inner: Arc<Inner>,
}

struct Inner {
    settings_file: PathBuf,
    exe: PathBuf,
    lock_retry: Duration,
    ready_timeout: Duration,
    on_change: Box<dyn Fn(&EngineState) + Send + Sync>,
    log: Mutex<LogFile>,
    shared: Mutex<Shared>,
    changed: Condvar,
}

struct Shared {
    state: EngineState,
    password: String,
    process: Option<Process>,
    stop_requested: bool,
    next_id: u64,
    /// Bumped to cancel a pending (re)start.
    timer: u64,
    policy: RestartPolicy,
    /// Quit: nothing starts an engine any more, not even a Restart that was already waiting.
    closed: bool,
}

/// The engine process while it runs.
struct Process {
    id: u64,
    child: Arc<Mutex<Child>>,
    /// Dropping it is the stop request.
    stdin: Option<ChildStdin>,
}

impl Supervisor {
    /// `on_change` runs with the supervisor's lock held: it must not block or call back into the supervisor.
    pub fn new(config: SupervisorConfig, on_change: impl Fn(&EngineState) + Send + Sync + 'static) -> Self {
        let shared = Shared {
            state: EngineState::Stopped,
            password: String::new(),
            process: None,
            stop_requested: false,
            next_id: 0,
            timer: 0,
            policy: config.policy,
            closed: false,
        };
        Supervisor {
            inner: Arc::new(Inner {
                settings_file: config.settings_file,
                exe: config.exe,
                lock_retry: config.lock_retry,
                ready_timeout: config.ready_timeout,
                on_change: Box::new(on_change),
                log: Mutex::new(LogFile::new(config.log_file, LOG_LIMIT)),
                shared: Mutex::new(shared),
                changed: Condvar::new(),
            }),
        }
    }

    pub fn start(&self) {
        let mut s = self.inner.lock();
        if s.process.is_none() && !s.closed {
            s.timer += 1;
            spawn(&self.inner, &mut s);
        }
    }

    /// Close the engine's stdin and wait up to `wait` for it to finish its job; then kill it.
    pub fn stop(&self, wait: Duration) {
        let inner = &self.inner;
        let mut s = inner.lock();
        s.timer += 1; // no pending restart either
        let Some(process) = s.process.as_mut() else {
            return inner.set(&mut s, EngineState::Stopped);
        };
        let id = process.id;
        process.stdin = None;
        let child = process.child.clone();
        s.stop_requested = true;
        inner.note("stopping the engine");
        inner.set(&mut s, EngineState::Stopping);
        let (s, waited) = inner.changed.wait_timeout_while(s, wait, |s| s.runs(id)).unwrap_or_else(PoisonError::into_inner);
        if !waited.timed_out() {
            return;
        }
        drop(s);
        inner.note(&format!("the engine didn't stop within {} s: ending it", wait.as_secs()));
        let _ = child.lock().unwrap_or_else(PoisonError::into_inner).kill();
        let s = inner.lock();
        let _ = inner.changed.wait_timeout_while(s, KILL_WAIT, |s| s.runs(id));
    }

    /// Quit: stop for good. A Restart already waiting, a pending retry or a later start() start nothing.
    pub fn shutdown(&self, wait: Duration) {
        self.inner.lock().closed = true;
        self.stop(wait);
    }

    pub fn restart(&self) {
        self.stop(STOP_WAIT);
        self.inner.lock().policy = RestartPolicy::new(); // a restart by hand starts the crash count afresh
        self.start();
    }

    pub fn state(&self) -> EngineState {
        self.inner.lock().state.clone()
    }

    /// The port and launch password, or why there are none: "starting", "stopped" or "lock_held".
    pub fn connection(&self) -> Result<(u16, String), &'static str> {
        let s = self.inner.lock();
        match s.state {
            EngineState::Running { port } => Ok((port, s.password.clone())),
            // Stopping is seen only during "Restart engine": Quit closes the app
            EngineState::Starting | EngineState::Stopping => Err("starting"),
            EngineState::LockHeld => Err("lock_held"),
            _ => Err("stopped"),
        }
    }
}

impl Shared {
    fn runs(&self, id: u64) -> bool {
        self.process.as_ref().is_some_and(|p| p.id == id)
    }

    /// Waiting for READY: a first start, or another try while a terminal engine held the lock.
    fn starting(&self) -> bool {
        matches!(self.state, EngineState::Starting | EngineState::LockHeld)
    }
}

impl Inner {
    fn lock(&self) -> MutexGuard<'_, Shared> {
        self.shared.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn set(&self, s: &mut Shared, state: EngineState) {
        s.state = state;
        (self.on_change)(&s.state);
        self.changed.notify_all();
    }

    fn note(&self, text: &str) {
        self.line(&format!("[app] {text}"));
    }

    fn line(&self, text: &str) {
        self.log.lock().unwrap_or_else(PoisonError::into_inner).line(text);
    }
}

fn spawn(inner: &Arc<Inner>, s: &mut Shared) {
    let settings = match AppSettings::load_or_create(&inner.settings_file, &inner.exe) {
        Ok(found) => found,
        Err(why) => {
            inner.note(&why);
            return inner.set(s, EngineState::Missing(why));
        }
    };
    let mut command = Command::new(&settings.python);
    command
        .args(["-m", "assistant.cli", "--config"])
        .arg(&settings.config)
        .args(["engine", "--parent-pid", &std::process::id().to_string(), "--password-stdin"])
        .env("PYTHONIOENCODING", "utf-8") // a pipe would otherwise get the ANSI code page
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(dir) = settings.pythonpath.clone().or_else(|| worktree_source(&inner.exe)) {
        command.env("PYTHONPATH", dir);
    }
    #[cfg(windows)]
    std::os::windows::process::CommandExt::creation_flags(&mut command, CREATE_NO_WINDOW);
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(e) => {
            let why = format!(
                "Can't start the engine with {}: {e}. Edit {}",
                settings.python.display(),
                inner.settings_file.display()
            );
            inner.note(&why);
            return inner.set(s, EngineState::Missing(why));
        }
    };
    let password = new_password();
    let mut stdin = child.stdin.take().expect("stdin is piped");
    let _ = stdin.write_all(format!("{password}\n").as_bytes()); // if the engine is gone already, its exit says why
    let stdout = child.stdout.take().expect("stdout is piped");
    let stderr = child.stderr.take().expect("stderr is piped");
    s.next_id += 1;
    let id = s.next_id;
    let child = Arc::new(Mutex::new(child));
    s.process = Some(Process { id, child: child.clone(), stdin: Some(stdin) });
    s.password = password;
    s.stop_requested = false;
    inner.note(&format!("starting the engine ({} with {})", settings.python.display(), settings.config.display()));
    if s.state != EngineState::LockHeld {
        inner.set(s, EngineState::Starting); // while the lock is held, the tray keeps saying so between tries
    }

    let (out, err) = (inner.clone(), inner.clone());
    thread::spawn(move || forward(&out, stdout, Some(id)));
    thread::spawn(move || forward(&err, stderr, None));

    let waiter = inner.clone();
    thread::spawn(move || {
        let code = loop {
            let status = child.lock().unwrap_or_else(PoisonError::into_inner).try_wait();
            match status {
                Ok(Some(status)) => break status.code(),
                Ok(None) => thread::sleep(POLL),
                Err(_) => break None,
            }
        };
        exited(&waiter, id, code);
    });

    let timer = inner.clone();
    thread::spawn(move || {
        thread::sleep(timer.ready_timeout);
        let s = timer.lock();
        if s.starting() && s.runs(id) {
            timer.note(&format!("no READY within {} s: ending the engine", timer.ready_timeout.as_secs()));
            if let Some(process) = &s.process {
                let _ = process.child.lock().unwrap_or_else(PoisonError::into_inner).kill();
            }
        }
    });
}

/// Every line of an engine pipe goes to app.log; on stdout, READY makes the engine `id` running.
fn forward(inner: &Inner, pipe: impl Read, ready_for: Option<u64>) {
    let mut reader = BufReader::new(pipe);
    let mut buf = Vec::new();
    loop {
        buf.clear();
        match reader.read_until(b'\n', &mut buf) {
            Ok(0) | Err(_) => return,
            Ok(_) => {}
        }
        let text = String::from_utf8_lossy(&buf);
        let line = text.trim_end();
        inner.line(line);
        if let (Some(id), Some(port)) = (ready_for, parse_ready_line(line)) {
            let mut s = inner.lock();
            if s.starting() && s.runs(id) {
                inner.set(&mut s, EngineState::Running { port });
            }
        }
    }
}

fn exited(inner: &Arc<Inner>, id: u64, code: Option<i32>) {
    let mut s = inner.lock();
    if !s.runs(id) {
        return;
    }
    s.process = None;
    if s.stop_requested {
        inner.note("the engine stopped");
        return inner.set(&mut s, EngineState::Stopped);
    }
    if code == Some(ENGINE_RUNNING_EXIT) {
        if s.state != EngineState::LockHeld {
            inner.note("a terminal engine holds engine.lock: taking over once it stops");
            inner.set(&mut s, EngineState::LockHeld);
        }
        return schedule(inner, &mut s, inner.lock_retry);
    }
    let shown = code.map_or_else(|| "unknown".to_string(), |c| c.to_string());
    match s.policy.on_crash(Instant::now()) {
        Some(delay) => {
            inner.note(&format!("the engine exited (code {shown}): restarting in {} s", delay.as_secs()));
            inner.set(&mut s, EngineState::Starting);
            schedule(inner, &mut s, delay);
        }
        None => {
            inner.note(&format!("the engine exited (code {shown}): 5 crashes in 10 minutes, not restarting"));
            inner.set(&mut s, EngineState::GaveUp);
        }
    }
}

/// Start the engine again after `delay`, unless something else happened first.
fn schedule(inner: &Arc<Inner>, s: &mut Shared, delay: Duration) {
    s.timer += 1;
    let token = s.timer;
    let inner = inner.clone();
    thread::spawn(move || {
        thread::sleep(delay);
        let mut s = inner.lock();
        if s.timer == token && s.process.is_none() && !s.closed {
            spawn(&inner, &mut s);
        }
    });
}

/// 32 random bytes as hex: new for every engine start.
fn new_password() -> String {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).expect("the system's random source works");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// app.log, rolled over to app.log.old once it passes `limit` bytes: the engine logs to stderr all day.
struct LogFile {
    path: PathBuf,
    limit: u64,
    file: Option<File>,
    size: u64,
}

impl LogFile {
    fn new(path: PathBuf, limit: u64) -> Self {
        LogFile { path, limit, file: None, size: 0 }
    }

    fn line(&mut self, text: &str) {
        if self.size > self.limit {
            self.file = None;
            let _ = fs::rename(&self.path, self.path.with_extension("log.old"));
            self.size = 0;
        }
        if self.file.is_none() {
            if let Some(dir) = self.path.parent() {
                let _ = fs::create_dir_all(dir);
            }
            self.file = OpenOptions::new().create(true).append(true).open(&self.path).ok();
            self.size = self.file.as_ref().and_then(|f| f.metadata().ok()).map_or(0, |m| m.len());
        }
        if let Some(file) = &mut self.file {
            if writeln!(file, "{text}").is_ok() {
                self.size += text.len() as u64 + 1;
            }
        }
    }
}

/// `READY port=<n>`, the engine's line once its API listens.
pub fn parse_ready_line(line: &str) -> Option<u16> {
    let port = line.strip_prefix("READY port=")?;
    if port.is_empty() || !port.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    port.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_ready_line_accepts_only_the_ready_line() {
        assert_eq!(parse_ready_line("READY port=47615"), Some(47615));
        for other in ["READY port=", "READY port=abc", "READY port=70000", "xREADY port=1", "READY port=1 x", "ready"] {
            assert_eq!(parse_ready_line(other), None, "{other}");
        }
    }

    #[test]
    fn the_log_rolls_over_past_its_limit() {
        let dir = std::env::temp_dir().join(format!("pocket-log-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join("app.log");
        let mut log = LogFile::new(path.clone(), 100);
        for i in 0..20 {
            log.line(&format!("line {i}"));
        }
        let old = std::fs::read_to_string(dir.join("app.log.old")).unwrap();
        let now = std::fs::read_to_string(&path).unwrap();
        assert!(old.len() > 100 && now.len() <= 100, "{old:?} {now:?}");
        assert!(now.contains("line 19"));
    }
}
