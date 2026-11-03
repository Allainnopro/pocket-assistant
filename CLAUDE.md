# CLAUDE.md: Pocket Assistant

Project memory for Claude Code. Read this before every task.

## What this is
A single-user personal assistant running on Allain's Windows PC:
1. **Opportunity Radar**: finds and ranks hackathons, ideathons, competitions, jobs, internships.
2. **Inbox Butler**: triages Gmail, including university Outlook mail auto-forwarded to Gmail.
3. **Companion**: an original tsundere chibi on the desktop (Tauri 2), plus a Telegram bot for the phone. Text only, no voice.

Budget: at most $10/month in LLM API costs. Owner is a solo developer and AI student.

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
- **Secrets.** Tokens and the Gmail App Password in Windows Credential Manager via `keyring`. Never write tokens, `credentials.json`, `config.toml`, `.env`, or `*.db` to git.
- **Privacy.** University mail contains other students' data. Store extracted fields + short snippets; purge `body_text` after extraction.
- **No scraping logged-in personal accounts** (Facebook, Instagram, LinkedIn). Platforms reach us through their email alerts. Threads only via its official API.
- **Cost.** Log every LLM call (model, tokens, cost) to SQLite. A monthly cap in config stops non-essential calls.
- **Companion is an original character.** No existing anime characters, no cloned voices.
- Keep `parser.py` pure (no network/DB) so it stays unit-testable.

## Roadmap
- [x] **Phase 0: Ingestion.** Gmail OAuth, sync, MIME/HTML parsing, source tagging, SQLite, CLI. 14 tests passing.
- [ ] **Phase 1: Brain + Telegram digest.**
  - `profile.toml`: interests, location (HCMC), level (student/intern), keywords to boost/penalize.
  - `extract.py`: cheap model (Haiku-class) -> JSON schema: kind (hackathon|ideathon|competition|job|internship|uni_notice|other), title, organizer, deadline (ISO date or null), location, remote, eligibility, prize_or_pay, url, relevance 0-100, why (one line). Validate with pydantic; retry once on invalid JSON.
  - `opportunities` table; dedupe on normalized title + organizer + deadline.
  - `llm_calls` table for cost logging + monthly cap.
  - `digest.py`: stronger model writes the daily digest in the character's voice from top items.
  - Telegram bot (python-telegram-bot) to push the digest; APScheduler for sync every 2h + digest at 08:00 Asia/Saigon.
  - Tests with recorded fixture responses; no live API calls in tests.
- [ ] **Phase 2: Sprite chibi.** Tauri 2 transparent always-on-top window, sprite state machine (idle, walk, sit, sleep, excited, letter, watch-tap, pouty), click -> chat bubble backed by a local FastAPI endpoint. Max 3 proactive bubbles/day.
- [ ] **Phase 3: Tracker + calendar.** Detect application-status emails -> pipeline board; deadlines -> Google Calendar (requires click); feedback loop from opens/stars/dismissals.
- [ ] **Phase 4: Upgrades.** Live2D (pixi-live2d-display needs PIXI v6), web search API sources, Threads official API, weekly review.

## Design doc
Full technical design: https://claude.ai/code/artifact/6c471f65-33d2-4607-bb71-4f966ea3de60
