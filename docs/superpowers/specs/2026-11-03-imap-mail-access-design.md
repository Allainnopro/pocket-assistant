# Pocket Assistant: mail access through IMAP

**Status:** design approved by the owner in conversation on 2026-11-03. This spec is waiting for the owner's review.
**Changes:** decision A3 and owner task 1 of `2026-10-25-desktop-app-redesign-design.md`, and the "Read-only mail" hard rule in `CLAUDE.md`.

## 1. Why

The Google Cloud app is stuck in Testing mode: **Publish app** stays greyed out, so Google ends the Gmail sign-in every 7 days. The owner wants that weekly chore gone.

### The owner's decision

The owner chose IMAP with a Google App Password, after being told what it costs:

| | OAuth `gmail.readonly` (before) | IMAP + App Password (now) |
|---|---|---|
| Sign-in | Weekly, about a minute | Once; lasts until revoked or the Google password changes |
| What the key can do | Read only, enforced by Google | Full mailbox access, including deleting, and sending mail |
| Google's advice | The recommended route | "App passwords aren't recommended and are unnecessary in most cases" |

Read-only stops being enforced by Google. It becomes a promise kept by this project's code (section 4).

### What stays the same

- The 2.5-minute target from new email to ranked item, and the 2-minute check.
- What is stored, the 7-day body purge, and every other privacy rule.
- The PC is the only computer holding the secret. The laptop never does.
- Everything after "here is an email": the parser's rules, the queue, extraction, ranking.

### Success criteria

| Measure | Target |
|---|---|
| Sign-ins needed in normal use | None after the first |
| New email in Gmail → ranked | ≤ 150 s |
| An unread email the engine reads | Still unread in Gmail |
| Emails already stored | Not fetched or billed again |
| Commands the code can send to Gmail | Only the allowed read commands (section 4) |

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| B1 | **Keep the 2-minute check and swap only the connection.** Rejected for now: IMAP IDLE (push), which needs an open connection that survives sleep and can be added later. Rejected: supporting OAuth and IMAP side by side, twice the code for no gain. | The smallest change; everything downstream keeps its tests. |
| B2 | **Python's built-in `imaplib`.** Rejected: `imap_tools`. | It adds no dependency, and the code uses a few commands plus Gmail's extensions. |
| B3 | **A fresh connection per check, with a 30-second socket timeout.** | No connection to keep alive through sleep or dropped Wi-Fi, and a stuck server can't stall the scheduler. |
| B4 | **Emails are converted to the Gmail-API shape the parser already reads.** | `parser.py` stays pure and its tests stay valid. |
| B5 | **The Google libraries, `credentials.json` and the OAuth flow are removed.** | Nothing uses them any more. |

## 3. How it works

### Units

| Unit | Does | Used through |
|---|---|---|
| `gmail_imap.py` (replaces `gmail_client.py`) | `open_mailbox(cfg) -> Mailbox`, and the `Mailbox` read commands below | `gmail_feed`, `ingest`, `cli` |
| `gmail_message.py` (new, pure) | `to_api_shape(raw: bytes, *, message_id, thread_id, labels, internal_date_ms) -> dict`: one email in the shape `parser.build_mail_item` reads | `Mailbox.get_message` |
| `vault.py` | New secret kind `gmail` (section 3, "The password") | `set-key`, `open_mailbox` |
| `engine/gmail_feed.py` | `check_mail`, on a UID bookmark instead of a history id | `CheckMailJob` |

### `Mailbox`

- `address: str`, lower-cased, from `[gmail] address`.
- `bookmark() -> Bookmark(uidvalidity, last_uid)`: where All Mail stands now.
- `list_new(after_uid) -> list[MailRef]` and `search(query, limit) -> list[MailRef]`, where `MailRef` holds `id`, `uid` and `labels`, without downloading any body.
- `get_message(message_id) -> dict`, raising `MessageGone` when it no longer exists.
- `close()`.

### Connecting

- `imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)`, then `LOGIN`.
- Find All Mail with `LIST`, by its `\All` attribute, because the folder's name depends on the account's language.
- Open it read-only (`EXAMINE`, through `select(folder, readonly=True)`).

### Ids stay the same

Google's IMAP docs: `X-GM-MSGID` and `X-GM-THRID` are the decimal form of the hex ids in the Gmail API. Converted back to hex, they match every stored `message_id`. So stored mail is not fetched again, and "Open in Gmail" links keep working.

### Finding new mail

- **The bookmark** is the state keys `imap_uidvalidity` and `imap_last_uid`, for All Mail. `gmail_history_id` is deleted.
- **A normal check:** list UIDs above `imap_last_uid`, read their ids and labels, skip the skip-labels and what is already stored, fetch the rest, save the mail, then move the bookmark.
- **The fallback:** with no bookmark, or when `UIDVALIDITY` changed, take the bookmark **first**, then search with `X-GM-RAW` and the `[gmail] query`, over `min(days since the last good check, 7)` days. Search results keep the 5,000-message bound.
- **An IMAP detail:** `UID n:*` always returns the newest message, even when `n` is past it. UIDs at or below the bookmark are dropped.
- **Rescued mail.** A message moved out of Spam or the bin appears in All Mail with a new, higher UID, so the normal check finds it. The live check confirms this on the real mailbox.
- **Spam and the bin** aren't in All Mail at all.

### Labels

- `X-GM-LABELS` maps to the labels the code already uses: `\Inbox` → `INBOX`, `\Sent` → `SENT`, `\Draft` and `\Drafts` → `DRAFT`, `\Important` and `Important` → `IMPORTANT`, `\Starred` → `STARRED`. Other labels stay as they are.
- **Inbox categories** aren't confirmed to come through `X-GM-LABELS`. When a check has new mail, one `UID SEARCH UID <range> X-GM-RAW "category:<name>"` per category sets `CATEGORY_PERSONAL`, `_SOCIAL`, `_PROMOTIONS`, `_UPDATES` and `_FORUMS`. Gmail's search can lag a just-arrived email by moments, so a promotion can occasionally miss its label and be read at a fraction of a cent.

### Downloading one email

- `UID FETCH <uid> (BODY.PEEK[]<0.2000000> X-GM-MSGID X-GM-THRID X-GM-LABELS INTERNALDATE)`.
- **`BODY.PEEK`** doesn't mark the email as read.
- **The first 2 MB only.** Text comes before attachments, the parser keeps 4,000 characters anyway, and Gmail's limit is 2,500 MB of IMAP downloads a day.

### Converting

`to_api_shape` uses Python's `email` module with the modern policy:

- Headers are decoded (`=?UTF-8?B?...`), as the Gmail API returns them.
- Only `text/plain` and `text/html` parts are kept, as base64url data, decoded with their declared charset and errors replaced. A truncated last part is fine.
- The **snippet**, which IMAP doesn't provide, is the first 200 characters of the email's text. The parser cleans it as it does Gmail's.

### The password

- **Saving:** `assistant set-key gmail`, with a hidden prompt or `--stdin`. Spaces are removed, since Google shows the password in groups of four, and the shape must be 16 lower-case letters. It's stored in Windows Credential Manager under `gmail-app-password`, then a sign-in is tested at once.
- **Your address:** `[gmail] address` in `config.toml`. It is optional at load, so `stats` and `list` still work; anything reaching Gmail without it stops with a message saying what to set.
- **`assistant auth`** no longer opens a browser. It signs in with the saved password and reports the result.
- **Cleaning up:** the first successful `set-key gmail` deletes the old `gmail-oauth-token` entry from the keyring.
- **A new password is picked up by the engine** at its next check, with no restart.

## 4. Keeping it read-only

The guarantee, in order:

1. **`Mailbox` is the only code that touches IMAP**, and it sends commands through one allow-list: `LOGIN`, `LIST`, `EXAMINE`, `UID SEARCH`, `UID FETCH`, `LOGOUT`. Anything else raises before reaching the network.
2. **`UID FETCH` is limited too:** only the data items above, which never change a message. The fake connection in the tests fails on any other command, or on a fetch without `BODY.PEEK`.
3. **The folder is opened read-only,** so the server itself refuses changes on that connection.
4. **No code can send mail.** A test fails if `smtplib` is imported anywhere under `src/`.
5. **The AI has no route to the mailbox.** The model has no tools, and its output is validated JSON that only becomes stored opportunities.

**The new `CLAUDE.md` rule, replacing "Read-only mail":**

> **Read-only mail, enforced in our code.** The owner chose an App Password over OAuth on 2026-11-03 to end the weekly sign-in, so Google no longer enforces read-only: the password has full mailbox access. Only `gmail_imap.Mailbox` touches IMAP, through its command allow-list, in a folder opened read-only, with `BODY.PEEK`. The code never sends, deletes, or modifies mail, and never imports `smtplib`. Any future action (calendar insert, draft reply) requires an explicit user click.

**If the password ever leaks:** delete it at [Google App Passwords](https://myaccount.google.com/apppasswords), which ends it at once, then create a new one and run `assistant set-key gmail`.

## 5. Errors

| What happens | How it's recognised | What the engine does | What you see |
|---|---|---|---|
| Wrong or revoked password, including after a Google password change | `LOGIN` fails with `AUTHENTICATIONFAILED` | `auth_lost`. Looks every minute for a new saved password; tries the same one again only hourly, so repeated failures can't get the account locked | "Gmail access lost; run `assistant set-key gmail`" |
| No network, DNS, timeout, connection dropped | `OSError`, `socket.timeout`, `ssl.SSLError`, `IMAP4.abort` | Retry in 30 s, then 5, then 15 minutes | `offline` |
| Gmail limiting | `OVERQUOTA`, `THROTTLED`, "Too many simultaneous connections" | Retry in 5, then 15 minutes | `gmail_busy` |
| Gmail temporarily unavailable | `UNAVAILABLE` | Same as no network | `offline` |
| A message vanishes between listing and fetching | The fetch returns nothing | Skip it; the bookmark still advances | Nothing |
| `[gmail] address` or the password is missing | Before connecting | `auth_lost`, with what to set | The same message, naming the setting |
| Anything else | | Goes to the scheduler as a job error | Logged by type |

Logs still hold ids and reasons only. The password is never logged, and no error message includes it.

## 6. Testing

- **`to_api_shape`:** encoded subjects, quoted-printable Vietnamese, multipart, HTML-only, a wrong charset, a message truncated at 2 MB.
- **`Mailbox`, against a fake `imaplib` connection:** every response parsed; ids converted to hex; the labels map; the allow-list; `BODY.PEEK`; `EXAMINE`.
- **`check_mail`, against a `FakeMailbox`:** today's feed tests, rewritten on UIDs, plus a `UIDVALIDITY` change.
- **Every engine rule from M1's reviews** keeps its test: the bookmark taken before a search, the search not cut at `max_messages`, skipped labels, a message gone before fetching, a new sign-in picked up.
- **Mutation checks** on the allow-list, `BODY.PEEK`, read-only opening, and bookmark order.
- **Offline, as ever:** no test reaches Gmail.
- **The two-reviewer adversarial review** runs, because this is mail access and secrets.

**Live check on the real mailbox (the owner, with an approved extraction estimate):**
1. `assistant set-key gmail`, then `assistant auth`.
2. Start the engine; it catches up on everything since the last run, at most 7 days.
3. Time a new email to its ranked item (≤ 150 s).
4. An unread email stays unread after the engine reads it.
5. Move an email to Spam, then back: it's found again and isn't billed twice if it was stored.
6. Run the privacy check on `engine.log`, as in M1, including that the password isn't there.

## 7. Risks

| Risk | Mitigation |
|---|---|
| A leaked password opens the whole mailbox | Stored only in Credential Manager on the PC; never logged; deleted at Google in one click (section 4) |
| A future change adds a write command | The allow-list and its test, plus the `CLAUDE.md` rule |
| Google restricts App Passwords or IMAP later | All mail access sits behind `Mailbox`, so the Gmail API can return without touching anything else |
| Rescued mail keeps its old UID, against expectation | Found at the live check. The fallback would be a daily search of the last 2 days |
| Changing the Google password stops mail checks | Reported as `auth_lost`, with the command to run |

## 8. Order of work

1. This switch, on branch `imap-mail` off `master`: its own plan, then merged to `master`.
2. M2 resumes on `m2-local-api` with `master` merged in, at its Task 7. Task 7's `signed_in` and Task 12's setup notes change to match.

## 9. Out of scope

- IMAP IDLE (push), which can come later.
- Any write to the mailbox.
- Mail from anywhere but this one Gmail account.
