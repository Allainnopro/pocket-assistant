# Email Kinds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every email gets one of eleven kinds (rules when sure, else the AI's answer from the existing extraction call, else a visible guess), shown as a coloured dot; the four grey kinds leave Important.

**Architecture:** A pure rule sorter (`email_kinds.py`, like `filters.py`); migration v9 stores the rules' and the AI's answers apart; the API decides the shown kind and the three-day urgency in SQL; the dashboard draws a `KindDot`.

**Tech Stack:** Python 3.12 + SQLite + FastAPI + pydantic (engine); React 19 + Tailwind 4 + Vitest (dashboard).

**Spec:** `docs/superpowers/specs/2026-11-18-email-kinds-design.md`

## Global Constraints

- Kind keys, exactly: `study`, `uni_admin`, `hackathon`, `jobs`, `scholarship`, `news_events`, `uni_mass`, `social`, `promo`, `accounts`, `unsorted`. Grey (not in Important): `uni_mass`, `social`, `promo`, `accounts`.
- Shown names: Subjects & program, Uni admin, Hackathons & contests, Jobs & internships, Scholarships, Uni news & events, Uni mass mail, Social, Newsletters & promos, Accounts & system, Unsorted.
- Colours: light blue #7DD3FC, violet #A78BFA, orange #FB923C, yellow #FACC15, pink #E879F9, green #4ADE80, grey #6B7280, slate #94A3B8, sand #C8A97E, silver #E5E7EB; urgent = `--alert`; unsorted = hollow `--mist` ring. Raw hex only in `index.css` / `tokens.ts`.
- Rules never send an email to a grey kind unless sure; an unsure guess is `news_events` for university mail, `unsorted` otherwise.
- Shown kind = `rule_kind` if `rule_sure`, else `ai_kind`, else `rule_kind`. Urgent = (`rule_urgent` or `ai_urgent`) and received within 3 days.
- Important = shown kind not grey, or urgent, or `injection_flag`. Today's needs-action rows use the same test.
- No subjects or addresses in logs. The labelled set and its checker stay in git-ignored `.local/` and are never committed; repo tests use made-up mail.
- The recorded smoke fixture is re-recorded only after the owner says yes (a paid call, ≈ $0.0006).
- Back up `assistant.db` before the engine first opens it with v9.

## Review Focus

1. Mail stored before the engine re-sorts (rule columns NULL) must still show and count → Task 4 test "an email without rule answers shows as unsorted and stays in Important".
2. A grey-kind email the AI marks urgent stays visible for three days, then leaves → Task 4 test "urgent grey mail is in Important for three days, then in Other".
3. A rules change must re-sort old mail without losing the AI's answers → Task 2 test "a new rules version refills the rule answers and keeps the AI's".
4. Accents and case in Vietnamese subjects ("HỌC BỔNG", "hoc bong", NFD forms) sort the same → Task 1 test "Vietnamese words match with or without accents".
5. The pre-filter dropping an email (codes, security alerts) must still leave it a kind → Task 2 test "a filtered email keeps its rule kind".

---

### Task 1: The rule sorter

**Files:**
- Create: `src/assistant/email_kinds.py`, `tests/test_email_kinds.py`
- Create (git-ignored, never committed): `.local/email-labels/check_rules.py`

**Interfaces:**
- Produces:
  - `KINDS: tuple[str, ...]` (the eleven keys, in the order above), `GREY: frozenset[str]`, `RULES_VERSION: int` (start at 1; bump on every rule change)
  - `@dataclass(frozen=True) class RuleKind: kind: str; sure: bool`
  - `sort_by_rules(sender: str, sender_domain: str, subject: str, source: str, my_addresses: tuple[str, ...]) -> RuleKind`
  - `urgent_by_rules(subject: str) -> bool`
- The owner's own sender lists come from `config.toml [kinds]` (`study_domains`, `study_sender_words`, `hackathon_domains`, `promo_domains`, `accounts_domains`), never from the code; the built-in tables keep only generic platforms (`edstem.org`, `instructure.com`, `facebookmail.com`, `discord.com`, `linkedin.com`, `devpost.com`, `kaggle.com`, the account providers, `google.com`).

- [ ] **Step 1: Write the failing tests** (made-up mail and made-up `[kinds]` values only), each asserting `sort_by_rules(...)`:
  - `Subjects & program are sure`: `edstem.org` → `study`, sure; `instructure.com` → `study`; a sender name holding a configured `study_sender_words` entry → `study`; a lecturer from a configured `study_domains` entry → `study`.
  - `social, promos and accounts are sure only for known senders`: `facebookmail.com` → `social`; `discord.com` → `social`; LinkedIn invitation → `social`; `LinkedIn Job Alerts` → `jobs`; "X is hiring" from LinkedIn → `jobs`; a configured `promo_domains` sender → `promo`; `accounts.google.com` "Security alert" → `accounts`; mail from one of `my_addresses` → `accounts`; all sure.
  - `university words decide when clear`: a university-domain "Thông báo học bổng ..." → `scholarship`, sure; a health-office sender or "khám sức khỏe" → `uni_admin`, sure; a student-affairs "[... - Tuyển dụng] ..." subject → `jobs`, sure; "Cuộc thi Hackathon ..." (hackathon word) → `hackathon`.
  - `an unsure guess is never grey`: a university-domain "Chương trình Đại nhạc hội" → `RuleKind("news_events", False)`; unknown `example.com` "Hello" → `RuleKind("unsorted", False)`; for 50 made-up subjects, every result with `sure=False` has a kind not in `GREY`.
  - `Vietnamese words match with or without accents`: "HỌC BỔNG", "hoc bong" and the NFD form of "học bổng" all give `scholarship`.
  - `urgent words`: `urgent_by_rules` is true for "KHẨN - Nhắc lần 2", "[Bắt buộc] Về việc ...", "Action Required | Join ...", "URGENT: ..."; false for "Bắt đầu học kỳ" and "Weekly digest".
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_email_kinds.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** with `textnorm.fold` for all matching, domain matches like `filters._domain_match`, ordered rules (sender-certain first, then university words, then the visible guess). University mail = `source == "university"` or an academic domain (`filters.ACADEMIC`).
- [ ] **Step 4: Run** the tests — Expected: PASS. Then write `.local/email-labels/check_rules.py`: load the labelled set, map draft kinds to the eleven keys with the spec's moves, run `sort_by_rules` on each email (`my_addresses` and the `[kinds]` lists = the owner's own, from config.toml), print agreement per kind and every sure-grey disagreement. Tune the rules (bump nothing yet) until **no sure-grey decision disagrees** and as many as possible are sure. Run the tests again — Expected: PASS.
- [ ] **Step 5: Commit** `email_kinds.py` and its tests: `Sort emails into kinds by rules: sure for known senders, never grey when unsure`. Ledger the checker's final numbers (agreement per kind, sure share) without any subject text.

### Task 2: Storage — both answers, kept apart

**Files:**
- Modify: `src/assistant/store.py` (migration v9, `add`, `save_extraction`, new `resort_kinds`), `src/assistant/pipeline.py` (pass the AI's answers), `src/assistant/ingest.py` (pass `my_addresses` to `add`)
- Test: `tests/test_store.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `sort_by_rules`, `urgent_by_rules`, `RULES_VERSION` (Task 1).
- Produces:
  - Columns on `mail_items`: `rule_kind TEXT`, `rule_sure INTEGER`, `rule_urgent INTEGER`, `ai_kind TEXT`, `ai_urgent INTEGER` (all nullable; v9 adds them, nothing else).
  - `Store.add(item, my_addresses: tuple[str, ...] = ()) -> bool` fills the three rule columns.
  - `Store.save_extraction(..., ai_kind: str | None = None, ai_urgent: bool = False)` writes the AI columns in the same transaction.
  - `Store.resort_kinds(my_addresses: tuple[str, ...]) -> int` refills the rule columns of every row, sets state `kind_rules_version` to `str(RULES_VERSION)`, returns rows touched.
  - `Store.kinds_outdated() -> bool` — state `kind_rules_version` differs from `RULES_VERSION`.

- [ ] **Step 1: Write the failing tests:**
  - `v9 adds the five kind columns` (fresh and v8 databases).
  - `a stored email gets its rule answers` (add a made-up `edstem.org` mail → `rule_kind='study'`, `rule_sure=1`, `rule_urgent=0`).
  - `a new rules version refills the rule answers and keeps the AI's` (set `ai_kind='scholarship'`, corrupt `rule_kind`, `resort_kinds` → rule columns recomputed, `ai_kind` unchanged, `kinds_outdated()` false afterwards).
  - `the extraction saves the AI's kind and urgency` (pipeline test with a fake reply carrying `email_kind="news_events"`, `urgent=true` → columns set).
  - `a filtered email keeps its rule kind` (an `accounts.google.com` code mail is filtered by the pipeline → `ai_kind` NULL, `rule_kind='accounts'`).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_store.py tests/test_pipeline.py -q` — Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** The pipeline reads `email_kind`/`urgent` from the parsed extraction (Task 3 adds them to the model; until then use `getattr(extraction, "email_kind", None)` — removed in Task 3).
- [ ] **Step 4: Run** the full suite `.venv/Scripts/python -m pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** `Store each email's rule and AI kind apart (v9), re-sort on new rules`.

### Task 3: The AI names the kind and urgency

**Files:**
- Modify: `src/assistant/extraction.py` (`Extraction` fields, prompt section), `src/assistant/pipeline.py` (drop the `getattr`), `tests/fixtures/llm/synthetic_*.json` (replies gain the fields), `tests/fixtures/llm/recorded_smoke_ok.json` (re-recorded), `tests/test_extraction.py`, `tests/test_llm.py`

**Interfaces:**
- Produces: `Extraction.email_kind: Literal[<the ten keys without "unsorted">]`, `Extraction.urgent: bool`, both required.

- [ ] **Step 1: Write the failing tests:** `the reply names the email's kind and urgency` (parse a synthetic reply with `email_kind="scholarship"`, `urgent=false`); `an unknown kind is refused` (`email_kind="spam"` → ValueError); `the prompt describes the kinds and urgency` (system prompt contains every key and "three days").
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_extraction.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.** Prompt section "# The email's kind (for the whole email)" listing the ten keys with the spec's one-line "Covers", plus: "urgent: true only if the email asks the candidate to act within about three days of today, or says it is mandatory or urgent." Update every synthetic fixture's reply JSON.
- [ ] **Step 4: Run** the suite — Expected: everything passes except `test_the_recorded_live_reply_reads_and_prices_as_expected` (the old recording lacks the fields).
- [ ] **Step 5: STOP — ask the owner** for one paid call (≈ $0.0006): `assistant llm smoke --record tests/fixtures/llm/recorded_smoke_ok.json`. After the yes, run it, update that test's usage/cost numbers to the new recording and assert `email_kind == "hackathon"` (the devpost email) and `urgent is False`. Run the suite — Expected: all pass.
- [ ] **Step 6: Commit** `The extraction names each email's kind and urgency`.

### Task 4: API — kinds, urgency and the new Important

**Files:**
- Modify: `src/assistant/engine/api_emails.py`, `src/assistant/engine/api_overview.py`, `src/assistant/engine/runtime.py` (`Engine.__init__`: `if store.kinds_outdated(): store.resort_kinds(my_addresses)`), `app/src/api/openapi.json` + `app/src/api/schema.d.ts` (regenerated)
- Test: `tests/test_api_emails.py`, `tests/test_api_overview.py`, `tests/test_engine_runtime.py`

**Interfaces:**
- Produces: `EmailRow.kind: Literal[<the eleven keys>]` (so the dashboard's generated type is a union), `EmailRow.urgent: bool`; badges lose `"university"`; `NeedsAction` gains `kind`, `urgent`. SQL helpers in `api_emails.py`: `KIND_SQL` (the shown-kind expression, NULL rule columns → `'unsorted'`) and `urgent_sql(since: str) -> str`.

- [ ] **Step 1: Write the failing tests:**
  - `important holds coloured kinds, urgent and flagged mail; other the rest` (one email per kind; `section=important` returns exactly the non-grey ones, `other` the grey ones).
  - `the AI decides when the rules aren't sure` (`rule_sure=0, rule_kind='news_events', ai_kind='uni_mass'` → shown `uni_mass`, in Other) and `sure rules win over the AI`.
  - `urgent grey mail is in Important for three days, then in Other` (clock 2 days after → important, `urgent=true`; 4 days after → other, `urgent=false`).
  - `an email without rule answers shows as unsorted and stays in Important` (all five columns NULL).
  - `Today's needs-action rows leave out grey kinds unless urgent`.
  - `the engine re-sorts when the rules version changed` (runtime test: stale state → rule columns filled after start).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_api_emails.py tests/test_api_overview.py tests/test_engine_runtime.py -q` — Expected: new tests FAIL.
- [ ] **Step 3: Implement**; then `assistant api-schema` and `npm --prefix app run gen:api`.
- [ ] **Step 4: Run** the Python suite and `npm --prefix app run verify` (dashboard types may need the fixture helpers' `kind`/`urgent` defaults) — Expected: all pass.
- [ ] **Step 5: Commit** `API: each email's kind and urgency; grey kinds leave Important`.

### Task 5: The dots

**Files:**
- Modify: `app/src/index.css` (`--kind-*` tokens), `app/src/tokens.ts` (`KIND_COLOURS`), `app/src/components/EmailBadges.tsx` (no university), `app/src/views/{Emails,EmailPane,Today}.tsx`, test fixtures
- Create: `app/src/lib/emailKinds.ts`, `app/src/components/KindDot.tsx`
- Test: `app/src/components/display.test.tsx`, `app/src/views/{Emails,Today}.test.tsx`, `app/src/tokens.test.ts`

**Interfaces:**
- Produces: `KIND_INFO: Record<EmailKind, { label: string; colour: string /* css var */ }>`, `type EmailKind` (from the API schema), `<KindDot kind={...} urgent={...} />` (8 px circle; `role="img"`, `aria-label` and `title` = label, or `Urgent · <label>`; unsorted = hollow ring).

- [ ] **Step 1: Write the failing tests:**
  - `a dot names its kind, and urgent mail says so` (`KindDot` with `scholarship` → accessible name "Scholarships"; `urgent` → "Urgent · Scholarships" and `data-urgent="true"`).
  - `every kind has a name and a colour token` (`KIND_INFO` keys equal the API's eleven; index.css defines each `--kind-*` with the Global Constraints hex; `tokens.ts` `KIND_COLOURS` matches).
  - Emails `each row shows its kind's dot, and the legend lists the kinds in view` (rows of `jobs` and `study` → dots named "Jobs & internships", "Subjects & program"; a legend region lists those two names).
  - Today `needs-action rows show their dot`.
  - Emails pane `names the kind beside the subject`.
- [ ] **Step 2: Run** `npx vitest run` (in app/) — Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Dot before the sender on Emails rows, before the subject on Today's email rows, beside the subject in the pane with the label; legend under the Important/Other tabs: one dot + name per kind present in the current list.
- [ ] **Step 4: Run** `npm --prefix app run verify` — Expected: all pass (colour and lamp guards included).
- [ ] **Step 5: Commit** `Emails show a coloured dot for their kind`.

### Task 6: Live switch-over and check

- [ ] **Step 1:** Make a backup of the real `assistant.db` (git-ignored). Stop the dev engine (in the engine's terminal) with one Ctrl+C and start it again from the branch; confirm the log shows the re-sort and no errors.
- [ ] **Step 2:** In the built-in browser: Emails Important shows only coloured dots (plus red/unsorted), Other the grey ones; counts match `section` queries; Today's rows carry dots; hover names; the legend.
- [ ] **Step 3:** Report to the owner: emails per kind on their real data, how many the rules were sure of, and the checker's agreement numbers. Delete `.local/email-labels/` and the scratchpad export (the spec's privacy promise) once the owner is happy with the sorting.
