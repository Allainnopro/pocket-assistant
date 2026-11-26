# M5: the engine moves to a cloud VM

**Status:** design agreed with the owner in conversation on 2026-11-26. This spec is waiting for the owner's review.
**Changes:** decision A6 ("one main computer, the PC") and the "no cloud server" line of section 13 in
`2026-10-25-desktop-app-redesign-design.md`; replaces M4 part 2 (the laptop viewer) of
`2026-11-18-m4-desktop-app-design.md`; the architecture, secrets rule and roadmap in `CLAUDE.md`.

## 1. Why

The owner wants the assistant to keep working around the clock while both computers are off: reading and ranking mail
now, and later Telegram, reports and calendar (the roadmap below). Today the engine lives on the PC, so the PC has to
stay on. M5 moves the same engine, doing the same work, to a free cloud VM, and turns the desktop app on both computers
into a viewer of it.

### Where M5 sits (roadmap agreed 2026-11-26)

| # | Milestone |
|---|---|
| **M5** | **The engine on a free cloud VM; the PC and the laptop become viewers (this spec)** |
| M6 | Two-way Telegram; a desktop agent with a hotkey bar and laptop control |
| M7 | Driving Claude Code, relaying its permission requests, remote mode |
| M8 | Narrow Gmail writes (own labels, archive, drafts), calendar through Apps Script, AI briefings and drafts |
| M9 | Live2D mascot (spike first) and chat |
| M10 | Installer |

Each gets its own spec. The old M5 (chibi), M6 (Telegram) and M7 (installer) fold into M9, M6 and M10.

### Success criteria

| Measure | Target |
|---|---|
| Both computers off | Mail is still read and ranked within 150 s of arriving |
| VM ports reachable from the internet | None |
| A star on one computer shown on the other | ≤ 2 s while both are online (as M2) |
| Engine memory when idle, on the VM | Under 150 MB (as M1) |
| Hosting cost | $0, apart from the external IP if Google charges for it (section 9) |
| Data lost in the move | None: opportunities, stars, applied, cost ledger and paired devices carry over |

## 2. Decisions

| # | Decision (owner's choice unless marked) | Why |
|---|---|---|
| C1 | **Google Cloud's always-free e2-micro.** Rejected: Oracle Always Free, a paid VPS. | $0. Google's free-tier page (read 2026-11-26): one non-preemptible e2-micro a month in `us-west1`, `us-central1` or `us-east1`; 30 GB-months of standard persistent disk; 1 GB of outbound transfer a month from North America to all destinations except China and Australia; a billing account is required. Google's machine-type page lists the e2-micro with 1 GB of memory. |
| C2 | **The same Python engine as a plain Linux service** (approach A, section 3). | Smallest change; nothing about reading or ranking changes. |
| C3 | **Viewer-only desktop app.** The app on both computers only connects to the VM. Its engine supervisor is removed. | Two engines can never read the same mailbox; less code. Development keeps `assistant engine --dev` in a terminal. |
| C4 | **Copy the existing database to the VM once.** Rejected: a fresh 7-day sync. | Keeps stars, applied, the month's cost ledger and the laptop's pairing. The IMAP bookmark means only unseen mail is fetched. |
| C5 | **Nightly snapshot on the VM (keep 7), and the PC's app downloads the newest.** | Survives losing the VM without another cloud service. |
| C6 | **`profile.toml` is edited in the app.** Rejected: editing over SSH; the PC holding a master copy. | The file now lives on the VM; editing from either computer must keep "saved → re-ranked within 30 s". |
| C7 | **Gmail stays on IMAP with an App Password.** | No weekly sign-in (the 2026-11-03 decision). Google's help page still offers App Passwords for personal accounts with 2-Step Verification. |
| C8 | **Development runs on a restored backup, with guards.** Rejected: developing against the live VM; an empty dev database. | Real data to build against, without unfinished code touching the live engine. `--dev` can never check mail, so it can never become a second engine. |

## 3. Approaches (owner picked A)

| | A. Plain service (chosen) | B. Encrypted secrets | C. Docker |
|---|---|---|---|
| How | Repo + `.venv`, a systemd unit running `assistant engine` as its own user, secrets in a `0600` file through a new Linux store in `vault.py` | As A, but secrets encrypted at rest with `systemd-creds` | A container image on the VM |
| Gains | Smallest change, matches the repo | A leaked disk snapshot doesn't expose the secrets | Reproducible |
| Costs | Secrets are plaintext on the VM's disk, protected by file permissions and no public access | Doesn't help against root on the running VM; `set-key` becomes a root step | Heavier on 1 GB of memory; more tooling; pushes secrets toward environment variables, which `vault.py`'s rule forbids |

## 4. Goals

1. The engine runs on the VM around the clock: it starts at boot, restarts after a crash, and needs no terminal.
2. `assistant set-key anthropic|gmail` works on Linux, storing secrets in a file only the service's user can read.
3. The VM is reachable only through Tailscale. No inbound port is open to the internet, SSH included.
4. The desktop app on the PC and the laptop pairs with the VM once, keeps its device key in Windows Credential Manager,
   and shows the dashboard from the VM, with the connection state in the tray and a banner when the VM can't be reached.
5. Settings edits `profile.toml` on the VM.
6. Nightly backups on the VM, and the PC keeps downloaded copies.
7. A written, checked, one-time move of the PC's data to the VM.
8. The test suite runs on Linux as well as Windows in CI.

## 5. Non-goals

- Telegram, laptop control, Claude Code, mail writes, calendar and the mascot (M6–M9).
- An offline snapshot saved to disk on a viewer: only the data already in memory stays visible (my call).
- Deploying from GitHub or updating automatically.
- Editing `config.toml` from the app.
- Two engines, or failing over to the PC.

## 6. Anti-goals (a failure even if it works)

- **Any VM port open to the internet** once set-up is finished, SSH included.
- **Secrets** in the repository, environment variables, process arguments or logs, or readable by any other user.
- **Two engines reading one mailbox:** the PC's engine left running, or started with Windows, after the move.
- **Losing data in the move.**
- **Leaving the free tier unnoticed:** a wrong region, machine type or disk type, or backups eating the 1 GB of outbound
  transfer.
- **A viewer accepting actions while the VM is unreachable** that then silently fail.

## 7. Constraints

- 1 GB of memory on the VM.
- 1 GB of outbound transfer a month. Dashboard traffic, the event streams, backup downloads and calls to Anthropic all
  leave Google's network (Tailscale traffic included), so they all count.
- Every `CLAUDE.md` hard rule stays, except the architecture and secret-storage lines this spec changes.
- Versions pinned at least 14 days old, as before. Python ≥ 3.11 (Ubuntu 24.04 ships 3.12).

## 8. How it works

### 8.1 The VM

- **Machine:** e2-micro, Ubuntu 24.04 LTS, a 30 GB *standard* persistent disk, in `us-west1` (closest of the three to
  Vietnam; my call).
- **A 1 GB swap file** for headroom during `pip install` and spikes (my call).
- **Folders:**
  - code: `/opt/pocket-assistant` (git clone plus `.venv`), owned by root, read-only to the service;
  - data: `/var/lib/pocket-assistant/`, owned by a system user `pocket`, mode `0700`, holding `config.toml`,
    `profile.toml`, `assistant.db`, `engine.lock`, `logs/`, `secrets.json` and `backups/`.
    `config.toml` sets `db_path` and the profile path to files in this folder.
- **Service:** `deploy/pocket-assistant.service`:
  - `User=pocket`, `ExecStart=/opt/pocket-assistant/.venv/bin/assistant --config /var/lib/pocket-assistant/config.toml engine`;
  - `Restart=on-failure`;
  - `KillSignal=SIGINT`, so a stop runs the engine's existing Ctrl+C path (the current job finishes), and
    `TimeoutStopSec=150` to match M4;
  - hardening: `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ReadWritePaths=/var/lib/pocket-assistant`,
    `PrivateTmp=yes`, `ProtectHome=yes`.
- **Scripts** in `deploy/` (my call):
  - `setup.sh`: one-time; creates the user, the folders, the swap file, the clone and `.venv`, installs the unit, and does
    not start it;
  - `update.sh`: `git pull --ff-only`, `pip install -e .`, restart the service.
- **On the VM the launch password is unused:** no app runs there, and `--dev` is never used there.

### 8.2 Secrets on Linux

- `vault.py` keeps `keyring` (Windows Credential Manager) on Windows. Elsewhere it uses `secrets.json` next to
  `assistant.db`.
- **The file:**
  - created with mode `0600`;
  - written atomically: a temporary file in the same folder, then a rename;
  - refused if the group or others can read it. The engine and the CLI then name the fix (`chmod 600 …`).
- **Unchanged:** the same `SecretKind` shape checks, refusals and redaction, the hidden prompt, and Gmail's
  test-before-save.
- Both `vault.py` and `gmail_imap.py` keep calling `keyring`. On Linux the CLI installs the file store as `keyring`'s
  backend at start, so neither changes (settled while planning).
- The cleanup of the old OAuth token stays Windows-only.
- **On the VM, `set-key` runs as `pocket`.** `deploy/README.md` gives the exact
  `sudo -u pocket … assistant --config … set-key gmail` line.

### 8.3 Network

- **Tailscale on the VM,** on the owner's tailnet:
  - Tailscale SSH on (`tailscale up --ssh`);
  - `tailscale serve --bg 47615` publishes the API at `https://<vm>.<tailnet>.ts.net`;
  - `[api] tailnet_host` is the VM's name and `tailnet_login` the owner's login. Every M2 rule (Host checks, the login
    header, CORS for `http://tauri.localhost`) stays unchanged.
- **Google Cloud firewall:** once Tailscale SSH works, delete the default rules that allow SSH, RDP and ICMP from
  anywhere.
  - Recovery if Tailscale breaks (documented): temporarily add an SSH rule limited to the owner's current IP, fix it,
    remove the rule.
- **The VM keeps an external IP** for outbound internet (no Cloud NAT). Whether the free tier covers it is unconfirmed
  (section 9).
- **Google may send a security alert** at the first Gmail sign-in from a US datacenter address. The owner confirms it
  was them.

### 8.4 The desktop app becomes a viewer

- **Removed:**
  - the engine supervisor (`engine.rs`, `backoff.rs`, and the engine and venv parts of `settings.rs`);
  - the *Restart engine* tray item;
  - the stdin handshake.
- **Removed from the engine (now unused):** `--parent-pid`, `--password-stdin`, `engine/parent.py` and their tests (my
  call: code this change orphans).
- **First start, with no saved connection:** a *Connect* screen.
  - It asks for the VM's address (its `*.ts.net` name). Debug builds also accept a local engine at
    `http://127.0.0.1:<port>` (section 8.8).
  - If Credential Manager already holds the laptop's key from M2 (recorded as `pocket-assistant-device-key`; how it was
    written wasn't recorded), the app tries it against that address.
  - If no such key is found or it is refused, the app asks for the 6-digit code from `assistant pair` on the VM, posts it
    to `/api/pair` and stores the new key in Credential Manager. Step 9 of the move then revokes the old device, so no
    stale key stays valid.
  - The app's own Credential Manager entry must not collide with the `pocket-assistant` entries Python's `keyring` keeps
    (my call).
  - The address goes in `%APPDATA%\PocketAssistant\app.json`.
- **The connection handoff:** the Tauri command hands the dashboard `{ baseUrl, key }` instead of `{ port, password }`.
  The dashboard's client and event stream send `Authorization: Bearer <key>` to `baseUrl`, exactly as they send the
  launch password today.
  - The browser development setup (`assistant engine --dev`, Vite, the launch password) stays unchanged.
- **Tray:**
  - tooltip: *Connected to <vm>*, or *Can't reach <vm> since 10:42*;
  - menu: *Open dashboard*, *Start with Windows*, *Forget this engine* (deletes the key and the address, back to
    *Connect*), *Quit*.
- **When the VM is unreachable:**
  - the dashboard keeps showing the data already loaded, under *Engine unreachable since 10:42 · retrying*;
  - actions are disabled;
  - it retries every 30 seconds, through the event stream's existing reconnect. `views/Unreachable.tsx` is the starting
    point.
- **`assistant pair`:** its text says "the engine" instead of "this PC". Its PowerShell line stays as a manual fallback.

### 8.5 Editing the profile

- **`GET /api/profile/text`** returns the current `profile.toml`.
- **`PUT /api/profile/text`** with `{ "text": ... }`:
  - refuses more than 64 KB;
  - parses the text with the same loader as `load_profile`;
  - if the text is broken, answers 400 with the parser's message and keeps the file as it was;
  - otherwise writes the file atomically and re-ranks, through the existing watcher or straight away.
  - Any paired device may use it.
  - A `profile_changed` event goes out on the stream.
- **Settings:** "Open in editor" becomes a monospace editor with *Save* and an inline error. The Tauri `open_profile`
  command is removed.

### 8.6 Backups

- **A nightly job at 03:00 HCMC (my call), on the existing scheduler:**
  - takes a consistent copy with SQLite's online backup (`sqlite3.Connection.backup`);
  - gzips it to `backups/assistant-YYYY-MM-DD.db.gz`;
  - keeps the newest 7.
- **`GET /api/backup/latest`:**
  - streams the newest file, with its date and SHA-256 in response headers;
  - is allowed only for the device id in `[backup] keeper_device` in `config.toml` (my call). Any other device gets 403.
- **The keeper app (the PC):**
  - checks after connecting, then daily;
  - downloads only a backup newer than the one it holds;
  - verifies the SHA-256;
  - keeps 7 in `%LOCALAPPDATA%\PocketAssistant\backups\`.
- **Restore** is documented as manual steps: stop the service, unzip over `assistant.db`, start the service.
- **Privacy:** the PC's copies hold the same data the PC held before M5.

### 8.7 The move (owner's steps, in this order)

1. Create the VM: e2-micro, `us-west1`, Ubuntu 24.04 LTS, 30 GB standard disk. Set a $1 budget alert on the billing
   account. `deploy/README.md` gives the console steps.
2. On the VM: install Tailscale, run `tailscale up --ssh`, then `tailscale serve --bg 47615`. Then delete the public
   firewall rules (8.3).
3. Run `deploy/setup.sh`.
4. **On the PC, stop everything that could start the old engine:**
   - quit the app;
   - turn off *Start with Windows*;
   - check that `engine.lock` is gone and no Python engine is running.
5. Copy `assistant.db`, `profile.toml` and `config.toml` to the VM's data folder over Tailscale.
   - The database uses SQLite's default journal mode (no WAL, `store.py`), so the stopped file is complete on its own.
   - Edit `db_path`, the profile path and `tailnet_host` in the copied `config.toml`.
6. **Secrets on the VM** (my call: a separate App Password per machine):
   - create a new Gmail App Password named for the VM, then run `set-key gmail`; it is tested before it is saved;
   - run `set-key anthropic`.
7. Start the service. Check `assistant auth`, `logs/engine.log` and `/api/today` over Tailscale.
8. **The PC:**
   - install the new app and connect with a pairing code;
   - put the PC's device id in `[backup] keeper_device`;
   - restart the service.
9. **The laptop:** install the new app and connect. Its existing key should work, because the database came along. If
   the app had to pair again, run `assistant devices` on the VM and revoke the laptop's old device.
10. **Clean up:**
    - revoke the PC's old App Password and delete it from the PC's Credential Manager;
    - keep the Anthropic key on the PC, for development's explicit CLI commands only (section 8.8);
    - delete the PC's old `assistant.db` once the first downloaded backup has verified.

### 8.8 Development after the move

- **A dev engine runs on a restored backup:** the newest downloaded backup, unzipped into a dev folder with its own
  `config.toml` whose `db_path` points there. `README.md` gives the steps.
- **`assistant engine --dev` never checks mail and never calls the model.**
  - It registers neither the mail check nor extraction.
  - This holds even if a Gmail App Password is present. This guard keeps a dev engine from ever becoming a second engine
    on the mailbox.
- **Explicit CLI commands still reach Anthropic** with the PC's key, for example `assistant llm smoke --record` for new
  fixtures. The existing caps still apply.
- **App development:**
  - debug builds' *Connect* screen accepts `http://127.0.0.1:<port>`;
  - the PC's device key works against the dev engine, because the restored database holds the PC's device row;
  - `--dev` already allows Vite's origin, which `tauri dev` loads from.
- **Release builds accept only `*.ts.net` addresses.**

### 8.9 Documentation

- **`README.md`:**
  - a new "Running on a cloud VM" section;
  - "The desktop app" rewritten for viewer mode;
  - the supervisor text removed.
- **`deploy/README.md`:** the VM set-up, `set-key` on the VM, updates, restoring a backup, firewall recovery.
- **`CLAUDE.md`:** the architecture (engine on a cloud VM, computers are viewers), the secrets rule (Credential Manager
  on the computers; a `0600` file on the VM), and the roadmap M5–M10. Other hard-rule changes (mail writes, two-way
  Telegram) land with their milestones.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Google charges for the external IPv4 address. Its pricing page couldn't be read on 2026-11-26. | The owner checks the billing report 48 h after the VM starts. Even a few dollars a month fits under the $15 budget. |
| 1 GB of memory is too little | The swap file; idle memory measured in the live check; fallback a paid small VPS (same steps) |
| Outbound transfer goes over 1 GB a month | Backups are gzipped and downloaded only when newer; measured in the live check |
| Tailscale breaks and locks the owner out | The documented temporary firewall rule |
| Google changes the free tier (it promises 30 days' notice) | The $1 budget alert |
| University mail (other students' data) now sits on a US cloud VM | The 7-day body purge and the stored-fields rule stay; the owner accepted the cloud move knowing this |
| Gmail blocks the first sign-in from a datacenter address | The owner confirms the security alert; `assistant auth` reports the refusal clearly, as now |

## 10. Testing

- **Python (offline, as now):**
  - the Linux vault: round trip, `0600` on create, refusal of a readable file, atomic write, redaction;
  - the backup job: the snapshot opens as a valid database, 7 kept, gzip;
  - `/api/backup/latest`: the keeper gets the file and its SHA-256; others get 403;
  - the profile routes: a good save re-ranks; a broken one gets 400 and keeps the last good; the size limit;
  - `--dev` registers no mail check and no extraction, even with a Gmail App Password saved.
- **CI:** the Python tests run on `windows-latest` and `ubuntu-latest`.
- **Front end:** the Connect screen, the `{ baseUrl, key }` handoff, the unreachable banner disabling actions, the profile
  editor.
- **Rust:** the supervisor tests go; tests for saving and forgetting the connection.
- **Live check (recorded, like M1 and M2):**
  - with both computers off, a test email is ranked on the VM within 150 s;
  - the PC and the laptop both connect, and a star shows on the other within 2 s;
  - stopping the service shows the banner and disables actions;
  - a nightly backup is downloaded to the PC and verified;
  - a port scan from outside the tailnet finds nothing open;
  - idle memory is measured;
  - the billing report after 48 h is checked.

## 11. Left to the builder

- Where the profile text routes live (`api_overview.py` or a new module) and whether re-ranking after a save runs straight
  away or through the 30-second watcher.
- Which Rust crate reads and writes Windows Credential Manager from the app, the app's own entry name, and how it looks
  for the laptop's M2 key (best effort; pairing again is the fallback).
- The exact `setup.sh` contents and the Google Cloud console steps, checked against current docs at the time.
- The `app.json` shape after the venv and repo fields go.
