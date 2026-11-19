//! Where the app finds the engine: `%APPDATA%\PocketAssistant\app.json` (Python, config, and for development
//! a source folder). Filled once from the app's own place in the repository; edit the file to change it.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AppSettings {
    pub python: PathBuf,
    pub config: PathBuf,
    /// Development only: run the engine from this source folder (PYTHONPATH). The project's .venv is an
    /// editable install of the main checkout, so a worktree build would otherwise run the main checkout's engine.
    #[serde(default)]
    pub pythonpath: Option<PathBuf>,
}

impl AppSettings {
    /// From the exe's place in a checkout: the nearest folder with `.venv\Scripts\python.exe` and `config.toml`
    /// gives python and config. Never a source folder: that comes from `worktree_source` at every start.
    pub fn discover(exe: &Path) -> Option<AppSettings> {
        let root = exe.ancestors().find(|dir| venv_python(dir).is_file() && dir.join("config.toml").is_file())?;
        Some(AppSettings { python: venv_python(root), config: root.join("config.toml"), pythonpath: None })
    }

    /// Reads `file`, or discovers the settings and writes them there. Errors name the file to edit.
    pub fn load_or_create(file: &Path, exe: &Path) -> Result<AppSettings, String> {
        let shown = file.display();
        if file.is_file() {
            let text = std::fs::read_to_string(file).map_err(|e| format!("Can't read {shown}: {e}"))?;
            return serde_json::from_str(&text).map_err(|e| format!("{shown} isn't valid: {e}"));
        }
        let found = AppSettings::discover(exe)
            .ok_or_else(|| format!("Can't find the engine (Python and config.toml): edit {shown}"))?;
        if let Some(dir) = file.parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("Can't create {}: {e}", dir.display()))?;
        }
        let text = serde_json::to_string_pretty(&found).expect("settings serialize");
        std::fs::write(file, text).map_err(|e| format!("Can't write {shown}: {e}"))?;
        Ok(found)
    }
}

/// For an exe built in a git worktree, that worktree's `src` (the nearest folder above the exe with
/// `src\assistant\__init__.py` and no `.venv` of its own). None in the main checkout, which the editable install
/// already points at. Worked out at every start, so it never outlives the build that needed it.
pub fn worktree_source(exe: &Path) -> Option<PathBuf> {
    let tree = exe.ancestors().find(|dir| dir.join("src").join("assistant").join("__init__.py").is_file())?;
    (!venv_python(tree).is_file()).then(|| tree.join("src"))
}

fn venv_python(dir: &Path) -> PathBuf {
    dir.join(".venv").join("Scripts").join("python.exe")
}

/// `%APPDATA%\PocketAssistant\<name>`.
pub fn app_data(name: &str) -> Option<PathBuf> {
    std::env::var_os("APPDATA").map(|dir| PathBuf::from(dir).join("PocketAssistant").join(name))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static NEXT: AtomicUsize = AtomicUsize::new(0);

    /// A fresh folder under the system temp dir (no tempfile crate: no new dependency).
    fn scratch() -> PathBuf {
        let dir = std::env::temp_dir()
            .join(format!("pocket-settings-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::SeqCst)));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn touch(path: &Path) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, b"").unwrap();
    }

    /// A checkout with a venv, a config and the engine's source.
    fn checkout(root: &Path) {
        touch(&root.join(".venv").join("Scripts").join("python.exe"));
        touch(&root.join("config.toml"));
        touch(&root.join("src").join("assistant").join("__init__.py"));
    }

    fn release_exe(tree: &Path) -> PathBuf {
        tree.join("app").join("src-tauri").join("target").join("release").join("pocket-assistant.exe")
    }

    #[test]
    fn discover_finds_the_repo_from_a_release_build() {
        let repo = scratch();
        checkout(&repo);
        let found = AppSettings::discover(&release_exe(&repo)).unwrap();
        assert_eq!(found.python, repo.join(".venv").join("Scripts").join("python.exe"));
        assert_eq!(found.config, repo.join("config.toml"));
        assert_eq!(found.pythonpath, None);
    }

    #[test]
    fn discover_never_saves_a_source_folder() {
        // saved, a worktree's folder would redirect every later build, the main checkout's too
        let repo = scratch();
        checkout(&repo);
        let worktree = repo.join(".worktrees").join("w");
        touch(&worktree.join("src").join("assistant").join("__init__.py"));
        let found = AppSettings::discover(&release_exe(&worktree)).unwrap();
        assert_eq!(found.python, repo.join(".venv").join("Scripts").join("python.exe"));
        assert_eq!(found.pythonpath, None);
    }

    #[test]
    fn worktree_source_is_the_worktree_src_for_a_worktree_build() {
        let repo = scratch();
        checkout(&repo);
        let worktree = repo.join(".worktrees").join("w");
        touch(&worktree.join("src").join("assistant").join("__init__.py"));
        assert_eq!(worktree_source(&release_exe(&worktree)), Some(worktree.join("src")));
    }

    #[test]
    fn worktree_source_is_none_for_the_main_checkout() {
        let repo = scratch();
        checkout(&repo);
        assert_eq!(worktree_source(&release_exe(&repo)), None);
    }

    #[test]
    fn discover_returns_none_without_a_venv() {
        let repo = scratch();
        touch(&repo.join("config.toml"));
        touch(&repo.join("src").join("assistant").join("__init__.py"));
        assert_eq!(AppSettings::discover(&release_exe(&repo)), None);
    }

    #[test]
    fn load_or_create_writes_the_file_once() {
        let repo = scratch();
        checkout(&repo);
        let file = repo.join("appdata").join("app.json");
        let first = AppSettings::load_or_create(&file, &release_exe(&repo)).unwrap();
        assert!(file.is_file());
        // an edited file wins over discovery
        let edited = AppSettings { python: PathBuf::from("C:/other/python.exe"), ..first.clone() };
        fs::write(&file, serde_json::to_string(&edited).unwrap()).unwrap();
        assert_eq!(AppSettings::load_or_create(&file, &release_exe(&repo)).unwrap(), edited);
    }

    #[test]
    fn load_or_create_names_the_file_when_nothing_is_found() {
        let empty = scratch();
        let file = empty.join("app.json");
        let error = AppSettings::load_or_create(&file, &release_exe(&empty)).unwrap_err();
        assert!(error.contains(&file.display().to_string()), "{error}");
    }
}
