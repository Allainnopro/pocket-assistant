# Pocket Assistant (Phase 0: ingestion)

Personal assistant = opportunity radar + inbox butler + tsundere desktop companion.
Phase 0 does one thing well: pull your Gmail (including mail forwarded from
university Outlook) into a local SQLite database, tagged by source.

## Layout

```
src/assistant/
  models.py        MailItem: the one shape every layer speaks
  config.py        loads config.toml
  gmail_client.py  OAuth (read-only) + list/fetch, token in Windows Credential Manager
  parser.py        raw Gmail JSON -> MailItem (MIME walk, HTML->text, source tagging)
  store.py         SQLite, dedupe on Gmail message id
  cli.py           assistant auth | sync | list | stats
character/tsundere.md   character card for Phase 1+
tests/test_parser.py    offline tests for the parser
tests/test_gmail_client.py  offline tests for token storage (fake keyring, no Google calls)
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

### Google Cloud (one time, ~10 minutes)
1. console.cloud.google.com -> new project (no billing needed) -> enable **Gmail API**.
2. Google Auth Platform -> Branding -> Get Started: app name, your Gmail as support/contact email,
   Audience **External** (Internal needs a Workspace organization; personal Gmail can't use it).
3. Audience -> Test users -> Add users -> your Gmail.
4. Clients -> Create Client -> **Desktop app** -> click **Download JSON** right away (Google shows the
   secret only once) -> save it in this folder as `credentials.json`.
5. Note: while the app is in "Testing", Google expires refresh tokens 7 days after sign-in, so you'd re-run
   `assistant auth` weekly. Audience -> **Publish app** (no verification needed for personal use under
   100 users; you click through an "unverified app" warning) avoids that. Run `assistant auth` once more
   after publishing, since tokens issued during Testing keep their 7-day expiry.

## Run

```powershell
assistant auth              # browser opens once; token goes to Credential Manager
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

- **Every 2 minutes** it reads Gmail's change feed: only mail that arrived since the last
  check, plus mail you rescued from Spam or the bin. New mail is extracted at once, 4 emails
  at a time (`[engine] extract_workers` in config.toml, 1-8). On its first start, or after
  more than a week away, it searches the days it missed instead (at most 7).
- **Every 30 seconds** it notices an edit to `profile.toml` and re-ranks everything. A
  broken edit is reported and the last good profile stays in use.
- **Every hour** it deletes email bodies older than 7 days (the privacy rule).
- Offline, it retries in 30 seconds, then every 5, then every 15 minutes. When Anthropic is
  busy it waits 5, then 15, then 60 minutes; at the daily cap it waits until midnight.

The log is `logs\engine.log` next to `assistant.db` (5 files of 1 MB at most). It names
emails by id: no subjects, senders, bodies or API keys.

While the engine runs, the commands that write refuse with a message: `sync`, `filter`
(without `--dry-run`), `extract` (without `--estimate`) and `rescore`. Everything else
works. `assistant set-key anthropic` takes effect without a restart; other config.toml
changes need one.

In `[gmail] query`, only label exclusions (`-in:spam`, `-category:promotions`, ...) apply
to new mail; the engine warns at start about any other term (`from:`, `OR`, ...).

Ctrl+C once lets the current job finish; Ctrl+C again quits at once.

## Before you trust it
- Send a test email to your university address and check `assistant list --source university` shows it.
  Some university tenants silently block external auto-forwarding.
- Turn on alerts that feed the radar: LinkedIn job alerts, Devpost/MLH/Devfolio/Unstop, ITviec/TopCV,
  and email notifications for the Facebook groups that post hackathons.
