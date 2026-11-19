//! Pocket Assistant's desktop app: a tray icon, the dashboard window, and the engine it supervises.

pub mod backoff;
pub mod engine;
pub mod settings;

use engine::{EngineState, Supervisor, SupervisorConfig, STOP_WAIT};
use serde::Serialize;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_window_state::StateFlags;

const TRAY: &str = "tray";
/// Windows' autostart entry passes this: start in the tray with the dashboard hidden.
const AUTOSTART_ARG: &str = "--autostart";
static QUITTING: AtomicBool = AtomicBool::new(false);

#[derive(Serialize)]
struct Connection {
    port: u16,
    password: String,
}

/// The dashboard signs in with this; before the engine is ready it says why ("starting", "stopped", "lock_held").
#[tauri::command]
fn engine_connection(supervisor: State<'_, Supervisor>) -> Result<Connection, String> {
    supervisor.connection().map(|(port, password)| Connection { port, password }).map_err(String::from)
}

/// Opens the profile in its default editor: only an existing file named profile.toml.
#[tauri::command]
fn open_profile(app: AppHandle, path: String) -> Result<(), String> {
    let file = Path::new(&path);
    if file.file_name().is_none_or(|name| name != "profile.toml") || !file.is_file() {
        return Err(format!("Not a profile file: {path}"));
    }
    app.opener().open_path(path, None::<&str>).map_err(|e| e.to_string())
}

fn tooltip(state: &EngineState, settings_file: &Path) -> String {
    let what = match state {
        EngineState::Starting => "engine starting".to_string(),
        EngineState::Running { .. } => "engine running".to_string(),
        EngineState::Stopping => "engine stopping…".to_string(),
        EngineState::Stopped => "engine stopped".to_string(),
        EngineState::GaveUp => "engine stopped (details in app.log)".to_string(),
        EngineState::LockHeld => "engine already running in a terminal".to_string(),
        EngineState::Missing(_) => format!("can't find the engine: edit {}", settings_file.display()),
    };
    // Windows keeps 128 UTF-16 units including the terminating NUL
    format!("Pocket Assistant · {what}").chars().take(127).collect()
}

fn show_dashboard(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("dashboard") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// The engine finishes its current job first (up to STOP_WAIT); the tray says "stopping…" meanwhile.
fn quit(app: &AppHandle) {
    if QUITTING.swap(true, Ordering::SeqCst) {
        return;
    }
    let (app, supervisor) = (app.clone(), app.state::<Supervisor>().inner().clone());
    std::thread::spawn(move || {
        supervisor.shutdown(STOP_WAIT);
        app.exit(0);
    });
}

/// On by default: a release build turns it on at its first start, and enabling again records this exe's path,
/// so the newest release build is the one started. Debug builds never touch it. Returns whether it is on.
fn autostart_at_launch(app: &AppHandle, first_run: bool) -> bool {
    let manager = app.autolaunch();
    if cfg!(not(debug_assertions)) && (first_run || manager.is_enabled().unwrap_or(false)) {
        let _ = manager.enable();
    }
    manager.is_enabled().unwrap_or(false)
}

fn build_tray(app: &AppHandle, tip: String, starts_with_windows: bool) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open dashboard", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "Restart engine", true, None::<&str>)?;
    let autostart = CheckMenuItem::with_id(app, "autostart", "Start with Windows", true, starts_with_windows, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let line = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&open, &restart, &autostart, &line, &quit_item])?;
    let mut tray = TrayIconBuilder::with_id(TRAY).tooltip(tip).menu(&menu).show_menu_on_left_click(false);
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.on_tray_icon_event(|tray, event| {
        if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
            show_dashboard(tray.app_handle());
        }
    })
    .on_menu_event(move |app, event| match event.id.as_ref() {
        "open" => show_dashboard(app),
        "restart" => {
            let supervisor = app.state::<Supervisor>().inner().clone();
            std::thread::spawn(move || supervisor.restart()); // waits for the current job: not on the UI thread
        }
        "autostart" => {
            let manager = app.autolaunch();
            let _ = if manager.is_enabled().unwrap_or(false) { manager.disable() } else { manager.enable() };
            let _ = autostart.set_checked(manager.is_enabled().unwrap_or(false));
        }
        "quit" => quit(app),
        _ => {}
    })
    .build(app)?;
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        // first: a second launch only shows this one's dashboard
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_dashboard(app)))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec![AUTOSTART_ARG])))
        .plugin(tauri_plugin_opener::init())
        // the size and place are remembered; whether it was shown is not (an autostart stays in the tray)
        .plugin(tauri_plugin_window_state::Builder::default().with_state_flags(StateFlags::all().difference(StateFlags::VISIBLE)).build())
        .invoke_handler(tauri::generate_handler![engine_connection, open_profile])
        .on_window_event(|window, event| {
            // closing the dashboard only hides it: it opens again instantly from the tray
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "dashboard" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(|app| {
            let settings_file = settings::app_data("app.json").ok_or("APPDATA is not set")?;
            let log_file = settings::app_data("app.log").ok_or("APPDATA is not set")?;
            let first_run = !settings_file.exists();
            let config = SupervisorConfig::new(settings_file.clone(), std::env::current_exe()?, log_file);
            let handle = app.handle().clone();
            let supervisor = Supervisor::new(config, move |state| {
                let (tip, main) = (tooltip(state, &settings_file), handle.clone());
                let _ = handle.run_on_main_thread(move || {
                    if let Some(tray) = main.tray_by_id(TRAY) {
                        let _ = tray.set_tooltip(Some(tip));
                    }
                });
            });
            app.manage(supervisor.clone());
            // before the tray, so its check item shows the setting as it now is
            let starts_with_windows = autostart_at_launch(app.handle(), first_run);
            build_tray(app.handle(), tooltip(&EngineState::Starting, Path::new("")), starts_with_windows)?;
            if !std::env::args().any(|arg| arg == AUTOSTART_ARG) {
                show_dashboard(app.handle());
            }
            supervisor.start();
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Pocket Assistant");
}
