# Email kinds: rulings and deferred minors

Copied from the execution ledger of `2026-11-18-email-kinds.md` before its workspace was deleted.

## Rulings

- Task 1: Ruling: added test_the_tuned_rules after tuning — the tuning step (plan Step 4) is checker-driven, so these pin what the labelled set taught; each passed on first run — cost if wrong: none
- Task 2: Ruling: the pipeline test "the extraction saves the AI's kind and urgency" moves to Task 3 — Extraction forbids unknown fields, so a reply can't carry email_kind before Task 3 adds it; Task 2 tests save_extraction's new params directly (test_opportunities: test_save_extraction_stores_the_ais_kind_and_urgency) and Task 2 needs no getattr bridge — cost if wrong: none
- Task 2: Ruling: test_v8_adds_devices_to_a_v7_database_and_keeps_its_rows now opens with MIGRATIONS[:8] — it checks v8, and "latest" is v9 now — cost if wrong: none
- Task 3: Ruling: the database backup moved from Task 6 to before the smoke call — llm smoke logs its cost in the real assistant.db, so it was the first v9 open; an online sqlite backup (v8, every row) first; the live db is v9 from then on while the dev engine still runs the old code (additive columns: its inserts leave the kind columns NULL, which the API shows as unsorted until the restart re-sorts) — cost if wrong: none, the backup restores v8
- Task 3: Ruling: synthetic_thinking_then_text's reply says email_kind "promo" (an empty reply), synthetic_ok's "hackathon" — cost if wrong: none
- Task 4: Ruling: test_important_emails_are_the_ones_that_matter was replaced by the plan's new tests, and test_each_email_shows_why_it_matters now expects no "university" badge — they pinned the old Important (opportunity/university/needs-action) the owner's option B replaces — cost if wrong: none
- Task 4: Ruling: test_the_email_list_says_when_it_is_cut_off lists Important now (an unknown sender's newsletter is unsorted, so visible); Today's row keys gain kind and urgent — cost if wrong: none
- Task 4: Ruling: urgency's three days are 72 hours from received_at (UTC), not HCMC calendar days — simpler, and the spec's "three days from arrival" reads that way — cost if wrong: an urgent email's red ends up to a few hours earlier or later than a calendar reading
- Task 5: Ruling: KindDot falls back to Unsorted for a kind it doesn't know (test "a kind the dashboard doesn't know shows as unsorted…", RED→GREEN) — an engine of another version (the laptop viewer in M4) must not crash a page — cost if wrong: one line
- Task 5: Ruling: the legend lists only kinds (in KIND_ORDER) present in the current list, without an "Urgent" entry; its dots are aria-hidden beside their words; the Ctrl+K palette shows no dots (left to the builder; skipped) — cost if wrong: the red's meaning is learned from hover ("Urgent · …")
- Task 5: Ruling: CSS tokens use hyphens (--kind-uni-admin) while keys use underscores — CSS naming — cost if wrong: none
- Final: Ruling: Today's needs-action rows use VISIBLE_SQL without Important's injection-flag clause — an injected "act now" shouldn't headline Today — cost if wrong: a flagged action email shows only in Emails
- Final: Ruling: a grey email with an opportunity stays in Other (spec, option B) and subject-word urgency on strangers' "URGENT:" mail stands (spec: either flag makes it urgent) — cost if wrong: occasional red on a spammy subject

## Deferred minors

- rows stored with NULL rule columns after the version stamp (old code writing later) stay Unsorted until the next rules bump
- all github.com mail is Accounts for sure, including invitations that are not account mail
- "học bóng rổ" (a ball sport) folds to "hoc bong" and is sorted Scholarships for sure
- a kind string this engine doesn't know makes /api/emails and /api/today fail validation (500)
- mail forwarded by hand from one of the owner's own addresses is Accounts for sure
- the email pane and the badges still crash on data from another engine version (KindDot alone falls back)
- urgent mail that doesn't need action isn't added to Today (Today stays the needs-action list; urgency only lifts its grey filter)

## Task and review record

- Task 1: complete (commits 87b42ec..2412744, tests: ../../.venv/Scripts/python.exe -m pytest -q → 962 passed in 40.02s)
- Task 2: complete (commits 2412744..25d65c5, tests: ../../.venv/Scripts/python.exe -m pytest -q → 967 passed in 39.36s)
- Task 3: complete (commits 25d65c5..8809bcf, tests: ../../.venv/Scripts/python.exe -m pytest -q → 972 passed in 40.16s)
- Task 4: complete (commits 8809bcf..c3ba0ec, tests: ../../.venv/Scripts/python.exe -m pytest -q → 977 passed in 39.84s)
- Task 5: complete (commits c3ba0ec..219dad7, tests: npm --prefix app run verify → ✨ openapi-typescript 7.13.0)
- Task 6: complete (commits 219dad7..219dad7, tests: ../../.venv/Scripts/python.exe -m pytest -q → 977 passed in 39.91s)
- Final review: workflow wf_c7d82132-c71 (3 lenses on opus + 1 skeptic): 12 confirmed (3 important, 9 minor), 5 rejected
- Final: re-graded "known senders return sure grey before hackathon/scholarship words" minor → important (a registered hackathon's next-steps mail hidden, no AI override) and "pre-filtered mail lands in Important as Unsorted" minor → important (every OTP, bank notice and owner-dropped sender surfaces in Important)
- Final: fixed Ed course mail with an account word sorted Accounts for sure — ED_SIGN_IN only; test_course_mail_from_ed_that_mentions_verifying_stays_a_course RED→GREEN
- Final: fixed false urgent reds (khó khăn, khán giả, Khan, non-urgent, khăn) — KHẨN only in capitals or with cấp/trương/thông báo, non-urgent excluded; test_not_urgent cases RED→GREEN
- Final: fixed LinkedIn person messages filed Social for sure — "(via LinkedIn)" senders go to the AI (jobs guess with job words, else unsorted); interview/phỏng vấn/ứng tuyển/job offer added to JOBS; test_a_linkedin_message_from_a_person_goes_to_the_ai RED→GREEN
- Final: fixed hackathon/scholarship mail from known senders hidden — hackathon words before onboarding, google.com and promo domains check hackathon/contest/scholarship first; test_hackathon_and_scholarship_words_beat_welcome_and_promo_senders RED→GREEN
- Final: fixed pre-filtered mail shown as Unsorted in Important — KIND_SQL takes the filter's verdict (codes/bank → accounts, owner rules/promotions → promo); test_mail_the_pre_filter_dropped_is_grey_not_unsorted RED→GREEN; suites 990/990 and 148/148; checker sure on most of the labelled set, sure-grey wrong 0; RULES_VERSION 2
