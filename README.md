# Pocket Assistant (Phase 0: ingestion)

Personal assistant = opportunity radar + inbox butler + tsundere desktop companion.
Phase 0 does one thing well: pull your Gmail (including mail forwarded from
university Outlook) into a local SQLite database, tagged by source.

## Layout

```
src/assistant/
  models.py        MailItem: the one shape every layer speaks
  config.py        loads config.toml
  gmail_imap.py    IMAP + App Password: Mailbox can only read. Password in Windows Credential Manager
  gmail_message.py one raw email -> the shape parser.py reads
  parser.py        raw Gmail JSON -> MailItem (MIME walk, HTML->text, source tagging)
  store.py         SQLite, dedupe on Gmail message id
  cli.py           assistant auth | sync | list | stats
character/tsundere.md   character card for Phase 1+
tests/test_parser.py    offline tests for the parser
tests/test_gmail_imap.py    offline tests against a fake IMAP server (fake keyring, no Google calls)
```

## Setup (Windows, PowerShell)

```powershell
cd pocket-assistant
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest                      # parser tests, no account needed
copy config.example.toml config.toml   # then set your university address
```

### Gmail App Password (one time, ~2 minutes)
1. Turn on **2-Step Verification** for your Google account. App Passwords need it.
2. Create a password at <https://myaccount.google.com/apppasswords>. Google shows it only once.
3. In `config.toml`, set your inbox under `[gmail]`: `address = "you@gmail.com"`.
4. Run `assistant set-key gmail` and paste the password at the hidden prompt. A sign-in is
   tested first, and the password is saved in Windows Credential Manager (on the VM, a private
   secrets.json), only if Gmail accepts it, so a typo never replaces one that works.

The password opens your whole mailbox: Google doesn't limit it to reading. Only this project's
code keeps it read-only (`gmail_imap.Mailbox`). Gmail's certificate is checked on every
connection; if it doesn't check out (a network intercepting connections), the password isn't
sent and you're told to try another network.

**To end it:** delete it at <https://myaccount.google.com/apppasswords>, which works at once.
Changing your Google password also ends it. Either way, create a new one and run
`assistant set-key gmail`.

## Run

```powershell
assistant auth              # signs in with the saved password and reports the result
assistant sync --days 7     # first pull
assistant stats             # counts per source
assistant list --source university
```

Re-running `sync` is cheap: already-stored messages are skipped before download.

## Running the engine

The engine normally runs on a cloud VM: see [Running on a cloud VM](#running-on-a-cloud-vm).
To run it by hand (on the VM, or in a terminal while developing):

```powershell
assistant engine            # keeps running; Ctrl+C to stop
```

The engine replaces running `sync`, `filter`, `extract` and `rescore` by hand:

- **Every 2 minutes** it asks Gmail for mail newer than the last message it saw: only mail
  that arrived since the last check, plus mail you rescued from Spam or the bin. New mail is
  extracted at once, 4 emails at a time (`[engine] extract_workers` in config.toml, 1-8). On
  its first start, or if Gmail renumbers its messages, it searches the days it missed instead
  (at most 7).
- **Every 30 seconds** it notices an edit to `profile.toml` and re-ranks everything. A
  broken edit is reported and the last good profile stays in use.
- **Every hour** it deletes email bodies older than 7 days (the privacy rule).
- If Gmail refuses the password, it says what to run and tries the same one only hourly, so
  repeated failures can't get the account locked. A new one is used within a minute or two.
- Offline, it retries in 30 seconds, then every 5, then every 15 minutes; extraction picks
  up again within a minute of a mail check getting through. When Anthropic is busy it waits
  5, then 15, then 60 minutes; at the daily cap it waits until midnight.

The log is `logs\engine.log` next to `assistant.db` (5 files of 1 MB at most). It names
emails by id: no subjects, senders, bodies or API keys.

While the engine runs, the commands that write refuse with a message: `sync`, `filter`
(without `--dry-run`), `extract` (without `--estimate`) and `rescore`. Everything else
works. `assistant set-key anthropic` and `assistant set-key gmail` take effect within a
minute or two, without a restart; other config.toml changes need one.

In `[gmail] query`, only label exclusions (`-in:spam`, `-category:promotions`, ...) apply
to new mail; the engine warns at start about any other term (`from:`, `OR`, ...).

Ctrl+C once lets the current job finish; Ctrl+C again quits at once.

## Running on a cloud VM

The engine runs on a small Google Cloud VM (free e2-micro, Ubuntu 24.04), so mail is read and
ranked while both computers are off. The VM is reached only through Tailscale: no port is open
to the internet. **[deploy/README.md](deploy/README.md)** has the steps: creating the VM,
Tailscale, the set-up script, secrets, updates and restoring a backup.

- **Secrets** on the VM (`assistant set-key`) go in a private `secrets.json` next to
  `assistant.db`, readable only by the service's user. Always run `set-key` and `pair` there
  as `pocket` (`sudo -u pocket`); a file others can read is refused, with the `chmod 600` fix.
- **The API** is served by `tailscale serve --bg 47615`, at
  `https://<vm-name>.<your-tailnet>.ts.net`. Every request needs a paired device's key.
  In config.toml set `[api] tailnet_host` (the name alone, in lower case) and `tailnet_login`.
- **Pairing:** `assistant pair` on the VM shows a 6-digit code (good once, for 5 minutes). Type it
  into the desktop app's Connect screen. The command also prints a PowerShell line, a manual
  fallback.
- **Lost a computer?** `assistant devices` lists the paired ones, and
  `assistant devices revoke <id>` cuts one off: at once for new requests, and within
  15 seconds for its open live feed.
- **Backups:** every night at 03:00 (Ho Chi Minh City) the engine saves
  `backups/assistant-YYYY-MM-DD.db.gz` next to the database and keeps the newest 7. The
  computer whose device id is in `[backup] keeper_device` downloads the newest one after
  connecting and then daily, checks it, and keeps 7 in `%LOCALAPPDATA%\PocketAssistant\backups\`.
  A failed backup shows on the dashboard.
- **Profile:** edit `profile.toml` in the app's Settings, from any paired computer. A broken
  file is refused with the parser's message and the saved one stays; a good one is re-ranked
  within 30 seconds.

## The desktop app

The desktop app is a viewer: a tray icon and the dashboard in its own window, showing the
engine on the VM. It runs no engine itself. Install it on each computer.

Build it in `app/` (needs [Rust](https://rustup.rs) and the Visual Studio C++ Build Tools),
the first time and after pulling changes:

```powershell
npm ci --ignore-scripts
npx tauri build --no-bundle
```

Then run `app\src-tauri\target\release\pocket-assistant.exe`.

- **First start:** a Connect screen asks for the VM's address (`<vm-name>.<tailnet>.ts.net`).
  If the laptop still holds its old device key, that is tried first. Otherwise run
  `assistant pair` on the VM (the exact line is in [deploy/README.md](deploy/README.md)) and enter the 6-digit code and a
  name for this computer. Nothing is saved until the engine has answered. A wrong address or
  Tailscale being off says "Can't reach ..."; a wrong code is reported separately.
- **Where things are kept:** the address in `%APPDATA%\PocketAssistant\app.json`; the device
  key in Windows Credential Manager (`pocket-assistant-app`), never in a file.
  The app also turns on *Start with Windows*.
- **The tray icon:** left-click opens the dashboard. Right-click for *Open dashboard*,
  *Start with Windows*, *Forget this engine* (deletes the saved key and address, back to
  Connect) and *Quit*. Hovering shows the connection state.
- **If the VM can't be reached,** the dashboard keeps the data already loaded under a banner,
  disables actions and retries every 30 seconds. Closing its window only hides it; running
  the exe again shows it.
- **Quit** closes the app only; the engine keeps working on the VM.

## Development after the move

Develop against a copy of your data, not the live VM:

1. Unzip the newest downloaded backup (`%LOCALAPPDATA%\PocketAssistant\backups\`) into a dev
   folder, e.g. `dev\assistant.db`.
2. Put a `config.toml` in that folder whose `[storage] db_path` (and `[paths] profile`) point
   there, and copy `profile.example.toml` for the profile.
3. `assistant --config dev\config.toml engine --dev`. It prints a launch password and lets
   Vite's dev server call the API. **It never checks mail and never calls the model**, even if
   a Gmail password is saved, so it can't become a second engine on your mailbox.
4. Run `assistant --config dev\config.toml pair`; it prints a pairing code for the dev engine.
5. In `app/`, `npx tauri dev` (quit the tray app first if `tauri dev` exits at once: only one
   copy of the app can run). In a debug build the Connect screen also accepts
   `127.0.0.1:47615` (any port from 1024); release builds accept only `*.ts.net` names. Pair
   the debug build with the code from step 4. A debug build keeps its own connection
   (`app-dev.json` and the Credential Manager entry `device-key-dev`), so the release app stays
   connected to the live VM and its key is untouched.

Commands you run yourself, like `assistant llm smoke --record`, still use an Anthropic key
saved on the PC for development only. The engine's own keys are set on the VM
([deploy/README.md](deploy/README.md)). The usual caps apply.

## Using the dashboard in a browser (development)

1. Start the engine in dev mode on a restored backup (see
   [Development after the move](#development-after-the-move)): `assistant engine --dev`. It prints
   a launch password (new at every start) and lets the dashboard's dev server call the API.
2. In `app/`, the first time: `npm install --ignore-scripts`. Then `npm run dev`.
3. Open `http://localhost:5173` and paste the launch password. It lasts until you close the
   tab; after an engine restart the dashboard asks for the new one.

Keys: `g t` / `g o` / `g e` / `g r` / `g s` jump to Today, Opportunities, Emails, Reports,
Settings; `Ctrl+K` or `/` searches; in a list, `j` / `k` move, `Enter` opens, `Esc` closes,
`s` stars, `d` dismisses, `a` marks applied. Keys never fire while you're typing in a field.

Changes appear live (a star on one computer shows on the other within a couple of seconds). If the
engine stops, the dashboard says so and reconnects by itself.

For development: `npm test`, `npm run build`, and after changing an API route,
`assistant api-schema` then `npm run gen:api` (`npm run verify` runs all three checks).

## Before you trust it
- Send a test email to your university address and check `assistant list --source university` shows it.
  Some university tenants silently block external auto-forwarding.
- Turn on alerts that feed the radar: LinkedIn job alerts, Devpost/MLH/Devfolio/Unstop, ITviec/TopCV,
  and email notifications for the Facebook groups that post hackathons.
