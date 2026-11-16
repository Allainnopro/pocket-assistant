# IMAP mail access: rulings and deferred minors

Every decision the executor made on the owner's behalf while carrying out `2026-11-03-imap-mail-access.md`, in order, each with what it costs if wrong; then every review finding deferred as Minor. Copied from the plan's ledger before its workspace was deleted.

## Rulings

- Task 4: Ruling: a real Gmail search reports some emails under both category:primary and category:updates, so they carry both labels (probe, 2026-11-03: stored ids match, labels map, formats parse) — kept as Gmail reports it; only drop_promotions and -category: exclusions read these labels — cost if wrong: someone excluding -category:primary would also skip those emails.
- Task 5: Ruling: FakeMailbox takes uidvalidity in its constructor; the plan's .reset_uidvalidity() is not added — no test changes it mid-run — cost if wrong: one small method
- Task 5: Ruling: added test_a_skipped_email_at_the_end_still_moves_the_bookmark and test_a_search_lost_midway_saves_no_bookmark_so_the_next_check_searches_again beyond the plan's list — two mutants survived without them — cost if wrong: none
- Task 6: Ruling: _gmail_trouble returns None for PermissionError, ahead of the plan's OSError rule — Mailbox raises PermissionError when the read-only gate trips, and that must surface as a bug, never read as offline — cost if wrong: a Windows firewall block (WSAEACCES) reaches the scheduler log instead of showing offline
- Task 6: Ruling: the auth_lost log line and status detail carry the GmailAuthError text, rather than a fixed 'run assistant set-key gmail' — spec section 5 says a missing address must name the setting; the text is always our own wording — cost if wrong: none, no server text or password reaches it
- Task 7: Ruling: also added test_a_refused_sign_in_keeps_the_old_google_token, and CLAUDE.md's Secrets rule now names the App Password — a failed switch must not cost the old sign-in, and the rule should say where the password lives — cost if wrong: none
- Task 8: Ruling: fixing C0/C9/C19 TLS, C1/C10/C21 unreadable mail and pre-1970 dates, C20 per-message NO, C2/C11 HTML and header stalls, C3/C16 attached email in the body, C4/C13 fetch parsing, C12 LOGIN classification, C22 greeting, C23 charsets, C15a/C25 bookmark write order and account, plus Minors C5 (test the password before saving), C6 (gate refuses CR/LF and odd FETCH shapes), C7 (address shape), C8/C26 (CLI messages instead of tracebacks) — the Criticals and Importants by effect, and the Minors because each fix is a few lines — cost if wrong: more code to review
- Task 8: Ruling: deferring C14 (feed after a long gap), C15c/C24 (search index lag at a fallback), C17 (setup errors as gmail status), C18 (small MIME differences) and the old-mail-with-new-UID finding — every skeptic graded these Minor by effect; spend stays under the daily cap and each needs a rare trigger — cost if wrong: a few missed or extra emails in rare cases
- Task 8: Ruling: TLS uses truststore.SSLContext (Windows' store), not ssl.create_default_context — matches llm.py and lets Windows fetch a missing Google root itself; truststore was already installed and is now declared — cost if wrong: a sign-in failure at the live check, fixed by switching to certifi
- Task 8: Ruling: a test-only self-signed certificate and key are committed under tests/data — the loopback test needs a real TLS server and nothing can be generated at test time without new dependencies; the key protects nothing — cost if wrong: none
- Task 8: Ruling: header values are cut to 8 KB before decoding — a 200 KB To: stalled the old policy; recipients past 8 KB of one header are lost — cost if wrong: a university address beyond the 8 KB mark is missed
- Task 8: Ruling: an email forwarded inline (message/rfc822 with no attachment disposition or filename) is still read, as the Gmail API path did — university forwarding may wrap mail this way — cost if wrong: a bounce report's quoted mail reaches extraction, as before
- Task 8: Ruling: a refused email holds the bookmark for at most REFUSED_TRIES=3 checks (about 6 minutes), then is skipped with a 'gave up' log line — Gmail's per-message NO is usually brief, and one stuck email must not stop all mail — cost if wrong: an email refused for longer than 6 minutes is lost
- Task 8: Ruling: test_only_mailbox_touches_imap now allows cli.py and jobs.py to name imaplib.IMAP4.error and .abort only, instead of listing the files — the CLI must catch IMAP errors to print a message; any other imaplib use outside gmail_imap still fails the test — cost if wrong: none
- Final: Ruling: REFUSED_TRIES window is about 4 minutes, not 6 (gives up on the third check) — the earlier ruling's wording was off; 3 tries stands — cost if wrong: an email refused for over 4 minutes is skipped
- Final: Ruling: C14 feed after a long gap — stands as ruled; at ~12 emails/day a 3-week gap costs cents under the daily cap — cost if wrong: a few dollars after a months-long gap
- Final: Ruling: a refused email in fallback mode is skipped at once — intended and tested; fallback runs only at a first run, a UIDVALIDITY change or an account change — cost if wrong: one email lost in a rare window
- Final: Ruling: a non-ASCII [gmail] query is refused (ConfigError) — as the plan specifies; IMAP search can't send it — cost if wrong: none beyond the C17 minor
- Final: Ruling: a fresh TLS login every 2 minutes — spec decision B3; the live check ran clean — cost if wrong: Gmail rate limits, which back off as gmail_busy
- Final: Ruling: truststore revocation behaviour and a wrong PC clock reading as untrusted_network — Windows-dependent, not testable offline; the live sign-in worked — cost if wrong: a misleading status on a badly set clock
- Final: Ruling: LOGOUT on a dead socket may wait up to 30 s — on a check that already failed — cost if wrong: one slow failed check
- Final: Ruling: MAX_DEPTH=30 drops text nested deeper — real mail rarely passes 5 — cost if wrong: text of an absurdly nested email lost
- Final: Ruling: Latin-1 mislabelled as UTF-8 may decode via CP949 — the price of reading Korean mail correctly — cost if wrong: garbled text in rare Western mail
- Final: Ruling: All Mail's name sent as a LIST literal isn't handled — Gmail quotes folder names, including the Vietnamese one — cost if wrong: an AssistantError naming All Mail
- Final: Ruling: a normal 16-lower-case-letter Google password pasted by mistake is saved "not tested" — the shape match is very unlikely — cost if wrong: a refused sign-in at the next check, reported as auth_lost or offline
- Final: Ruling: M2 consumers of state keys and Config positions — handled when M2 merges master (spec section 8) — cost if wrong: merge conflicts already expected
- Final: Ruling: other pre-existing links.py/parser.py behaviour — unchanged by this branch; only the stall class was in scope — cost if wrong: none new
- Final: Ruling: header 8 KB cut, inline-forwarded rfc822, truststore, the committed test key, PermissionError ordering — the reviewer checked the stated costs and found them right — cost if wrong: as stated in each ruling
- Final: Ruling: the 300 KB per-email budget in extract_body is removed, and the 60-part timing case with it — with the scan linear it only saved about a second, and that case sat too close to its limit on CI; the 30,000-character cap in clean_text stays as a one-line bound — cost if wrong: a 2 MB crafted email takes about 1.3 s instead of 0.2 s

## Deferred minors

- Final: minor (deferred): test_only_mailbox_touches_imap misses 'from imaplib import' and 'import imaplib as'; the Task 8 ruling's "cost if wrong: none" is wrong — a second door added that way would pass the guard
- Final: minor (deferred): C17's cost is understated — All Mail hidden or a non-ASCII [gmail] query stops every check while the gmail status shows no problem; the console and log carry the full message; validating the query in load_config would surface it at start
- Final: minor (deferred): `assistant sync` says "Nothing changed" after a drop mid-run although mail read so far is kept
