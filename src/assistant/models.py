"""Domain objects shared by every layer.

Why a dataclass instead of passing Gmail's raw dicts around?
The raw Gmail format is deeply nested and Gmail-specific. Converting it ONCE
into a flat, typed object means the brain (Phase 1) and the companion (Phase 2)
never need to know Gmail exists. If we later add Outlook via Graph or an RSS
feed, each collector just produces the same MailItem shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Every kind of item the brain can extract. One list, shared by profile.toml
# validation, the extraction schema and the opportunities table.
OPPORTUNITY_KINDS = (
    "hackathon", "ideathon", "competition", "internship", "job", "job_fair",
    "seminar", "uni_notice", "other",
)


@dataclass(frozen=True)
class MailItem:
    message_id: str          # Gmail's stable id: our dedupe key
    thread_id: str
    received_at: str         # ISO 8601, UTC
    sender: str              # "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    sender_domain: str       # "linkedin.com"
    to_me: bool | None       # one of MY addresses in To/Cc? None = unknown (stored before Phase 1)
    recipient_count: int     # addresses in To/Cc: 1 = personal, 45 = class-wide. The list itself
                             # (classmates' names and emails) is never stored.
    subject: str
    snippet: str             # Gmail's short preview text
    body_text: str           # plain text, truncated
    source: str              # "linkedin", "devpost", "university", "other", ...
    labels: list[str] = field(default_factory=list)
