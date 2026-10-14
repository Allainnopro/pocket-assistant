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
1. console.cloud.google.com -> new project -> enable **Gmail API**.
2. OAuth consent screen -> External -> add your Gmail as a **test user**.
3. Credentials -> Create OAuth client ID -> **Desktop app** -> download as `credentials.json` into this folder.
4. Note: while the app is in "Testing" mode, Google expires refresh tokens after 7 days, so you'd re-run `assistant auth` weekly. Publishing the app (even unverified, for yourself) removes that limit.

## Run

```powershell
assistant auth              # browser opens once; token goes to Credential Manager
assistant sync --days 7     # first pull
assistant stats             # counts per source
assistant list --source university
```

Re-running `sync` is cheap: already-stored messages are skipped before download.

## Before you trust it
- Send a test email to your university address and check `assistant list --source university` shows it.
  Some university tenants silently block external auto-forwarding.
- Turn on alerts that feed the radar: LinkedIn job alerts, Devpost/MLH/Devfolio/Unstop, ITviec/TopCV,
  and email notifications for the Facebook groups that post hackathons.
