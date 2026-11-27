//! The saved connection: the engine's address in `%APPDATA%\PocketAssistant\app.json` (`{ "address": ... }`).
//! The device key lives in Windows Credential Manager, never in this file.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize)]
struct Saved {
    address: String,
}

/// `https://<name>.ts.net` from what the user typed; with `allow_local`, also `http://127.0.0.1:<1024-65535>`.
pub fn base_url(address: &str, allow_local: bool) -> Result<String, String> {
    let address = address.trim().to_lowercase();
    if let Some(port) = address.strip_prefix("127.0.0.1:").filter(|_| allow_local) {
        let ok = port.bytes().all(|b| b.is_ascii_digit()) && port.parse::<u16>().is_ok_and(|p| p >= 1024);
        return if ok { Ok(format!("http://{address}")) } else { Err(REFUSAL.to_string()) };
    }
    let valid = address.strip_suffix(".ts.net").is_some_and(|name| {
        name.split('.').all(|label| !label.is_empty() && label.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-'))
    });
    if valid { Ok(format!("https://{address}")) } else { Err(REFUSAL.to_string()) }
}

/// The saved address, or None when there is none or the file isn't ours.
pub fn load_address(file: &Path) -> Option<String> {
    let saved: Saved = serde_json::from_str(&std::fs::read_to_string(file).ok()?).ok()?;
    Some(saved.address)
}

pub fn save_address(file: &Path, address: &str) -> Result<(), String> {
    if let Some(dir) = file.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("Can't create {}: {e}", dir.display()))?;
    }
    let text = serde_json::to_string(&Saved { address: address.to_string() }).expect("address serializes");
    std::fs::write(file, text).map_err(|e| format!("Can't write {}: {e}", file.display()))
}

pub fn forget_address(file: &Path) -> Result<(), String> {
    match std::fs::remove_file(file) {
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(format!("Can't remove {}: {e}", file.display())),
        _ => Ok(()),
    }
}

/// `%APPDATA%\PocketAssistant\<name>`.
pub fn app_data(name: &str) -> Option<PathBuf> {
    std::env::var_os("APPDATA").map(|dir| PathBuf::from(dir).join("PocketAssistant").join(name))
}

/// `%LOCALAPPDATA%\PocketAssistant\<name>`: big, rebuildable data stays out of the roaming profile.
pub fn local_data(name: &str) -> Option<PathBuf> {
    std::env::var_os("LOCALAPPDATA").map(|dir| PathBuf::from(dir).join("PocketAssistant").join(name))
}

/// `assistant-YYYY-MM-DD.db.gz` and nothing else: the name arrives from the webview and becomes a file name.
pub fn valid_backup_name(name: &str) -> bool {
    let Some(date) = name.strip_prefix("assistant-").and_then(|rest| rest.strip_suffix(".db.gz")) else { return false };
    date.len() == 10
        && date.bytes().enumerate().all(|(i, c)| if i == 4 || i == 7 { c == b'-' } else { c.is_ascii_digit() })
}

fn backup_names(dir: &Path) -> std::io::Result<Vec<String>> {
    let mut names: Vec<String> = std::fs::read_dir(dir)?
        .filter_map(|entry| entry.ok()?.file_name().into_string().ok())
        .filter(|name| valid_backup_name(name))
        .collect();
    names.sort(); // the date is in the name: oldest first
    Ok(names)
}

/// Keep the `keep` newest backups; other files in the folder are not ours to touch.
pub fn prune_backups(dir: &Path, keep: usize) -> std::io::Result<()> {
    let names = backup_names(dir)?;
    for name in &names[..names.len().saturating_sub(keep)] {
        std::fs::remove_file(dir.join(name))?;
    }
    Ok(())
}

/// The date (YYYY-MM-DD) of the newest backup in `dir`, if any.
pub fn latest_backup_date(dir: &Path) -> Option<String> {
    let name = backup_names(dir).ok()?.pop()?;
    Some(name["assistant-".len()..name.len() - ".db.gz".len()].to_string())
}

const REFUSAL: &str = "Enter your engine's Tailscale name, like my-vm.tail1234.ts.net";

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static NEXT: AtomicUsize = AtomicUsize::new(0);

    /// A fresh folder under the system temp dir (no tempfile crate: no new dependency).
    fn scratch() -> PathBuf {
        let dir = std::env::temp_dir()
            .join(format!("pocket-settings-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::SeqCst)));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_tailnet_name_becomes_https() {
        assert_eq!(base_url(" My-VM.tail1234.ts.net ", false).unwrap(), "https://my-vm.tail1234.ts.net");
    }

    #[test]
    fn other_addresses_are_refused() {
        for a in ["https://x.ts.net", "evil.com", "x.ts.net.evil.com", "127.0.0.1:47615", "", ".ts.net", "a..ts.net", "a b.ts.net"] {
            assert_eq!(base_url(a, false).unwrap_err(), REFUSAL, "{a}");
        }
    }

    #[test]
    fn debug_builds_may_use_a_local_engine() {
        assert_eq!(base_url("127.0.0.1:47615", true).unwrap(), "http://127.0.0.1:47615");
        for a in ["127.0.0.1:80", "127.0.0.1:1023", "127.0.0.1:65536", "127.0.0.1:", "127.0.0.1:+8080", "127.0.0.1:8080/x"] {
            assert!(base_url(a, true).is_err(), "{a}");
        }
    }

    #[test]
    fn the_address_is_saved_and_forgotten() {
        let file = scratch().join("sub").join("app.json");
        assert_eq!(load_address(&file), None);
        save_address(&file, "my-vm.tail1234.ts.net").unwrap();
        assert_eq!(load_address(&file), Some("my-vm.tail1234.ts.net".to_string()));
        forget_address(&file).unwrap();
        assert_eq!(load_address(&file), None);
        assert!(!file.exists());
        forget_address(&file).unwrap(); // forgetting twice is fine
    }

    #[test]
    fn an_old_app_json_has_no_address() {
        let file = scratch().join("app.json");
        std::fs::write(&file, r#"{"python":"C:/p.exe","config":"C:/c.toml"}"#).unwrap();
        assert_eq!(load_address(&file), None);
    }

    #[test]
    fn only_backup_names_are_accepted() {
        assert!(valid_backup_name("assistant-2026-11-27.db.gz"));
        for n in ["../assistant-2026-11-27.db.gz", "x.gz", "assistant-2026-11-27.db", "assistant-2026-11-27.db.gz.tmp", "assistant-2026-1-27.db.gz", ""] {
            assert!(!valid_backup_name(n), "{n}");
        }
    }

    #[test]
    fn seven_backups_are_kept() {
        let dir = scratch();
        for day in 1..=9 {
            std::fs::write(dir.join(format!("assistant-2026-11-{day:02}.db.gz")), b"x").unwrap();
        }
        std::fs::write(dir.join("other.txt"), b"x").unwrap();
        prune_backups(&dir, 7).unwrap();
        let mut left: Vec<String> = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
        left.sort();
        assert_eq!(left.len(), 8);
        assert_eq!(left[0], "assistant-2026-11-03.db.gz");
        assert!(left.contains(&"other.txt".to_string()));
    }

    #[test]
    fn the_newest_backup_date_is_read_from_the_names() {
        let dir = scratch();
        assert_eq!(latest_backup_date(&dir), None);
        for n in ["assistant-2026-11-05.db.gz", "assistant-2026-11-27.db.gz", "other.txt"] {
            std::fs::write(dir.join(n), b"x").unwrap();
        }
        assert_eq!(latest_backup_date(&dir), Some("2026-11-27".to_string()));
    }
}
