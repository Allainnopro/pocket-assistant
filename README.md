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
4. Run `assistant set-key gmail` and paste the password at the hidden prompt. It is saved in
   Windows Credential Manager, never in a file, and a sign-in is tested at once.

The password opens your whole mailbox: Google doesn't limit it to reading. Only this project's
code keeps it read-only (`gmail_imap.Mailbox`).

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

## Before you trust it
- Send a test email to your university address and check `assistant list --source university` shows it.
  Some university tenants silently block external auto-forwarding.
- Turn on alerts that feed the radar: LinkedIn job alerts, Devpost/MLH/Devfolio/Unstop, ITviec/TopCV,
  and email notifications for the Facebook groups that post hackathons.
