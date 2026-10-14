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

## Before you trust it
- Send a test email to your university address and check `assistant list --source university` shows it.
  Some university tenants silently block external auto-forwarding.
- Turn on alerts that feed the radar: LinkedIn job alerts, Devpost/MLH/Devfolio/Unstop, ITviec/TopCV,
  and email notifications for the Facebook groups that post hackathons.
