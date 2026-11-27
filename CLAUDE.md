# CLAUDE.md: Pocket Assistant

Project memory for Claude Code. Read this before every task.

## What this is
A single-user personal assistant. The engine runs on a Google Cloud VM (reached only over Tailscale); Allain's PC and laptop are viewers (the Tauri app):
1. **Opportunity Radar**: finds and ranks hackathons, ideathons, competitions, jobs, internships.
2. **Inbox Butler**: triages Gmail, including university Outlook mail auto-forwarded to Gmail.
3. **Companion**: an original tsundere chibi on the desktop (Tauri 2), plus a Telegram bot for the phone. Text only, no voice.

Budget: LLM soft cap $10/month, hard $15. Owner is a solo developer and AI student.

## How to work with the owner
- Explain every non-trivial change like a senior engineer teaching a student: what it does, why this design, what the tradeoff is. Keep it simple.
- Fix files directly rather than describing fixes.
- Verify library APIs and versions against current docs before using them; don't rely on memory for fast-moving packages (Tauri, Anthropic SDK, Telegram libs).
- Small, reviewable steps. Run tests after each step and show the result.

## Architecture (layers, each replaceable)
```
Collectors (scheduled) -> Brain pipeline -> Memory (SQLite) -> Faces (chibi, Telegram, daily digest)
```
- Brain pipeline: 1 rule pre-filter (free) -> 2 extract to JSON (cheap model) -> 3 dedupe -> 4 score vs profile -> 5 daily digest (stronger model).
- Python core: FastAPI + APScheduler (Phase 1+). n8n only if connectors get painful.
- `MailItem` (src/assistant/models.py) is the shape every layer speaks. Collectors convert raw formats once, at the edge.

## Layout
```
src/assistant/  models.py config.py gmail_imap.py gmail_message.py parser.py store.py cli.py
app/            the dashboard (React + Vite); app/src-tauri/ the desktop app (Tauri 2: tray, connects to the engine)
character/tsundere.md   character card (system prompt for chat + digest)
tests/                  offline tests, no network or account needed
```

## Commands
```
pip install -e ".[dev]"
pytest
assistant auth | sync --days 7 | list [--source X] | stats
```

## Hard rules (do not break)
- **Read-only mail, enforced in our code.** The owner chose an App Password over OAuth on 2026-11-03 to end the weekly sign-in, so Google no longer enforces read-only: the password has full mailbox access. Only `gmail_imap.Mailbox` touches IMAP, through its command allow-list, in a folder opened read-only, with `BODY.PEEK`. The code never sends, deletes, or modifies mail, and never imports `smtplib`. Any future action (calendar insert, draft reply) requires an explicit user click.
- **Prompt injection.** Email content is untrusted data. Wrap it as data in prompts; LLM outputs are JSON validated against a schema; no email text can trigger an action.
- **Secrets.** Tokens and the Gmail App Password in Windows Credential Manager via `keyring` on the computers, and in a `0600` `secrets.json` next to the database on the VM. Never write tokens, `credentials.json`, `config.toml`, `.env`, or `*.db` to git.
- **Privacy.** University mail contains other students' data. Store extracted fields + short snippets; purge `body_text` after extraction.
- **No scraping logged-in personal accounts** (Facebook, Instagram, LinkedIn). Platforms reach us through their email alerts. Threads only via its official API.
- **Cost.** Log every LLM call (model, tokens, cost) to SQLite. A monthly cap in config stops non-essential calls.
- **Companion is an original character.** No existing anime characters, no cloned voices.
- Keep `parser.py` pure (no network/DB) so it stays unit-testable.

## Roadmap
- [x] **M1: Engine.** Gmail over IMAP, parsing, extraction, ranking, SQLite, the always-on engine.
- [x] **M2: API.** Local API, paired devices over Tailscale, live events.
- [x] **M3: Dashboard.** React dashboard: Today, Opportunities, Emails, Reports, Settings.
- [x] **M4: Desktop app.** Tauri 2 tray app around the dashboard.
- [x] **M5: Cloud engine.** The engine on a free Google Cloud VM; the PC and laptop are viewers (Connect screen, offline banner, profile editor in Settings); nightly backups downloaded by the keeper PC. Spec: `docs/superpowers/specs/2026-11-26-m5-cloud-engine-design.md`.
- [ ] **M6: Telegram + desktop agent.** Two-way Telegram; a desktop agent with a hotkey bar and laptop control.
- [ ] **M7: Claude Code.** Driving Claude Code, relaying its permission requests, remote mode.
- [ ] **M8: Mail writes + briefings.** Narrow Gmail writes (own labels, archive, drafts), calendar through Apps Script, AI briefings and drafts.
- [ ] **M9: Mascot.** Live2D mascot (spike first; pixi-live2d-display needs PIXI v6) and chat.
- [ ] **M10: Installer.**

Each milestone gets its own spec.

## Design doc
Full technical design: https://claude.ai/code/artifact/6c471f65-33d2-4607-bb71-4f966ea3de60
