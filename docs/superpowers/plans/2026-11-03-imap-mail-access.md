# IMAP Mail Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The assistant reads Gmail over IMAP with an App Password instead of the Gmail API with OAuth, so the weekly sign-in ends, while the code stays strictly read-only.

**Architecture:**
- A new `gmail_imap.py` holds `Mailbox`, the only code that touches IMAP. It sends commands through an allow-list, in a folder opened read-only.
- A pure `gmail_message.py` converts each downloaded email into the Gmail-API shape `parser.py` already reads, so everything after "here is an email" stays as it is.
- `check_mail` keeps its logic, on a UID bookmark instead of a history id.

**Tech Stack:** Python's built-in `imaplib` and `email`; `keyring` for the password. The Google libraries are removed.

**Spec:** `docs/superpowers/specs/2026-11-03-imap-mail-access-design.md`, which overrides `2026-10-25-desktop-app-redesign-design.md` on mail access.

## Global Constraints

- **Host:** `imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)`. A fresh connection per check.
- **The allow-list:** `LOGIN`, `LIST`, `EXAMINE`, `UID SEARCH`, `UID FETCH`, `LOGOUT`. Anything else raises before reaching the network.
- **Fetch items allowed:** `X-GM-MSGID`, `X-GM-THRID`, `X-GM-LABELS`, `INTERNALDATE`, `BODY.PEEK[]<0.2000000>`. Never `BODY[]` or `RFC822`, which mark mail as read.
- **All Mail** is found by the `\All` attribute from `LIST`, and opened with `select(folder, readonly=True)`.
- **Ids:** `X-GM-MSGID` and `X-GM-THRID` are decimal; the stored ids are the same numbers in lower-case hex, with no `0x`.
- **State keys:** `imap_uidvalidity`, `imap_last_uid`, `gmail_address`, `last_ok_check_at`, `last_check_error`. `gmail_history_id` is deleted on the first good check.
- **Password:** vault kind `gmail`, entry `gmail-app-password`; inner spaces removed; exactly 16 lower-case letters. Its value and any part of it are never printed or logged.
- **Waits:** a rejected password is rechecked for a new saved one every **1 min**, and the same one is retried every **60 min**. Offline: 30 s, 5 min, 15 min. Gmail busy: 5 min, 15 min.
- **Fallback search:** the bookmark first, then `min(days since the last good check, 7)` days, at most 5,000 messages.
- **Tests stay offline.** `IMAP4_SSL` is replaced by a fake in every test (`tests/fake_imap.py`); `tests/conftest.py` blocks real hosts anyway.
- The only live contact with Gmail happens in Tasks 4 and 9, with the owner.

## Review Focus

1. **A malformed email** (unknown charset, broken MIME, no body, undecodable header) is converted with what can be read, or skipped by id. It never stops the check. Test: Task 1, `test_a_broken_email_still_converts`.
2. **The connection drops halfway through a batch** (the lid closes). What was saved stays saved, the bookmark doesn't move, and the next check fetches only the rest. Test: Task 5, `test_a_connection_lost_midway_keeps_the_saved_mail_and_the_bookmark`.
3. **All Mail is named differently or hidden.** A Vietnamese-language account has another folder name; All Mail can also be hidden from IMAP in Gmail's settings. The first works; the second says what to change. Tests: Task 3, `test_all_mail_is_found_by_its_attribute_in_any_language`, `test_all_mail_hidden_from_imap_says_what_to_change`.
4. **The password pasted as Google shows it** (`abcd efgh ijkl mnop`, or piped with a byte-order mark) is accepted; a wrong length is refused without echoing it. Test: Task 2, `test_a_gmail_password_is_accepted_as_google_shows_it`.
5. **IMAP's `UID n:*` always returns the newest message,** even when `n` is past it. A quiet check must fetch nothing. Test: Task 3, `test_a_quiet_mailbox_lists_nothing_new`.

---

### Task 1: Converting one email

**Files:**
- Create: `src/assistant/gmail_message.py`
- Test: `tests/test_gmail_message.py`

**Interfaces:**
- Produces: `to_api_shape(raw: bytes, *, message_id: str, thread_id: str, labels: list[str], internal_date_ms: int) -> dict`. Pure, no network. The result has:
  - `id`, `threadId`, `labelIds`, and `internalDate` (a string);
  - `snippet`: the first 200 characters of the email's text, plain text preferred, HTML stripped through `parser.html_to_text` otherwise;
  - `payload: {"mimeType", "headers": [{"name", "value"}], "parts": [{"mimeType", "body": {"data": <base64url>}}]}`, the shape in `parser.py`'s docstring, with only `text/plain` and `text/html` parts. A non-multipart email gets one entry in `parts`.
- Uses `email.message_from_bytes(raw, policy=email.policy.default)`. Parts are read with their declared charset and `errors="replace"`; an unknown charset falls back to UTF-8 with replacement. Attachments are skipped.

- [ ] **Step 1: Write the failing tests**

```python
def shape(raw, **kw):
    return to_api_shape(raw, message_id="abc", thread_id="t1", labels=["INBOX"], internal_date_ms=1760000000000, **kw)

def test_headers_are_decoded_as_the_gmail_api_returns_them():
    raw = b"From: =?UTF-8?B?UGjDsm5nIMSQw6BvIHThuqFv?= <pdt@example.edu>\r\nSubject: =?UTF-8?B?VGjDtG5nIGLDoW8=?=\r\n\r\nx"
    assert get_header(shape(raw)["payload"], "Subject") == "Thông báo"
    assert get_header(shape(raw)["payload"], "From").startswith("Phòng Đào tạo")

def test_the_result_builds_a_mail_item_through_the_real_parser():
    item = build_mail_item(shape(SAMPLE), ("me@uni.edu",), 4000, ("me@gmail.com",))
    assert (item.message_id, item.thread_id, item.subject) == ("abc", "t1", "Hello") and "body text" in item.body_text

def test_quoted_printable_vietnamese_is_decoded(): ...       # Content-Transfer-Encoding: quoted-printable, charset utf-8
def test_a_multipart_email_keeps_both_text_parts_and_no_attachment(): ...
    # multipart/mixed with text/plain, text/html and a PDF attachment -> 2 parts; the PDF bytes appear nowhere
def test_an_html_only_email_has_a_text_snippet(): ...        # snippet holds the words, no tags
def test_the_snippet_is_at_most_200_characters(): ...
def test_a_message_truncated_at_2_mb_converts(): ...         # a multipart body cut in the middle of a part, no closing boundary
def test_a_broken_email_still_converts():                    # Review Focus 1
    for raw in (b"", b"not an email at all", b"Content-Type: text/plain; charset=klingon\r\n\r\nabc\xff",
                b"Subject: =?bad?Q?x?=\r\nContent-Type: multipart/mixed\r\n\r\nno boundary"):
        assert shape(raw)["id"] == "abc"
```

- [ ] **Step 2: Run them to confirm they fail** (no module `assistant.gmail_message`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit:** `git commit -m "Convert a downloaded email to the shape the parser reads"`

---

### Task 2: The password and the address

**Files:**
- Modify: `src/assistant/vault.py`, `src/assistant/config.py`, `src/assistant/cli.py` (`set-key` choices), `config.example.toml`
- Test: `tests/test_vault.py` (append), `tests/test_cli.py` (append, for the config)

**Interfaces:**
- Produces:
  - `vault.SECRETS["gmail"]`: entry `gmail-app-password`, label `Gmail App Password`, shape `[a-z]{16}`, created at `https://myaccount.google.com/apppasswords`.
  - `SecretKind` gains `spaces_allowed: bool = False`, which removes all inner whitespace before the shape check, and `shows_ending: bool = True`. For `gmail` they are `True` and `False`: `set_key` prints no characters of this password.
  - `Config.gmail_address: str = ""`, from `[gmail] address`, lower-cased. A value without `@` raises `ConfigError`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("pasted", ["abcdefghijklmnop", "abcd efgh ijkl mnop", " abcd efgh ijkl mnop\n", "﻿abcd efgh ijkl mnop"])
def test_a_gmail_password_is_accepted_as_google_shows_it(pasted):   # Review Focus 4
    save("gmail", pasted)
    assert load("gmail") == "abcdefghijklmnop"

@pytest.mark.parametrize("wrong", ["short", "abcdefghijklmnopq", "ABCDEFGHIJKLMNOP", "abcd1234efgh5678"])
def test_a_wrong_gmail_password_is_refused_without_echoing_it(wrong):
    with pytest.raises(SecretError) as info:
        save("gmail", wrong)
    assert wrong not in str(info.value)

def test_set_key_gmail_prints_no_part_of_the_password(capsys): ...  # stdin piped; out holds none of its letters' runs: "mnop" not in out
def test_anthropic_keys_still_cant_hold_spaces(): ...               # "sk-ant-usr-" + "a b" * 20 is refused
def test_the_gmail_address_is_read_and_lower_cased(tmp_path): ...   # [gmail] address = "Me@Gmail.com" -> "me@gmail.com"; default ""
def test_an_address_without_at_is_refused(tmp_path): ...
```

- [ ] **Step 2: Run them to confirm they fail.**
- [ ] **Step 3: Implement.** The refusal message for `gmail` says what it should look like ("16 letters; copy it from <where>"), never the value.
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit:** `git commit -m "Store a Gmail App Password and the Gmail address"`

---

### Task 3: `Mailbox`, the only code that touches IMAP

**Files:**
- Create: `src/assistant/gmail_imap.py`, `tests/fake_imap.py`
- Test: `tests/test_gmail_imap.py`

**Interfaces:**
- Consumes: `vault.load("gmail")`, `Config.gmail_address` (Task 2); `to_api_shape` (Task 1); `errors.GmailAuthError`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class Bookmark: uidvalidity: str; last_uid: int

  @dataclass(frozen=True)
  class MailRef: id: str; uid: int; labels: tuple[str, ...]

  class MessageGone(Exception): ...
  class GmailBusy(Exception): ...

  def open_mailbox(cfg: Config) -> Mailbox: ...
  def token_fingerprint() -> bytes | None: ...   # SHA-256 of the saved password; None when there is none

  class Mailbox:                                 # a context manager; __exit__ calls close()
      address: str
      def bookmark(self) -> Bookmark: ...        # from the select's UIDVALIDITY and UIDNEXT - 1
      def list_new(self, after_uid: int) -> list[MailRef]: ...
      def search(self, query: str, limit: int) -> list[MailRef]: ...   # X-GM-RAW; the newest `limit`, oldest first
      def get_message(self, message_id: str) -> dict: ...              # a MailRef listed on this connection
      def close(self) -> None: ...               # LOGOUT, ignoring a dead connection
  ```
- **The one gate:** every server command goes through `Mailbox._send(command, *args)`, which raises `PermissionError` for anything outside the allow-list, and for a `UID FETCH` whose items aren't in the allowed set.
- **Listing:**
  - `UID SEARCH UID <n>:*`, dropping UIDs ≤ `after_uid`;
  - then `UID FETCH <uids> (X-GM-MSGID X-GM-LABELS)` in batches of 500;
  - then one `UID SEARCH UID <uids> X-GM-RAW "category:<name>"` per category, in batches of 500.
- **Labels:** `\Inbox` → `INBOX`, `\Sent` → `SENT`, `\Draft` and `\Drafts` → `DRAFT`, `\Important` and `Important` → `IMPORTANT`, `\Starred` → `STARRED`; others unchanged. Categories: `primary` → `CATEGORY_PERSONAL`, and `social`, `promotions`, `updates`, `forums` → `CATEGORY_<NAME>`.
- **Parsing `X-GM-LABELS`:** Gmail sends them as `("\\Inbox" "\\Sent" Work "My label")`; a small tokenizer handles quotes and doubled backslashes.
- **Errors,** translated in one place:

  | From `imaplib` | Raised |
  |---|---|
  | `LOGIN` failing | `GmailAuthError("Gmail refused the App Password. Run: assistant set-key gmail")` |
  | No address, no saved password | `GmailAuthError` naming what to set |
  | `OVERQUOTA`, `THROTTLED`, `Too many simultaneous connections` | `GmailBusy` |
  | `UNAVAILABLE` | `ConnectionError` |
  | No folder with `\All` | `AssistantError` saying to show All Mail in Gmail → Settings → Labels → "Show in IMAP" |
  | `IMAP4.abort`, `OSError` | unchanged |
  | A non-ASCII `[gmail] query` | `ConfigError` |

  No raised message may contain the password.
- **`tests/fake_imap.py`:** `FakeImap`, standing in for `imaplib.IMAP4_SSL`, holds messages (raw bytes, labels, ids) and implements exactly `login`, `list`, `select`, `response`, `uid`, `logout`. It answers in imaplib's real formats, including a fetch's `(meta, literal)` tuples and closing `b")"`. Any other attribute raises `AssertionError`, and `select` asserts `readonly=True`. It records `commands`.

- [ ] **Step 1: Write the failing tests**

```python
def test_it_signs_in_and_opens_all_mail_read_only(fake): ...
    # commands start LOGIN, LIST, EXAMINE; the password and address reached login; 30 s timeout and port 993 passed
def test_all_mail_is_found_by_its_attribute_in_any_language(fake):   # Review Focus 3
    # LIST returns (\HasNoChildren \All) "/" "[Gmail]/T&AOU-t c&HqM- th&AbA-": that exact name is what select received
def test_all_mail_hidden_from_imap_says_what_to_change(fake):        # Review Focus 3
    with pytest.raises(AssistantError, match="Show in IMAP"): ...
def test_a_rejected_password_is_an_auth_error_without_the_password(fake): ...
def test_a_missing_password_or_address_says_what_to_set(fake): ...
def test_the_bookmark_comes_from_the_folder(fake): ...               # UIDVALIDITY 7, UIDNEXT 42 -> Bookmark("7", 41)
def test_list_new_returns_hex_ids_and_labels(fake): ...
    # msgid 1760000000000 -> hex(...)[2:]; ("\\Inbox" Work) -> ("INBOX", "Work")
def test_a_quiet_mailbox_lists_nothing_new(fake):                    # Review Focus 5
    # UID SEARCH UID 42:* answers 41 (the newest), after_uid=41 -> []; and no UID FETCH was sent
def test_categories_are_added_from_a_search(fake): ...               # category:promotions matches uid 5 -> CATEGORY_PROMOTIONS
def test_large_lists_go_in_batches_of_500(fake): ...
def test_search_passes_the_query_and_keeps_the_newest(fake): ...
    # X-GM-RAW "newer_than:3d -in:spam" with quotes escaped; limit=2 of 3 -> the 2 highest UIDs
def test_a_non_ascii_query_is_refused(fake): ...
def test_get_message_downloads_with_peek_and_converts(fake): ...
    # exactly BODY.PEEK[]<0.2000000> in the items; result["id"], ["labelIds"], ["internalDate"] from the fetch
def test_a_message_gone_before_fetching_raises_message_gone(fake): ...
def test_nothing_outside_the_allow_list_reaches_the_server(fake):
    for command in ("STORE", "EXPUNGE", "COPY", "APPEND", "DELETE", "CREATE", "RENAME", "STATUS"):
        with pytest.raises(PermissionError):
            box._send(command, "1")
    with pytest.raises(PermissionError):
        box._send("UID FETCH", "1", "(BODY[])")   # marks mail as read
    with pytest.raises(PermissionError):
        box._send("UID STORE", "1", "+FLAGS", "(\\Deleted)")
    assert not any(c.split()[0] in ("STORE", "EXPUNGE") for c in fake.commands)
@pytest.mark.parametrize("text, raised", [("[OVERQUOTA] x", GmailBusy), ("[THROTTLED] x", GmailBusy),
    ("Too many simultaneous connections", GmailBusy), ("[UNAVAILABLE] x", ConnectionError)])
def test_gmail_trouble_is_translated(fake, text, raised): ...
def test_close_survives_a_dead_connection(fake): ...
def test_the_fingerprint_changes_with_the_saved_password(): ...      # None, then a hash holding no part of it, then another
```

- [ ] **Step 2: Run them to confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite, then mutation-check:**
  - drop the allow-list check;
  - allow `BODY[]`;
  - `readonly=False`;
  - keep UIDs ≤ `after_uid`.

  Each must make a test fail.
- [ ] **Step 5: Commit:** `git commit -m "Add a read-only Mailbox over IMAP"`

---

### Task 4: First contact with the real Gmail

This checks the fake against the real server before the engine depends on it. It needs the owner, costs nothing, and calls no model.

**Files:**
- Modify: `src/assistant/cli.py` (`assistant auth`, and a sign-in test after `set-key gmail`), possibly `src/assistant/gmail_imap.py` and `tests/fake_imap.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Produces: `cli.check_gmail(cfg) -> None`. It opens the mailbox and prints `Signed in to Gmail as <address>. All Mail is open, read-only.` `assistant auth` runs it, and so does `assistant set-key gmail` after saving. On refusal, `set-key` says the password was saved but Gmail refused it.

- [ ] **Step 1: Write the failing tests**

```python
def test_auth_signs_in_and_opens_no_browser(cfg, fake, capsys): ...   # "Signed in to Gmail as me@gmail.com" in out
def test_set_key_gmail_tests_the_sign_in(cfg, fake, capsys): ...
def test_set_key_gmail_says_when_gmail_refuses(cfg, fake, capsys): ...
```

- [ ] **Step 2: Run them to confirm they fail. Step 3: Implement, and run the full suite.**
- [ ] **Step 4: The owner's steps.**
  1. Add `address = "<your Gmail address>"` under `[gmail]` in `config.toml`.
  2. Create an App Password at <https://myaccount.google.com/apppasswords>, named `Pocket Assistant`.
  3. Run `.venv/Scripts/assistant.exe set-key gmail` and paste it into the **hidden prompt**. Never into chat.
- [ ] **Step 5: Probe the real wire format.** A scratchpad script opens the mailbox, lists the last 3 UIDs, and prints only each `MailRef`'s id and labels, then one `get_message`'s id, `labelIds`, `internalDate` and the header **names**. No subjects, senders or text.
  Expected:
  - the ids look like stored hex ids, and **one is already in `mail_items`**;
  - the labels map as planned;
  - `internalDate` is plausible.

  Any difference from `FakeImap`: change the fake to match reality first, watch the test fail, then fix `Mailbox`.
- [ ] **Step 6: Commit:** `git commit -m "Sign in to Gmail over IMAP from the CLI"`

---

### Task 5: Reading mail through `Mailbox`

**Files:**
- Create: `tests/fake_mailbox.py` (replaces `tests/fake_gmail.py`, deleted here)
- Modify: `src/assistant/ingest.py`, `src/assistant/engine/gmail_feed.py`, `src/assistant/cli.py` (`sync_mail`, `link_audit`)
- Test: `tests/test_gmail_feed.py`, `tests/test_ingest.py`, `tests/test_cli.py`, `tests/test_engine_jobs.py`, `tests/test_engine_runtime.py`

**Interfaces:**
- Consumes: `Mailbox`, `MailRef`, `Bookmark`, `MessageGone`, `open_mailbox` (Task 3).
- Produces:
  - `ingest.ingest_ids(store, mailbox, ids, cfg, my_addresses, log=print, skip_labels=SKIP_LABELS)`: the same contract, using `mailbox.get_message` and catching `MessageGone`.
  - `ingest.search_sync(store, mailbox, cfg, days, my_addresses, log=print, limit=None)`
  - `gmail_feed.check_mail(store, mailbox, cfg, *, now_utc, log=print) -> CheckResult`, with `mode: Literal["feed", "search"]`.
    - **Normal:** the bookmark is valid when `imap_uidvalidity` equals `mailbox.bookmark().uidvalidity`. `refs = mailbox.list_new(last)`, then drop refs hitting `SKIP_LABELS | query_exclusions(...)[0]`, then `ingest_ids`. The new `imap_last_uid` is the highest UID listed, dropped refs included, or `last` when none.
    - **Fallback:** no bookmark, or a changed `UIDVALIDITY`: take `mailbox.bookmark()` **first**, then `search_sync(days, limit=FALLBACK_LIMIT)`.
    - **On success:** set `imap_uidvalidity`, `imap_last_uid`, `gmail_address` (from `mailbox.address`), `last_ok_check_at`; clear `last_check_error` and `gmail_history_id`.
  - `HistoryExpired`, `fetch_new_ids`, `profile` and `RESCUE_LABELS` are removed.
  - `tests/fake_mailbox.py`: `FakeMailbox(messages, address="me@x.com", uidvalidity="1")`, taking `test_parser.make_message` dicts, with UIDs 1, 2, 3…; `.add(message)`, `.delete(id)`, `.reset_uidvalidity()`, `.calls`, `.queries`, a context manager.

- [ ] **Step 1: Rewrite the tests against `FakeMailbox`, and add:**

```python
def test_check_mail_uses_the_feed_when_it_has_a_bookmark(cfg): ...
    # bookmark ("1", 1); messages at uids 1,2,3 -> CheckResult(new=2, mode="feed"); "search" not in calls; imap_last_uid == "3"
def test_a_changed_uidvalidity_searches_instead(cfg): ...
def test_the_fallback_takes_its_bookmark_before_searching(cfg): ...   # calls.index("bookmark") < calls.index("search")
def test_the_fallback_search_is_not_cut_off_at_the_cli_limit(tmp_path): ...  # kept from M1's review: max_messages=2, 3 messages -> new=3
def test_a_first_check_without_a_bookmark_searches_7_days(cfg): ...
def test_a_message_deleted_before_fetch_is_skipped_and_the_bookmark_still_advances(cfg): ...
def test_skipped_labels_move_the_bookmark_but_download_nothing(cfg): ...      # SENT / SPAM refs: no get_message call; last_uid moves
def test_the_feed_honours_label_exclusions_in_the_configured_query(tmp_path): ...  # -category:promotions
def test_a_connection_lost_midway_keeps_the_saved_mail_and_the_bookmark(cfg):      # Review Focus 2
    # get_message raises OSError on the 2nd of 3 -> the exception propagates; the 1st is stored;
    # imap_last_uid is unchanged; the next check stores the other two and fetches nothing twice
def test_stored_emails_are_not_downloaded_again(cfg): ...             # search finds a stored id: no get_message for it
def test_the_old_history_bookmark_is_cleaned_up(cfg): ...             # gmail_history_id set before -> None after
```

- [ ] **Step 2: Run them to confirm they fail.**
- [ ] **Step 3: Implement.** `sync_mail` and `link_audit` use `with open_mailbox(cfg) as mailbox`. `CheckMailJob` is untouched in this task: `FakeMailbox` satisfies the same factory, and the test fakes that raised from `users()` now raise from `list_new`.
- [ ] **Step 4: Run the full suite, then mutation-check:** set the bookmark before the search's failure point; move the bookmark when a fetch failed; ignore `UIDVALIDITY`.
- [ ] **Step 5: Commit:** `git commit -m "Read mail through Mailbox, on a UID bookmark"`

---

### Task 6: The engine on `Mailbox`

**Files:**
- Modify: `src/assistant/engine/jobs.py`, `src/assistant/engine/runtime.py`
- Test: `tests/test_engine_jobs.py`, `tests/test_engine_runtime.py`

**Interfaces:**
- Consumes: `open_mailbox`, `token_fingerprint`, `GmailBusy` (Task 3).
- Produces:
  - `CheckMailJob(store, gmail, cfg, bus, scheduler, log=print, gmail_token=lambda: None)`. `gmail()` returns a context-managed `Mailbox`, opened fresh for each check and always closed. `_service` and `_built_with` go.
  - `AUTH_RETRY = timedelta(minutes=60)`, `RECHECK = timedelta(minutes=1)`.
  - **After `auth_lost`:** each call compares `gmail_token()` with the value when access was lost. Unchanged and under 60 minutes: return `RECHECK`, contacting nothing. Changed, or 60 minutes passed: try again.
  - `_gmail_trouble(exc)`:
    - `GmailAuthError` → `"auth_lost"`;
    - `GmailBusy` → `"gmail_busy"`;
    - `OSError`, `imaplib.IMAP4.abort` → `"offline"`;
    - anything else → `None`.

    All `HttpError`, `RefreshError`, `httplib2` and `http.client` handling goes.
  - The log line and status for `auth_lost` name `assistant set-key gmail`.
  - `run_engine`: `gmail=lambda: open_mailbox(cfg)`, `gmail_token=gmail_imap.token_fingerprint`.

- [ ] **Step 1: Rewrite the job tests:**

```python
def test_each_check_opens_a_fresh_mailbox_and_closes_it(store, cfg): ...      # 2 checks: 2 opens, 2 closes, also when list_new raises
def test_a_rejected_password_is_retried_hourly_not_every_check(store, cfg): ...
    # job(NOW) == MIN; job(NOW + 5 * MIN) == MIN and opens == 1; job(NOW + 60 * MIN) tries again, opens == 2
def test_after_lost_access_a_new_password_is_picked_up_within_a_minute(store, cfg): ...
def test_gmail_limiting_backs_off_5_then_15_minutes(store, cfg): ...
@pytest.mark.parametrize("error", [OSError("x"), socket.timeout(), ssl.SSLError("x"), imaplib.IMAP4.abort("x"), ConnectionError("x")])
def test_a_lost_connection_counts_as_offline(store, cfg, error): ...          # 30 s, then 5 min, then 15 min
def test_any_other_imap_error_goes_to_the_scheduler(store, cfg): ...          # imaplib.IMAP4.error("odd") is raised
def test_the_engine_uses_the_password_fingerprint(tmp_path, monkeypatch): ... # run_engine passes gmail_imap.token_fingerprint
```

- [ ] **Step 2: Run them to confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite, then mutation-check:** retry every check after `auth_lost`; don't close the mailbox after an error.
- [ ] **Step 5: Commit:** `git commit -m "Run the engine's mail check on a fresh read-only connection"`

---

### Task 7: Remove OAuth, and rewrite the rule

**Files:**
- Delete: `src/assistant/gmail_client.py`, `tests/test_gmail_client.py`
- Modify: `pyproject.toml`, `src/assistant/config.py`, `src/assistant/cli.py`, `config.example.toml`, `README.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-11-03-imap-mail-access-design.md`, and every test building `Config(...)` positionally
- Test: `tests/test_gmail_imap.py` (append), `tests/test_cli.py` (append)

**Interfaces:**
- `Config.credentials_file` goes, so `Config`'s first positional argument becomes `gmail_query`. A leftover `credentials_file` key in `config.toml` is ignored.
- `check_gmail`, on success, deletes the old `gmail-oauth-token` keyring entry and prints `Removed the old Google sign-in.` when there was one. It runs on `auth` too, so it still happens if Task 4's `set-key` came before this task.
- Remove `google-api-python-client`, `google-auth-oauthlib` and `google-auth` from `pyproject.toml`.
- **`CLAUDE.md`:** replace the "Read-only mail" rule with the spec's section 4 text, with `gmail_client.Mailbox` written as `gmail_imap.Mailbox`. Change `assistant auth` in its commands if needed.
- **Spec:** replace `gmail_client.py` with `gmail_imap.py` in sections 3 and 4.
- **README:** replace the Google Cloud setup with the App Password steps: 2-Step Verification, the link, `[gmail] address`, `assistant set-key gmail`, `assistant auth`. Add how to revoke it, and that a Google password change ends it.

- [ ] **Step 1: Write the failing tests**

```python
def test_nothing_can_send_mail_or_uses_the_old_google_client():
    source = {p: p.read_text(encoding="utf-8") for p in (ROOT / "src").rglob("*.py")}
    for name in ("smtplib", "googleapiclient", "google.auth", "google_auth_oauthlib"):
        assert not [p for p, text in source.items() if f"import {name}" in text or f"from {name}" in text], name

def test_only_mailbox_touches_imap():
    users = [p.name for p in (ROOT / "src").rglob("*.py") if "imaplib" in p.read_text(encoding="utf-8")]
    assert sorted(users) == ["gmail_imap.py", "jobs.py"]   # jobs.py only names imaplib.IMAP4.abort

def test_a_good_sign_in_removes_the_old_google_token(cfg, fake, capsys): ...
def test_an_old_credentials_file_setting_is_ignored(tmp_path): ...
```

- [ ] **Step 2: Run them to confirm they fail.**
- [ ] **Step 3: Implement.** Then uninstall the three packages from `.venv`, so a leftover import can't hide.
- [ ] **Step 4: Run the full suite.** Expected: all pass with the Google libraries gone.
- [ ] **Step 5: Commit:** `git commit -m "Remove OAuth and the Google libraries; read-only now rests on Mailbox"`

---

### Task 8: Two-lens adversarial review

The spec requires it (section 6): mail access and secrets.

- [ ] **Step 1: Run a two-agent review workflow** over `git diff <branch base>..HEAD -- src tests`, with these lenses:
  1. **Security and read-only:** any path that could change the mailbox or send mail; whether the allow-list can be bypassed (`conn` reachable, a second connection, command injection through a search query or label); the password in errors, logs, process arguments, tracebacks or the database; old OAuth leftovers.
  2. **Correctness:** UID and `UIDVALIDITY` logic and every way mail could be missed or fetched twice; `imaplib` response parsing against its real formats; MIME conversion of real-world messages; error classification on flaky networks; timeouts during sleep; bandwidth.

  Reviewers write probes only in the scratchpad, against temporary databases and `FakeImap`. No network, no real keyring. Each finding is checked by two skeptics, as in M1.
- [ ] **Step 2: Fix each confirmed finding** with a regression test, and mutation-check each fix.
- [ ] **Step 3: Run the full suite.**
- [ ] **Step 4: Commit:** `git commit -m "Fix IMAP review findings"`

---

### Task 9: Live check on the real mailbox

These steps need the owner. Reading mail costs nothing; extracting it is estimated first and approved.

- [ ] **Step 1: Back up the database and sign in.** Copy `assistant.db` to `assistant.pre-imap.db`, and run `assistant auth`. Expected: signed in; the old Google sign-in removed.
- [ ] **Step 2: Catch up, and estimate.** `assistant sync --days 7`. Expected: emails stored before the switch are **skipped, not new**. Then `assistant extract --estimate`, report the count and cost, and wait for approval.
- [ ] **Step 3: Start the engine,** in a terminal tab the owner can see. Expected: extraction clears the queue.
- [ ] **Step 4: Time a new email.** The owner sends a test email through the university forward. Expected: ranked ≤ 150 s after Gmail received it.
- [ ] **Step 5: Unread stays unread.** Check that email in Gmail: it's still unread after the engine read it.
- [ ] **Step 6: Rescued mail.** Move an email to Spam, wait for a check, move it back, wait for a check.
  Expected: it's found by the feed (`check_mail: 1 new`, or skipped as stored, with no extra model call).
  If it isn't found, the spec's fallback (a daily search of the last 2 days) becomes a fix under this task, test-first.
- [ ] **Step 7: Stop with Ctrl+C.** Check that `engine.log` holds no subject, address or part of the password, and that `assistant costs` matches.
- [ ] **Step 8: Tidy up.** The owner can delete `credentials.json`, and remove `pocket-assistant` at Google's "Third-party apps" page. The Google Cloud project is no longer used.
- [ ] **Step 9: Record the results** in section 6 of the IMAP spec, then commit: `git commit -m "Record the IMAP live check"`
