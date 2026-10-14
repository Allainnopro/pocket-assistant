"""Domain objects shared by every layer.

Why a dataclass instead of passing Gmail's raw dicts around?
The raw Gmail format is deeply nested and Gmail-specific. Converting it ONCE
into a flat, typed object means the brain (Phase 1) and the companion (Phase 2)
never need to know Gmail exists. If we later add Outlook via Graph or an RSS
feed, each collector just produces the same MailItem shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MailItem:
    message_id: str          # Gmail's stable id: our dedupe key
    thread_id: str
    received_at: str         # ISO 8601, UTC
    sender: str              # "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    sender_domain: str       # "linkedin.com"
    recipients: str          # To + Cc, used to detect forwarded university mail
    subject: str
    snippet: str             # Gmail's short preview text
    body_text: str           # plain text, truncated
    source: str              # "linkedin", "devpost", "university", "other", ...
    labels: list[str] = field(default_factory=list)
