# M5: Cloud Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The unchanged engine runs 24/7 on a free Google Cloud e2-micro, and the desktop app on the PC and the laptop becomes a viewer of it over Tailscale.

**Architecture:**
- **Engine:**
  - On Linux, a small `keyring` backend stores secrets in a `0600` file next to the database. The CLI installs it at start, so `vault.py` and `gmail_imap.py` keep calling `keyring` unchanged.
  - The app handshake goes (`--parent-pid`, `--password-stdin`); `--dev` registers no mail check and no extraction.
  - New: a nightly backup job, a keeper-only backup download, and a profile text editor route.
- **The Rust shell** loses its supervisor and keeps a saved connection: an address in `app.json` and a device key in Credential Manager.
- **The dashboard:**
  - signs in to that connection;
  - keeps loaded data with a banner when the VM is unreachable;
  - edits `profile.toml`;
  - on the keeper device, downloads backups.
- **`deploy/`** holds the systemd unit and set-up scripts.

**Tech Stack:** Python 3.12 (engine, FastAPI, sqlite3), Rust + Tauri 2.12 (shell), React 19 + Vite + Vitest (dashboard), systemd + Tailscale on Ubuntu 24.04 LTS.

**Spec:** `docs/superpowers/specs/2026-11-26-m5-cloud-engine-design.md`

## Global Constraints

- VM: e2-micro, `us-west1`, Ubuntu 24.04 LTS, 30 GB standard persistent disk.
- Code at `/opt/pocket-assistant` (root-owned). Data at `/var/lib/pocket-assistant/` (user `pocket`, mode `0700`).
- Secrets file: `<folder of db_path>/secrets.json`.
  - JSON shape `{ "<service>": { "<user>": "<secret>" } }`, mode `0600`.
  - Written atomically: a temporary file in the same folder, then `os.replace`.
  - Refused if group or other permission bits are set; the message names `chmod 600 <path>` and never contains a secret.
  - Windows keeps Credential Manager unchanged.
- systemd unit: `User=pocket`, `Restart=on-failure`, `KillSignal=SIGINT`, `TimeoutStopSec=150`, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/pocket-assistant`, `PrivateTmp=yes`, `ProtectHome=yes`.
- Backups:
  - `<folder of db_path>/backups/assistant-YYYY-MM-DD.db.gz`;
  - made once per HCMC day, at the first engine check at or after 03:00 `Asia/Ho_Chi_Minh`;
  - newest 7 kept.
- `GET /api/backup/latest`:
  - 200 `application/gzip`, with headers `X-Backup-Date` and `X-Backup-SHA256` (lower-case hex), both listed in CORS `expose_headers`;
  - 403 unless the caller is device id `[backup] keeper_device`;
  - 404 when no backup exists.
- The PC keeps 7 backups in `%LOCALAPPDATA%\PocketAssistant\backups\`.
- `GET /api/profile/text` → `{ "text": str }`.
- `PUT /api/profile/text` with `{ "text": str }`:
  - text over 65,536 UTF-8 bytes → 413;
  - text that fails to parse → 400 with the parser's message, and the file unchanged;
  - success → 200 with the saved text, plus a `profile_changed` event;
  - any paired device may call it;
  - CORS allows `PUT`; the body limit for that path is 140,000 bytes (other paths keep `MAX_BODY = 4096`).
- Viewer addresses:
  - release builds accept only `^[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net$` (after trim and lower-case), as `https://<address>`;
  - debug builds also accept `127.0.0.1:<1024–65535>`, as `http://…`.
- App secrets: Rust `keyring` crate, service `pocket-assistant-app`, user `device-key`. The laptop's M2 key is looked up best effort under the target `pocket-assistant-device-key`. `app.json` = `{ "address": "<address>" }`.
- `assistant engine --dev` registers neither `check_mail` nor `extract` (nor `backup`), and builds no `VaultLLM`, even with secrets saved.
- New dependencies:
  - only the Rust `keyring` crate, exact pin, version at least 14 days old, checked on crates.io at the time, with Windows native support;
  - `Cargo.lock` committed;
  - no new Python or npm packages.
- Rust shells need `export PATH="$USERPROFILE/.cargo/bin:$PATH"`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Full checks before each commit that touches them:
  - Python: `pytest -q -p no:cacheprovider`;
  - dashboard: `cd app && npm run verify`;
  - Rust: `cd app/src-tauri && cargo test`.

## Review Focus

1. **A secrets file made readable by mistake** (e.g. `sudo` without `-u pocket`, or a copied file with `0644`): the engine and `set-key` refuse with the `chmod 600` line, never start with exposed secrets, and never print one. Covered by Task 1, "a file others can read is refused with the fix".
2. **A backup that fails halfway** (disk full, database busy): the old backups stay, no partial or temporary file is left, the engine keeps running, and the dashboard says the backup failed. Covered by Task 3, "a failed backup keeps the old ones and says so".
3. **A corrupted or cut-off backup download:** nothing is saved, the PC's older copies stay, and the next daily try starts over. Covered by Task 10, "a hash mismatch saves nothing".
4. **Connect against the wrong address, or with Tailscale off:** the screen says it can't reach the engine, which is different from a wrong code. Nothing is saved. Covered by Task 7, "an unreachable address says so".
5. **Clicking an action while the VM is unreachable:** the button is disabled and no request is sent, so nothing fails silently. Covered by Task 8, "actions are disabled offline and send no request".

---

### Task 1: Secrets in a file on Linux

**Files:**
- Create: `src/assistant/file_keyring.py`, `tests/test_file_keyring.py`
- Modify: `src/assistant/cli.py` (`_run`), `src/assistant/vault.py` (module docstring: Windows Credential Manager, or `secrets.json` elsewhere), `.github/workflows/tests.yml`

**Interfaces:**
- Produces: `class FileKeyring(keyring.backend.KeyringBackend)` with `priority = 1`, `__init__(self, path: Path)`, `get_password(service, username) -> str | None`, `set_password(service, username, password) -> None`, `delete_password(service, username) -> None` (raises `keyring.errors.PasswordDeleteError` when absent); `install_for(cfg: Config) -> None` (no-op when `sys.platform == "win32"`, else `keyring.set_keyring(FileKeyring(cfg.db_path.parent / "secrets.json"))`). Unsafe permissions raise `keyring.errors.KeyringError`.

- [ ] **Step 1: Write the failing tests** (permission tests `@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")`; a fixture restores `keyring.get_keyring()` after each test; do not use the `vault` fixture here)

```python
def test_round_trip_and_the_file_is_private(tmp_path):
    ring = FileKeyring(tmp_path / "secrets.json")
    ring.set_password("pocket-assistant", "gmail-app-password", "abcdabcdabcdabcd")
    assert ring.get_password("pocket-assistant", "gmail-app-password") == "abcdabcdabcdabcd"
    assert (tmp_path / "secrets.json").stat().st_mode & 0o777 == 0o600

def test_a_file_others_can_read_is_refused_with_the_fix(tmp_path):
    path = tmp_path / "secrets.json"
    FileKeyring(path).set_password("s", "u", "the-secret")
    path.chmod(0o644)
    with pytest.raises(keyring.errors.KeyringError) as refused:
        FileKeyring(path).get_password("s", "u")
    assert f"chmod 600 {path}" in str(refused.value) and "the-secret" not in str(refused.value)

def test_a_failed_write_leaves_the_old_file_and_no_temp(tmp_path, monkeypatch): ...  # os.replace raises -> old value readable, folder holds only secrets.json
def test_a_missing_file_reads_as_none(tmp_path): ...
def test_delete_removes_only_that_entry_and_a_missing_one_raises(tmp_path): ...
def test_install_is_a_no_op_on_windows_and_installs_elsewhere(tmp_path, monkeypatch): ...  # monkeypatch sys.platform
def test_set_key_on_linux_saves_into_the_file(tmp_path, monkeypatch): ...  # main(["--config", cfg, "set-key", "anthropic", "--stdin"]) with stdin = FAKE_KEY; secrets.json holds it
```

- [ ] **Step 2: Run them to verify they fail.** Run: `pytest tests/test_file_keyring.py -v`. Expected: FAIL with `ModuleNotFoundError: assistant.file_keyring`.
- [ ] **Step 3: Implement `file_keyring.py`.** Every read checks `st_mode & 0o077 == 0`. Writes go through `tempfile.mkstemp(dir=folder)`, `os.fchmod(fd, 0o600)` and `os.replace`, and the temporary file is removed on failure. In `cli._run`, call `install_for(cfg)` right after `load_config`.
- [ ] **Step 4: Run them to verify they pass.** Run: `pytest tests/test_file_keyring.py -v`. Expected: PASS. Permission tests are skipped on Windows.
- [ ] **Step 5: CI on both systems.** In `tests.yml`, use `strategy.matrix.os: [windows-latest, ubuntu-latest]` with `runs-on: ${{ matrix.os }}`, and replace the "Windows-only" comment. Run: `pytest -q -p no:cacheprovider`. Expected: all pass.
- [ ] **Step 6: Commit.**

```bash
git add src/assistant/file_keyring.py src/assistant/cli.py src/assistant/vault.py tests/test_file_keyring.py .github/workflows/tests.yml
git commit -m "Keep secrets in a private file on Linux; run the tests on Ubuntu too"
```

### Task 2: The engine drops the app handshake and gains the dev guard

**Files:**
- Delete: `src/assistant/engine/parent.py`, `tests/test_parent.py`
- Modify: `src/assistant/engine/runtime.py`, `src/assistant/cli.py` (engine options), `tests/test_engine_runtime.py`

**Interfaces:**
- Produces:
  - `run_engine(cfg: Config, dev: bool = False) -> None`. The `parent_pid`, `password_stdin` and `stdin` parameters are removed, along with `READY_LINE`, `_stop_on_end_of_stdin`, `_stop_when_parent_is_gone` and `PARENT_CHECK_SECONDS`.
  - `Engine.__init__(..., llm: LLM | None, ..., dev: bool = False)`. With `dev`, it registers only `sweep` and `profile_watch`.
  - In dev mode, `run_engine` passes `llm=None` and never constructs `VaultLLM`.

- [ ] **Step 1: Write the failing tests**

```python
def test_dev_engine_registers_no_mail_check_and_no_extraction(tmp_path, vault): ...
    # Engine(cfg, dev=True, llm=None, gmail=never_called, ...) -> list(engine.scheduler._jobs) == ["sweep", "profile_watch"]
def test_dev_engine_never_opens_gmail_even_with_a_saved_password(tmp_path, vault): ...
    # vault holds a gmail password; engine.scheduler.tick() with a FakeClock -> gmail callable never called
def test_the_engine_command_has_no_handshake_options():
    with pytest.raises(SystemExit) as bad:
        main(["engine", "--parent-pid", "1"])
    assert bad.value.code == 2
```

- [ ] **Step 2: Run them to verify they fail.** Run: `pytest tests/test_engine_runtime.py -k "dev_engine or handshake" -v`. Expected: FAIL. `Engine` takes no `dev` argument, and `--parent-pid` is still accepted.
- [ ] **Step 3: Implement.** Remove the handshake code, its CLI options and the tests that exercised it (`READY`, stdin, parent watch). Add the `dev` branch to `Engine.__init__` and `run_engine`. The non-dev engine keeps its jobs (Task 3 adds `backup`).
- [ ] **Step 4: Run the full suite.** Run: `pytest -q -p no:cacheprovider`. Expected: all pass, and nothing imports `assistant.engine.parent`.
- [ ] **Step 5: Commit.**

```bash
git add -A src/assistant tests
git commit -m "Drop the desktop app's engine handshake; --dev never checks mail or calls the model"
```

### Task 3: Nightly backups

**Files:**
- Create: `src/assistant/engine/backup.py`, `tests/test_backup.py`
- Modify: `src/assistant/config.py` (`BackupConfig`, `BACKUP_KEYS = {"keeper_device"}`, `Config.backup`), `src/assistant/engine/runtime.py` (register), `config.example.toml` (a commented `[backup] keeper_device` line), `app/src/lib/status.ts` (problem `backup_failed` → "Last night's backup failed: {detail}") and its test

**Interfaces:**
- Produces:
  - `BACKUP_AT = time(3, 0)` and `KEEP = 7`;
  - `backup_folder(cfg: Config) -> Path`, which is `cfg.db_path.parent / "backups"`;
  - `make_backup(db_path: Path, folder: Path, day: date) -> Path`;
  - `prune(folder: Path, keep: int = KEEP) -> list[Path]`, which removes only files named `assistant-YYYY-MM-DD.db.gz`;
  - `latest(folder: Path) -> tuple[date, Path] | None`;
  - `class BackupJob`, with `__init__(self, db_path: Path, folder: Path, bus: EventBus, log: Log)` and `__call__(self, now: datetime) -> None`;
  - `BackupConfig(keeper_device: int | None = None)`;
  - the non-dev engine registers `("backup", timedelta(hours=1), BackupJob(...))`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_backup_is_a_readable_copy(tmp_path): ...  # gunzip make_backup's file -> sqlite3 opens it; row counts match the source
def test_it_waits_for_three_in_the_morning_in_hcmc(tmp_path):
    job = BackupJob(db, folder, EventBus(), print)
    job(datetime(2026, 11, 26, 19, 59, tzinfo=timezone.utc))   # 02:59 HCMC
    assert list(folder.glob("*.gz")) == []
    job(datetime(2026, 11, 26, 20, 0, tzinfo=timezone.utc))    # 03:00 HCMC
    job(datetime(2026, 11, 26, 21, 0, tzinfo=timezone.utc))
    assert [p.name for p in folder.glob("*.gz")] == ["assistant-2026-11-27.db.gz"]
def test_seven_are_kept_and_other_files_left_alone(tmp_path): ...  # 9 daily files + notes.txt -> 7 newest + notes.txt
def test_a_failed_backup_keeps_the_old_ones_and_says_so(tmp_path, monkeypatch): ...
    # gzip write raises OSError -> old files intact, no temp files, bus problem ("backup", "backup_failed"), no exception escapes
def test_backup_keeper_device_config(tmp_path): ...  # 2 -> 2; "two", 0, true -> ConfigError naming [backup] keeper_device
```

- [ ] **Step 2: Run them to verify they fail.** Run: `pytest tests/test_backup.py -v`. Expected: FAIL with `ModuleNotFoundError`.
- [ ] **Step 3: Implement.**
  - `make_backup` uses `sqlite3.connect(db_path).backup(dest)` into a temporary database in `folder`, gzips it to a temporary `.gz`, then `os.replace`s it to the final name. Temporary files are removed in `finally`.
  - `BackupJob` catches `OSError` and `sqlite3.Error`, logs the type name only, and calls `bus.report("backup", "backup_failed", detail=<type name>)`. A success reports `None`.
  - In `status.ts`, add the copy, with one Vitest case.
- [ ] **Step 4: Run them to verify they pass.** Run: `pytest -q -p no:cacheprovider`, then `cd app && npx vitest run src/lib`. Expected: PASS.
- [ ] **Step 5: Commit.**

```bash
git add src/assistant/engine/backup.py src/assistant/engine/runtime.py src/assistant/config.py config.example.toml tests/test_backup.py app/src/lib/status.ts app/src/lib/*.test.ts
git commit -m "Back up the database every night at 03:00 HCMC, keeping 7"
```

### Task 4: Backups and the profile over the API

**Files:**
- Create: `src/assistant/engine/api_admin.py`, `tests/test_api_admin.py`
- Modify:
  - `src/assistant/engine/api.py`: include the router; CORS `allow_methods` gains `"PUT"` and `expose_headers=["X-Backup-Date", "X-Backup-SHA256"]`; `BodyLimit` allows 140,000 bytes for `/api/profile/text`;
  - `src/assistant/engine/events.py`: `PROFILE_CHANGED = "profile_changed"`;
  - regenerated `app/src/api/openapi.json` and `app/src/api/schema.d.ts`.

**Interfaces:**
- Consumes: `latest()` and `backup_folder()` (Task 3), `cfg.backup.keeper_device`, `load_profile(path)`, `Authorized` / `Caller(kind, device_id)`.
- Produces:
  - `class ProfileText(BaseModel): text: str`;
  - routes and statuses as listed in Global Constraints;
  - the PUT writes `profile.toml.new` next to the profile, validates it with `load_profile`, then `os.replace`s it. The 30-second watcher re-ranks.

- [ ] **Step 1: Write the failing tests** (with `tests/api_helpers.Api`)

```python
def test_only_the_keeper_gets_the_backup(tmp_path): ...
    # [backup] keeper_device = <paired id>; keeper -> 200, body == file bytes, X-Backup-SHA256 == hashlib.sha256(bytes).hexdigest(),
    # X-Backup-Date == "2026-11-27"; another device -> 403; the launch password -> 403
def test_no_backup_yet_is_404(tmp_path): ...
def test_backup_headers_are_readable_from_the_app(tmp_path): ...  # Origin http://tauri.localhost -> Access-Control-Expose-Headers has both
def test_profile_round_trip_sends_profile_changed(tmp_path): ...  # PUT valid text -> 200, file == text, bus saw "profile_changed"; GET returns it
def test_a_broken_profile_is_refused_and_the_old_one_kept(tmp_path): ...  # "interests = [" -> 400 with the parser's message; file unchanged; no profile.toml.new
def test_a_profile_over_64_kb_is_refused(tmp_path): ...  # 65_537 bytes -> 413
def test_put_passes_the_cors_preflight(tmp_path): ...  # OPTIONS, Access-Control-Request-Method: PUT, Origin tauri -> allowed
```

- [ ] **Step 2: Run them to verify they fail.** Run: `pytest tests/test_api_admin.py -v`. Expected: FAIL with 404s or `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `api_admin.py`: a `router` with the three routes; the backup is a `FileResponse` with the two headers.
- [ ] **Step 4: Run the tests and regenerate the contract.** Run: `pytest -q -p no:cacheprovider`, then `assistant api-schema`, then `cd app && npm run gen:api && npm run verify`. Expected: all pass, and `schema.d.ts` lists `/api/backup/latest` and `/api/profile/text`.
- [ ] **Step 5: Commit.**

```bash
git add src/assistant/engine tests/test_api_admin.py app/src/api/openapi.json app/src/api/schema.d.ts
git commit -m "Serve the newest backup to the keeper device and edit profile.toml over the API"
```

### Task 5: Deploy files for the VM

**Files:**
- Create: `deploy/pocket-assistant.service`, `deploy/setup.sh`, `deploy/update.sh`, `deploy/README.md`
- Modify: `.gitattributes` (`*.sh text eol=lf` and `*.service text eol=lf`), `config.example.toml` (comment showing the VM's absolute `db_path` and `[paths] profile`)

**Interfaces:**
- Produces:
  - **The unit:** the Global Constraints values, plus
    `ExecStart=/opt/pocket-assistant/.venv/bin/assistant --config /var/lib/pocket-assistant/config.toml engine`.
  - **`setup.sh`** (root, `set -euo pipefail`, safe to re-run):
    - installs `git`, `python3-venv` and `sqlite3`;
    - creates the system user `pocket` (no login shell, home `/var/lib/pocket-assistant`);
    - creates the data folder (`0700`, `pocket:pocket`) and a 1 GB `/swapfile` if none exists (plus `/etc/fstab`);
    - clones `https://github.com/Allainnopro/pocket-assistant.git` to `/opt/pocket-assistant`, or pulls it if present;
    - creates `.venv` and runs `pip install -e .`;
    - installs and enables the unit. It **does not start** it.
  - **`update.sh`:** `git pull --ff-only`, then `pip install -e .`, then `systemctl restart pocket-assistant`.
  - **`README.md`** covers spec section 8.7 steps 1–3 and 6–7:
    - the console choices;
    - the $1 budget alert;
    - the Tailscale install (from Tailscale's current Linux docs);
    - `tailscale up --ssh` and `tailscale serve --bg 47615`;
    - deleting the default `default-allow-ssh`, `default-allow-rdp` and `default-allow-icmp` rules, and the temporary-rule recovery;
    - the `sudo -u pocket /opt/pocket-assistant/.venv/bin/assistant --config /var/lib/pocket-assistant/config.toml set-key gmail` line, and the same for `anthropic` and `pair`;
    - restoring a backup;
    - updating;
    - checking the billing report after 48 h;
    - that Google may send a security alert at the first Gmail sign-in from the VM, and the owner confirms it.

- [ ] **Step 1: Write the four files.**
- [ ] **Step 2: Check the shell syntax.** Run: `bash -n deploy/setup.sh && bash -n deploy/update.sh && echo ok`. Expected: `ok`.
- [ ] **Step 3: Check the line endings.** Run: `git add --renormalize deploy .gitattributes && git ls-files --eol deploy`. Expected: every file shows `i/lf`.
- [ ] **Step 4: Commit.** systemd-specific checks happen on the VM in Task 12.

```bash
git add deploy .gitattributes config.example.toml
git commit -m "Add the systemd unit and set-up scripts for the cloud VM"
```

### Task 6: The desktop app becomes a viewer (Rust)

**Files:**
- Delete: `app/src-tauri/src/engine.rs`, `app/src-tauri/src/backoff.rs`, `app/src-tauri/tests/supervisor.rs`, `app/src-tauri/tests/fake_engine.py`
- Rewrite: `app/src-tauri/src/settings.rs` (the connection store)
- Modify: `app/src-tauri/src/lib.rs`, `app/src-tauri/Cargo.toml`, `app/src-tauri/Cargo.lock`

**Interfaces:**
- Produces:
  - **Pure functions in `settings.rs`:**
    - `base_url(address: &str, allow_local: bool) -> Result<String, String>`. The refusal text is "Enter your engine's Tailscale name, like my-vm.tail1234.ts.net".
    - `load_address(file: &Path) -> Option<String>`, `save_address(file: &Path, address: &str) -> Result<(), String>`, `forget_address(file: &Path) -> Result<(), String>`.
  - **Tauri commands** (JS calls them with camelCase args):
    - `load_connection() -> Option<Connection>`, where `Connection { base_url, key }` serializes as `{ baseUrl, key }`;
    - `check_address(address) -> Result<String, String>`;
    - `legacy_key() -> Option<String>`;
    - `save_connection(address, key) -> Result<(), String>`;
    - `forget_connection() -> Result<(), String>`;
    - `report_connection(online: bool, since: Option<String>)`.
    - `allow_local` is `cfg!(debug_assertions)`.
    - `engine_connection` and `open_profile` are removed.
  - **Tray:**
    - tooltip *Connected to {address}*, *Can't reach {address} since {since}*, or *Not connected*;
    - menu *Open dashboard*, *Start with Windows*, *Forget this engine*, *Quit*;
    - *Forget* runs `forget_connection`, emits `connection-forgotten` to the `dashboard` window, and shows it.

- [ ] **Step 1: Write the failing tests** (`#[cfg(test)]` in `settings.rs`)

```rust
#[test] fn a_tailnet_name_becomes_https() { assert_eq!(base_url(" My-VM.tail1234.ts.net ", false).unwrap(), "https://my-vm.tail1234.ts.net"); }
#[test] fn other_addresses_are_refused() { for a in ["https://x.ts.net", "evil.com", "x.ts.net.evil.com", "127.0.0.1:47615", ""] { assert!(base_url(a, false).is_err()); } }
#[test] fn debug_builds_may_use_a_local_engine() { assert_eq!(base_url("127.0.0.1:47615", true).unwrap(), "http://127.0.0.1:47615"); assert!(base_url("127.0.0.1:80", true).is_err()); }
#[test] fn the_address_is_saved_and_forgotten() { /* temp dir: save -> load == Some; forget -> load == None, file gone */ }
```

- [ ] **Step 2: Run them to verify they fail.** Run: `cd app/src-tauri && cargo test settings`. Expected: compile errors, since the functions don't exist yet.
- [ ] **Step 3: Implement.**
  - Add the `keyring` crate with an exact pin; record the version in the commit message.
  - Remove the supervisor and its wiring.
  - Implement the commands and the tray.
  - The key entry is service `pocket-assistant-app`, user `device-key`. `legacy_key` tries the target `pocket-assistant-device-key` and returns `None` on any error.
- [ ] **Step 4: Run tests and build.** Run: `cargo test && cd .. && npx tauri build --no-bundle`. Expected: tests pass, and the build succeeds with no reference to `engine` or `backoff`.
- [ ] **Step 5: Commit.**

```bash
git add -A app/src-tauri
git commit -m "Desktop app: connect to the engine instead of running it"
```

### Task 7: The dashboard connects with a device key

**Files:**
- Create: `app/src/views/Connect.tsx`, `app/src/views/Connect.test.tsx`
- Modify:
  - `app/src/lib/tauri.ts`: `Connection = { baseUrl: string; key: string }`;
  - `app/src/api/client.ts`: `makeClient` takes `baseUrl`;
  - `app/src/api/auth.tsx`, `app/src/api/useLiveEvents.ts`, `app/src/views/Login.tsx`;
  - `app/src/test/tauri.ts` (the fake invoke), and the existing tests these changes break.

**Interfaces:**
- Consumes: the Task 6 commands.
- Produces:
  - `makeClient(opts: { baseUrl: string; fetch?; getPassword; onUnauthorized })`;
  - `AuthValue.baseUrl: string`;
  - `signIn(candidate: string, baseUrl?: string): Promise<SignIn>`, where `baseUrl` defaults to `API_BASE`;
  - `useLiveEvents` connects to `${baseUrl}/api/events`.
- **The Connect flow:**
  1. Address field → `check_address`.
  2. `legacy_key` → if it signs in, `save_connection(address, legacy)`.
  3. Otherwise show the fields "Pairing code" and "Name for this computer" (required, at most 200 characters), then `POST {baseUrl}/api/pair` → `save_connection(address, key)` → `signIn(key, baseUrl)`.
- **In Tauri mode,** `AppSignIn`:
  - calls `load_connection()`;
  - with `null`, renders `<Connect/>`;
  - with a 401, calls `forget_connection()` and renders `<Connect notice="This computer was disconnected. Pair it again." />`;
  - reloads on a `connection-forgotten` event.
- Browser mode is unchanged.

- [ ] **Step 1: Write the failing tests**

```tsx
test("connects with a pairing code", ...)            // POST /api/pair body {code:"123456", device_name:"laptop"}; save_connection called with the returned key; dashboard shows
test("uses the laptop's old key without a code", ...)  // legacy_key -> "k"; /api/settings 200 -> save_connection(address, "k"); no code field shown
test("an unreachable address says so", ...)          // fetch rejects -> "Can't reach my-vm.tail1234.ts.net. Is Tailscale on, on this computer?"; save_connection not called
test("a refused code shows the engine's reason and keeps the form", ...)  // 400 {detail:"That code has expired."} -> text shown, inputs kept
test("a revoked key goes back to Connect and forgets it", ...)  // load_connection ok, /api/settings 401 -> forget_connection called, notice shown
```

- [ ] **Step 2: Run them to verify they fail.** Run: `cd app && npx vitest run src/views/Connect.test.tsx`. Expected: FAIL, because `Connect` doesn't exist.
- [ ] **Step 3: Implement** the client, auth and Login changes, plus `Connect.tsx`, reusing the existing `ui/input` and `ui/button`.
- [ ] **Step 4: Run all front-end checks.** Run: `npm run verify`. Expected: all pass, including the existing browser Login tests.
- [ ] **Step 5: Commit.**

```bash
git add -A app/src
git commit -m "Dashboard: pair with the cloud engine and sign in with the device key"
```

### Task 8: Offline banner, disabled actions, tray status

**Files:**
- Create: `app/src/api/online.tsx` (`OnlineProvider`, `useOnline(): boolean`), `app/src/components/OfflineBanner.tsx`
- Modify: `app/src/App.tsx` (`EngineGate`), `app/src/api/useAct.ts`, the components rendering star / dismiss / applied buttons (`OpportunityItem.tsx`, `OpportunityPanel.tsx`, `TopPick.tsx`), `app/src/views/Unreachable.tsx`, `app/src/components/Shell.test.tsx` or a new `App.test.tsx`

**Interfaces:**
- Consumes: `report_connection` (Task 6), `baseUrl` (Task 7).
- Produces:
  - `EngineGate` remembers whether health ever succeeded.
  - **After a success,** an error keeps `children` and shows `<OfflineBanner since={HH:MM HCMC} />`, with the text "Engine unreachable since {HH:MM} · retrying".
  - **With no success yet,** it shows `<Unreachable/>`. In the app, that reads "Can't reach the engine at {address}. Retrying…".
  - **Retry interval:** 30,000 ms in Tauri, 3,000 ms in the browser.
  - `useOnline()` is `false` while unreachable. `useAct` does nothing then, and the action buttons are `disabled`.
  - Every online/offline change in Tauri calls `invoke("report_connection", { online, since })`.

- [ ] **Step 1: Write the failing tests**

```tsx
test("data stays and a banner shows when the engine goes away", ...)
test("actions are disabled offline and send no request", ...)   // star button disabled; no fetch to /api/opportunities/*/star
test("back online clears the banner and re-enables actions", ...)
test("never loaded shows the unreachable screen", ...)
test("the tray is told the state", ...)                         // invoke("report_connection", {online:false, since:"10:42"})
```

- [ ] **Step 2: Run them to verify they fail.** Run: `npx vitest run src/App.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the checks.** Run: `npm run verify`. Expected: all pass.
- [ ] **Step 5: Commit.**

```bash
git add -A app/src
git commit -m "Keep the last data with a banner while the engine is unreachable; disable actions"
```

### Task 9: Edit the profile in Settings

**Files:**
- Modify: `app/src/views/Settings.tsx`, `app/src/views/Settings.test.tsx`, `app/src/api/keys.ts` (`keys.profileText`), `app/src/api/useLiveEvents.ts` (`profile_changed` → invalidate `keys.profileText`)

**Interfaces:**
- Consumes: `GET` / `PUT /api/profile/text` (Task 4), `useOnline()` (Task 8).
- Produces:
  - "Open in editor" is replaced by a monospace `<textarea>` labelled "profile.toml" and a *Save* button. *Save* is disabled while unchanged, while saving, or while offline.
  - A 400 or 413 shows the engine's `detail` inline and keeps the edits.
  - Success shows the toast "Saved. Rankings update within 30 seconds."
  - A `profile_changed` from another device refreshes the text only when there are no unsaved edits.

- [ ] **Step 1: Write the failing tests**

```tsx
test("shows profile.toml and saves an edit", ...)          // PUT body {text}; toast text exact
test("a broken profile shows the engine's message and keeps the edits", ...)
test("save is disabled until something changes", ...)
test("another device's save refreshes the text unless I'm editing", ...)
```

- [ ] **Step 2: Run them to verify they fail.** Run: `npx vitest run src/views/Settings.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement**, and remove the `open_profile` invoke and its `isTauri()` branch.
- [ ] **Step 4: Run the checks.** Run: `npm run verify`. Expected: all pass.
- [ ] **Step 5: Commit.**

```bash
git add -A app/src
git commit -m "Edit profile.toml from Settings on any paired computer"
```

### Task 10: The PC keeps the backups

**Files:**
- Create: `app/src/api/backups.ts`, `app/src/api/backups.test.ts`
- Modify: `app/src-tauri/src/settings.rs` (pure functions), `app/src-tauri/src/lib.rs` (commands), `app/src/App.tsx` (`useBackupKeeper()` inside `Live`, Tauri only)

**Interfaces:**
- Consumes: `GET /api/backup/latest` (Task 4), `baseUrl` and key (Task 7).
- Produces:
  - **The sync function:**
    `syncBackup(deps: { fetch: typeof fetch; baseUrl: string; key: string; invoke: typeof invoke }): Promise<"saved" | "current" | "not_keeper" | "failed">`.
    It verifies `X-Backup-SHA256` with `crypto.subtle.digest("SHA-256", bytes)`.
  - **`useBackupKeeper()`** runs it after the first successful health check, then every 24 h, and stops for the session after `"not_keeper"`.
  - **Rust commands:**
    - `latest_backup_date() -> Option<String>`;
    - `save_backup(request: tauri::ipc::Request) -> Result<(), String>`. Raw bytes; the file name comes in header `x-backup-name`. It writes a temporary file, renames it, then prunes to 7.
  - **Pure Rust functions:** `valid_backup_name(name: &str) -> bool` (`^assistant-\d{4}-\d{2}-\d{2}\.db\.gz$`) and `prune_backups(dir: &Path, keep: usize) -> std::io::Result<()>`.

- [ ] **Step 1: Write the failing tests**

```ts
test("saves a newer backup", ...)          // 200 + matching SHA -> invoke("save_backup", bytes, {headers:{"x-backup-name":"assistant-2026-11-27.db.gz"}}) -> "saved"
test("skips when the PC already has it", ...)  // latest_backup_date "2026-11-27" -> "current", no save
test("a hash mismatch saves nothing", ...)  // -> "failed", save_backup not called
test("a 403 means this computer isn't the keeper", ...)  // -> "not_keeper"
```
```rust
#[test] fn only_backup_names_are_accepted() { assert!(valid_backup_name("assistant-2026-11-27.db.gz")); for n in ["../assistant-2026-11-27.db.gz", "x.gz", "assistant-2026-11-27.db"] { assert!(!valid_backup_name(n)); } }
#[test] fn seven_backups_are_kept() { /* 9 files + other.txt -> 7 newest + other.txt */ }
```

- [ ] **Step 2: Run them to verify they fail.** Run: `npx vitest run src/api/backups.test.ts`, then `cd src-tauri && cargo test backup`. Expected: FAIL.
- [ ] **Step 3: Implement**, after checking `tauri::ipc::Request` and raw invoke bodies against the current Tauri 2 docs.
- [ ] **Step 4: Run all checks.** Run: `npm run verify`, then `cargo test`. Expected: all pass.
- [ ] **Step 5: Commit.**

```bash
git add -A app/src app/src-tauri/src
git commit -m "The keeper PC downloads and verifies the nightly backup"
```

### Task 11: Docs, roadmap and the pairing text

**Files:**
- Modify:
  - `README.md`: a new "Running on a cloud VM" section that points to `deploy/README.md`; "The desktop app" rewritten for viewer mode; "Development after the move" (restore the newest backup into a dev folder with its own `config.toml`, then `assistant engine --dev`, `tauri dev`, connecting to `127.0.0.1`); supervisor text removed;
  - `CLAUDE.md`: the "What this is" line (engine on a cloud VM, computers are viewers); the `app/src-tauri` layout line ("tray, connects to the engine"); the secrets rule (Credential Manager on the computers, a `0600` `secrets.json` on the VM); the roadmap replaced by M1–M4 done plus M5–M10 from the spec; the budget line ("LLM soft cap $10/month, hard $15");
  - `src/assistant/cli.py` (`pair`): "this PC" becomes "the engine", and its test in `tests/test_cli.py` or `tests/test_devices.py`;
  - `docs/superpowers/specs/2026-10-25-desktop-app-redesign-design.md` and `2026-11-18-m4-desktop-app-design.md`: a one-line note under each title, "A6 and the PC-as-main design are superseded by 2026-11-26-m5-cloud-engine-design.md".

- [ ] **Step 1: Update the pairing-text test, then run it to see it fail.** Run: `pytest tests -k pair -v`. Expected: FAIL on the old wording.
- [ ] **Step 2: Change the wording and edit the docs.**
- [ ] **Step 3: Run the full suite.** Run: `pytest -q -p no:cacheprovider`. Expected: all pass.
- [ ] **Step 4: Commit.**

```bash
git add README.md CLAUDE.md src/assistant/cli.py tests docs/superpowers/specs
git commit -m "Document the cloud engine, viewer app and M5-M10 roadmap"
```

### Task 12: The move and the live check (owner and agent together)

**Files:**
- Modify: `docs/superpowers/specs/2026-11-26-m5-cloud-engine-design.md` (a "Live check (date)" section, like M1 and M2)

- [ ] **Step 1: Owner, steps 1–3 of spec section 8.7.** Create the VM, set up Tailscale (`up --ssh`, `serve`), delete the firewall rules, run `deploy/setup.sh`.
  - Check: `systemd-analyze verify /etc/systemd/system/pocket-assistant.service` prints nothing.
  - Check: `gcloud compute firewall-rules list` (or the console) shows no rule allowing `0.0.0.0/0`.
- [ ] **Step 2: Owner, steps 4–7.** Stop the PC engine, copy the three files, edit the config, run `set-key gmail` and `set-key anthropic`, start the service.
  - Check: `sudo -u pocket … assistant … auth` reports success.
  - Check: `stat -c %a /var/lib/pocket-assistant/secrets.json` prints `600`.
  - Check: `curl -H "Authorization: Bearer <laptop key>" https://<vm>.<tailnet>.ts.net/api/today` from the laptop returns real counts.
- [ ] **Step 3: Owner, steps 8–10.** Connect the PC and the laptop, set `keeper_device`, restart the service, clean up.
  - If the laptop had to pair again, `assistant devices` on the VM, then `assistant devices revoke <old id>`.
  - Check: the revoked key gets 401.
- [ ] **Step 4: Record the measurements.**
  - With both computers off, send a test email. Its extraction time in `logs/engine.log` is ≤ 150 s after Gmail received it.
  - A star on one computer shows on the other within 2 s.
  - `systemctl stop pocket-assistant` shows the banner and disables actions on both. Starting it again clears both within 30 s.
  - The first nightly backup is downloaded to the PC and its SHA-256 verified.
  - A port scan of the VM's external IP from off the tailnet finds no open port.
  - Idle memory (`ps -o rss= -C assistant` or `systemctl status`) is under 150 MB.
  - The billing report 48 h later shows $0, or note the external IP's charge.
- [ ] **Step 5: Commit the live-check notes.**

```bash
git add docs/superpowers/specs/2026-11-26-m5-cloud-engine-design.md
git commit -m "Record the M5 live check"
```
