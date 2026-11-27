//! Pocket Assistant's desktop app: a tray icon and the dashboard window, a viewer of the engine on the cloud VM.

pub mod settings;

use serde::Serialize;
use std::path::PathBuf;
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_window_state::StateFlags;

const TRAY: &str = "tray";
/// Windows' autostart entry passes this: start in the tray with the dashboard hidden.
const AUTOSTART_ARG: &str = "--autostart";
const KEY_SERVICE: &str = "pocket-assistant-app";
const KEY_USER: &str = "device-key";

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Connection {
    base_url: String,
    key: String,
}

fn settings_file() -> Result<PathBuf, String> {
    settings::app_data("app.json").ok_or_else(|| "APPDATA is not set".to_string())
}

fn key_entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEY_SERVICE, KEY_USER).map_err(|e| format!("Can't reach Credential Manager: {e}"))
}

fn set_tooltip(app: &AppHandle, text: &str) {
    // Windows keeps 128 UTF-16 units including the terminating NUL
    let tip: String = format!("Pocket Assistant · {text}").chars().take(127).collect();
    let main = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Some(tray) = main.tray_by_id(TRAY) {
            let _ = tray.set_tooltip(Some(tip));
        }
    });
}

/// The saved address and key, if both are there and the address is still acceptable.
#[tauri::command]
fn load_connection() -> Option<Connection> {
    let address = settings::load_address(&settings_file().ok()?)?;
    let base_url = settings::base_url(&address, cfg!(debug_assertions)).ok()?;
    let key = key_entry().ok()?.inner.get_password().ok()?;
    Some(Connection { base_url, key })
}

/// What the dashboard will connect to for this address, or the reason it can't.
#[tauri::command]
fn check_address(address: String) -> Result<String, String> {
    settings::base_url(&address, cfg!(debug_assertions))
}

/// The laptop's key from M2, best effort.
///
/// shortcut: keyring 4.x builds Windows target names itself ("<user>.<service>"), and an explicit target needs
/// keyring-core, which isn't a direct dependency. So this reads the entry the M2 name most plausibly maps to;
/// any miss or error is None and pairing with a code is the fallback.
#[tauri::command]
fn legacy_key() -> Option<String> {
    keyring::Entry::new("pocket-assistant", "device-key").ok()?.inner.get_password().ok()
}

#[tauri::command]
fn save_connection(address: String, key: String) -> Result<(), String> {
    let address = address.trim().to_lowercase();
    settings::base_url(&address, cfg!(debug_assertions))?;
    key_entry()?.inner.set_password(&key).map_err(|e| format!("Can't save the key to Credential Manager: {e}"))?;
    settings::save_address(&settings_file()?, &address)
}

#[tauri::command]
fn forget_connection(app: AppHandle) -> Result<(), String> {
    forget(&app)
}

fn forget(app: &AppHandle) -> Result<(), String> {
    match key_entry()?.inner.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => {}
        Err(e) => return Err(format!("Can't remove the key from Credential Manager: {e}")),
    }
    settings::forget_address(&settings_file()?)?;
    set_tooltip(app, "Not connected");
    Ok(())
}

/// The dashboard says whether the engine answered; the tray tooltip follows.
#[tauri::command]
fn report_connection(app: AppHandle, online: bool, since: Option<String>) {
    let text = match settings_file().ok().and_then(|file| settings::load_address(&file)) {
        None => "Not connected".to_string(),
        Some(address) if online => format!("Connected to {address}"),
        Some(address) => match since {
            Some(since) => format!("Can't reach {address} since {since}"),
            None => format!("Can't reach {address}"),
        },
    };
    set_tooltip(&app, &text);
}

const KEEP_BACKUPS: usize = 7;

fn backups_dir() -> Result<PathBuf, String> {
    settings::local_data("backups").ok_or_else(|| "LOCALAPPDATA is not set".to_string())
}

/// The date of the newest backup this PC holds, so the dashboard only downloads a newer one.
#[tauri::command]
fn latest_backup_date() -> Option<String> {
    settings::latest_backup_date(&backups_dir().ok()?)
}

/// Keeps one downloaded backup. The body is the raw file; its name comes in the `x-backup-name` header.
/// Written to a temporary name and renamed, so a failure never leaves a half file under a backup name.
#[tauri::command]
fn save_backup(request: tauri::ipc::Request<'_>) -> Result<(), String> {
    let tauri::ipc::InvokeBody::Raw(bytes) = request.body() else { return Err("Expected the backup file's bytes".to_string()) };
    let name = request.headers().get("x-backup-name").and_then(|v| v.to_str().ok()).unwrap_or("");
    if !settings::valid_backup_name(name) {
        return Err("Not a backup file name".to_string());
    }
    let dir = backups_dir()?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("Can't create {}: {e}", dir.display()))?;
    let temp = dir.join(format!("{name}.tmp"));
    let target = dir.join(name);
    let saved = std::fs::write(&temp, bytes).and_then(|()| std::fs::rename(&temp, &target));
    if let Err(e) = saved {
        let _ = std::fs::remove_file(&temp);
        return Err(format!("Can't save {}: {e}", target.display()));
    }
    settings::prune_backups(&dir, KEEP_BACKUPS).map_err(|e| format!("Can't tidy {}: {e}", dir.display()))
}

fn show_dashboard(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("dashboard") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
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

fn build_tray(app: &AppHandle, starts_with_windows: bool) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open dashboard", true, None::<&str>)?;
    let autostart = CheckMenuItem::with_id(app, "autostart", "Start with Windows", true, starts_with_windows, None::<&str>)?;
    let forget_item = MenuItem::with_id(app, "forget", "Forget this engine", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let line = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&open, &autostart, &forget_item, &line, &quit_item])?;
    let mut tray = TrayIconBuilder::with_id(TRAY)
        .tooltip("Pocket Assistant · Not connected")
        .menu(&menu)
        .show_menu_on_left_click(false);
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
        "autostart" => {
            let manager = app.autolaunch();
            let _ = if manager.is_enabled().unwrap_or(false) { manager.disable() } else { manager.enable() };
            let _ = autostart.set_checked(manager.is_enabled().unwrap_or(false));
        }
        "forget" => {
            if forget(app).is_ok() {
                let _ = app.emit_to("dashboard", "connection-forgotten", ());
                show_dashboard(app);
            }
        }
        "quit" => app.exit(0),
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
        .invoke_handler(tauri::generate_handler![
            load_connection,
            check_address,
            legacy_key,
            save_connection,
            forget_connection,
            report_connection,
            latest_backup_date,
            save_backup
        ])
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
            // app.json exists only once connected, so a marker file tells a first start from a later one
            let marker = settings::app_data("started").ok_or("APPDATA is not set")?;
            let first_run = !settings_file()?.exists() && !marker.exists();
            if first_run {
                if let Some(dir) = marker.parent() {
                    let _ = std::fs::create_dir_all(dir);
                }
                let _ = std::fs::write(&marker, b"");
            }
            let starts_with_windows = autostart_at_launch(app.handle(), first_run);
            build_tray(app.handle(), starts_with_windows)?;
            if !std::env::args().any(|arg| arg == AUTOSTART_ARG) {
                show_dashboard(app.handle());
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Pocket Assistant");
}
