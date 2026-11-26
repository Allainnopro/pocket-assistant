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
}
