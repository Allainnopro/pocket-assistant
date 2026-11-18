# Email kinds: a coloured dot per email, and the noise out of Important

Part of the M3 dashboard feedback round (2026-11-18). Builds on `2026-11-17-m3-dashboard-design.md` (Emails view,
Today) and the engine as it stands on branch `m3-dashboard`.

## Why

The owner wants a small coloured dot per email showing its kind, so emails can be told apart at a glance, and less
unnecessary news. University mail is the hard part: it varies a lot, and one university office can send scholarships,
job fairs, seminars, concerts and surveys from the same address.

A sort of stored mail (sender names, subjects and previews, read by three agents and a checker) gave ten kinds. Four
of them are noise (Social, Newsletters & promos, Accounts & system, Uni mass mail): much of a student's mail is noise,
and the mail worth seeing is easily buried under it.

## What the owner decided

- **The ten kinds and their colours** below, with red as a flag for urgent mail on top of any kind (owner's colours:
  red urgent, green news, light blue subjects and the program office, orange hackathons, yellow jobs; the rest proposed and shown
  to the owner without objection).
- **Option B:** emails of the four grey kinds leave Important and go to Other automatically; nothing is deleted.
  Important keeps the six coloured kinds plus anything urgent.
- **Both (hybrid) sorting:** rules settle the clear-cut emails for free; the AI that already reads each new email
  decides what the rules can't (mostly the mixed university mail), inside the call it already makes.

| Key | Name shown | Colour | Covers | In Important |
|---|---|---|---|---|
| `study` | Subjects & program | light blue | course platforms (Ed, Canvas), lecturers, and the degree program's office, its forwards included | yes |
| `uni_admin` | Uni admin | violet | university admin aimed at the student personally: health checks, insurance, required surveys | yes |
| `hackathon` | Hackathons & contests | orange | hackathons and tech or startup contests, from contest platforms or the university | yes |
| `jobs` | Jobs & internships | yellow | job alerts and hiring posts, recruitment, career talks, job fairs | yes |
| `scholarship` | Scholarships | pink | scholarship calls and student awards (học bổng, giải thưởng) | yes |
| `news_events` | Uni news & events | green | useful university news: seminars, webinars, conferences, exchange, mentoring, club and faculty events | yes |
| `uni_mass` | Uni mass mail | grey | low-value university bulk mail: concerts, sports events, outside surveys, contests outside the student's field | no |
| `social` | Social | slate | social networks and chat apps: mentions, invitations, feed posts, contact suggestions | no |
| `promo` | Newsletters & promos | sand | product marketing, newsletters, onboarding and welcome mail | no |
| `accounts` | Accounts & system | silver | security alerts, sign-in codes, verification, privacy and data notices, mail from the owner's own addresses | no |

## Goals

- Every stored email has exactly one kind, and new mail gets its kind without any extra paid call.
- The Emails list and Today show a dot in the kind's colour (red when urgent), with the kind's name on hover and for
  screen readers; the email pane names the kind.
- Important shows only the six coloured kinds (and Unsorted), urgent mail and prompt-injection flags; Other shows the
  rest. The sidebar's Emails count follows.
- Today's "For you today" email rows leave out the grey kinds.
- Old mail (bodies already deleted after 7 days) is sorted by the rules.

## Non-goals

- Filtering or searching by kind, per-kind switches in Settings (option C; later if a kind lands wrong), Telegram
  alerts by kind (M6), editing rules from the dashboard.
- A paid run to re-sort old mail with the AI.

## Anti-goals (a failure even if it works)

- **A useful email hidden in Other.** Missing a real scholarship or seminar is worse than seeing a concert. Rules may
  send mail to a grey kind only when they are certain; whenever the rules aren't sure and the AI has no answer, the
  email lands in a visible kind.
- **Urgent mail out of sight.** Urgent mail is in Important whatever its kind.
- **The owner's mail leaving the PC for this feature.** The labelled set of real subjects stays on the PC, in the
  git-ignored `.local/email-labels/` (added to `.gitignore`), and is deleted once the sorter is built; the public
  repo's tests use made-up examples only.

## Constraints

- Read-only mail and the privacy rules stand: no subjects or addresses in logs; the AI sees nothing it doesn't already.
- Paid calls need the owner's approval each time: changing the AI's instructions means re-recording the smoke fixture
  (`tests/fixtures/llm/recorded_smoke_ok.json`, about $0.0006) — ask first.
- Schema changes go through a numbered migration (next is v9); the API schema is regenerated (`npm run gen:api`,
  checked by `check:api`).
- The dashboard's colour guards stand: raw colours live only in `index.css` and `tokens.ts`. The kind colours are a
  deliberate exception to the five-colour palette, used only for the dots (and their legend).

## Approaches (owner picked the third)

1. **Rules only** (sender, domain, subject words): free, instant, sorts old mail too; can't tell the university's useful
   bulk mail from its noise, so with option B some useful mail would sink into Other.
2. **AI only** (the extraction call names the kind): best on mixed mail; new mail only, every sort costs a call, and
   emails the free pre-filter drops (codes, security alerts) never get a kind.
3. **Both** — chosen: rules decide when they're certain; otherwise the AI's answer from the call it already makes;
   otherwise a visible fallback.

## How it works (decided)

**Sorting, in order, for each email:**
1. **Rules, certain:** known senders and unmistakable words decide (e.g. `edstem.org`, `instructure.com`, the
   owner's study domains and a sender name holding one of the owner's study sender words → `study`;
   `facebookmail.com`, `discord.com`, LinkedIn mail that is not a job alert → `social`; "LinkedIn Job Alerts" or "is
   hiring" → `jobs`; "học bổng" → `scholarship`; the account providers' security mail and the owner's own address →
   `accounts`; the university health office as sender, "bảo hiểm y tế", "khám sức khỏe" → `uni_admin`). The owner's
   own sender lists come from `config.toml [kinds]` (`study_domains`, `study_sender_words`, `hackathon_domains`,
   `hackathon_words`, `promo_domains`, `accounts_domains`), never from the code; the built-in tables keep only generic platforms
   (`edstem.org`, `instructure.com`, `facebookmail.com`, `discord.com`, `linkedin.com`, `devpost.com`, `kaggle.com`,
   the account providers, `google.com`). The full table is the builder's, tuned against the
   local labelled set (below).
2. **The AI:** the extraction call adds two answers for the whole email: `email_kind` (one of the ten keys) and
   `urgent`. Used when the rules weren't certain.
3. **Fallback:** the rules' best guess, which never picks a grey kind for mail the rules weren't sure about: university
   bulk mail without a clear word falls back to `news_events`; any other mail nothing could sort gets an eleventh,
   visible kind, `unsorted` ("Unsorted", a hollow mist dot, in Important), because a stranger's email isn't university
   news.

**Urgent:** rules flag explicit markers in the subject (KHẨN, BẮT BUỘC, "Action required", "urgent"); the AI flags mail
that asks the owner to act within about three days or that is mandatory. Either one makes it urgent. Urgency lasts
three days from the email's arrival (HCMC time): while it lasts the dot is red and the email is in Important and on
Today whatever its kind; after that the email sits wherever its kind puts it, with its kind's colour. The API sends
`urgent` already time-checked.

**A grey email with an opportunity or an action in it** (a LinkedIn post holding a job, a promo announcing an event)
still goes to Other, as option B says: the opportunity itself is listed on Opportunities either way, and anything the
owner must act on soon is urgent and stays visible. Today's "For you today" email rows follow the same rule as
Important.

**Engine:**
- A pure module (like `filters.py`): the kind keys, which are grey, `sort_by_rules(...)` returning the kind and whether
  the rules are sure (when not sure, the kind is the rules' visible guess, never grey), and `urgent_by_rules(subject)`.
- Migration v9 adds to `mail_items` both answers, kept apart so either can be redone: `rule_kind`, `rule_sure`,
  `rule_urgent`, `ai_kind` (null until the AI has read the email) and `ai_urgent`. It fills the rule columns for every
  stored email.
- The kind shown is decided when read, in one SQL expression: `rule_kind` if `rule_sure`, else `ai_kind`, else
  `rule_kind`. Urgent = (`rule_urgent` or `ai_urgent`) and received within three days.
- New mail gets its rule columns when stored; the extraction step always stores the AI's two answers. Mail the
  pre-filter drops never gets AI answers and keeps the rules' kind.
- Rule changes reach old mail: a rules version in the `state` table; when it changes, the engine refills the rule
  columns of every stored email (the AI's answers stay).

**API:** `EmailRow` (and the pane's detail, Today's needs-action rows) gain `kind` and `urgent`; `section=important`
means a coloured kind (or Unsorted), urgent, or injection-flagged; `other` is the rest. The "university" badge goes (the
dot says it); "opportunity", "needs action" and "addressed the assistant" stay.

**Dashboard:** a `KindDot` (8 px circle in the kind's colour, red when urgent; `title` and an accessible name like
"Scholarships" or "Urgent · Uni admin") before the sender on Emails rows, on Today's email rows, and beside the
subject in the email pane with the kind's name. The ten colours are tokens in `index.css` / `tokens.ts`.

**Checking the rules:** a local script (git-ignored, in `.local/`) compares the rules against the labelled set and
reports agreement per kind; the bar is that every grey decision the rules are sure of is right on that set. The set is
two git-ignored files in `.local/email-labels/`: one draft-kind label per email, and each email's sender name, domain,
subject and preview (addresses masked). Its labels use the sort's draft kind names; the builder maps them to the ten
keys, applying the checker's corrections for the borderline emails. The set and the script are deleted once the owner
is happy with the sorting.

## Left to the builder (my calls, so the owner can check them)

- The exact rule table and keyword lists, tuned against the labelled set.
- The eleventh `unsorted` kind for mail nothing could sort (my call, above), instead of calling it university news.
- Urgency lasting three days for Important and Today as well as for the red (my call, above).
- A small legend on the Emails page (dot and name for each kind in the current section).
- Exact hex values: from the sort's checker, adjusted so yellow doesn't read as the amber highlight and each dot keeps
  3:1 contrast on the near-black background: light blue #7DD3FC, violet #A78BFA, orange #FB923C, yellow #FACC15, pink
  #E879F9, green #4ADE80, grey #6B7280, slate #94A3B8, sand #C8A97E, silver #E5E7EB, urgent = the existing soft red.
- Whether the Ctrl+K search shows the dot on email results.
