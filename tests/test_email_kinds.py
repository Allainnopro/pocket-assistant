"""The rule sorter: sure only for known senders and unmistakable words, never grey when unsure.
Every email and sender here is made up; the owner's own senders live in their config.toml, never in the repo."""

import unicodedata

import pytest

from assistant.email_kinds import GREY, KINDS, KindSenders, RuleKind, configure, rules_stamp, sort_by_rules, urgent_by_rules

ME = ("me@gmail.com", "me@uni.example.edu")
# What an owner's config.toml [kinds] might hold: their university, program office, hackathons and newsletters
OWNER = KindSenders(study_domains=("uni.example.edu",), study_sender_words=("program office",),
                    hackathon_domains=("hackathon.example.org",), hackathon_words=("example jam",),
                    promo_domains=("newsletter.example.com", "vendor.example.com"),
                    accounts_domains=("notify.vendor.example.com",))


@pytest.fixture(autouse=True)
def owner():
    configure(OWNER)
    yield
    configure(KindSenders())


def sort(sender: str, subject: str, source: str = "other") -> RuleKind:
    domain = sender.rsplit("@", 1)[-1].rstrip(">").lower() if "@" in sender else ""
    return sort_by_rules(sender, domain, subject, source, ME)


def test_the_eleven_kinds_and_the_grey_ones():
    assert KINDS == ("study", "uni_admin", "hackathon", "jobs", "scholarship", "news_events",
                     "uni_mass", "social", "promo", "accounts", "unsorted")
    assert GREY == {"uni_mass", "social", "promo", "accounts"}


@pytest.mark.parametrize("sender, subject", [
    ("Tutor via Ed <notification@edstem.org>", "New in 12345 Example Course: Quiz 3"),
    ("12345 Example Course <notifications@instructure.com>", "Week 4: Topic notes"),
    ("Program Office <office@campus.example.edu.vn>", "Thông báo về việc mua bảo hiểm"),
    ("A Lecturer <lecturer@uni.example.edu>", "Reminder: lab assessment"),
])
def test_subjects_and_the_program_office_are_sure(sender, subject):
    assert sort(sender, subject, "university") == RuleKind("study", True)


def test_the_owners_senders_come_only_from_their_config():
    configure(KindSenders())
    assert sort("A Lecturer <lecturer@uni.example.edu>", "Reminder: lab assessment") == RuleKind("news_events", False)
    assert sort("Program Office <office@campus.example.edu.vn>", "Thông báo") == RuleKind("news_events", False)
    assert sort("Newsletter <news@newsletter.example.com>", "Spring sale") == RuleKind("unsorted", False)


def test_the_rules_stamp_changes_with_the_owners_senders():
    # a new [kinds] list re-sorts stored mail at the next engine start, like a new RULES_VERSION
    with_owner = rules_stamp()
    configure(KindSenders())
    assert rules_stamp() != with_owner
    configure(OWNER)
    assert rules_stamp() == with_owner


@pytest.mark.parametrize("sender, subject, kind", [
    ("Facebook <notification@facebookmail.com>", "Bạn có lời mời kết bạn mới", "social"),
    ("Discord <notifications@discord.com>", "someone mentioned you in Some Server", "social"),
    ("LinkedIn <invitations@linkedin.com>", "You have 3 new invitations", "social"),
    ("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>", "Example Co Backend Engineer", "jobs"),
    ("LinkedIn <jobs-noreply@linkedin.com>", "Example Co is hiring a Data Engineer", "jobs"),
    ("Newsletter <noreply@newsletter.example.com>", "4 new image styles to try", "promo"),
    ("Google <no-reply@accounts.google.com>", "Security alert", "accounts"),
    ("Vendor <noreply@notify.vendor.example.com>", "Ready for action", "accounts"),
    ("Me <me@gmail.com>", "TEST EMAIL", "accounts"),
])
def test_known_senders_are_sure(sender, subject, kind):
    assert sort(sender, subject) == RuleKind(kind, True)


@pytest.mark.parametrize("sender, subject, kind", [
    ("Student Affairs <affairs@campus.example.edu.vn>", "Thông báo chương trình học bổng Example năm 2027", "scholarship"),
    ("TRẠM Y TẾ <clinic@campus.example.edu.vn>", "Thu bảo hiểm y tế năm học mới", "uni_admin"),
    ("Student Affairs <affairs@campus.example.edu.vn>", "Thông báo về việc khám sức khỏe sinh viên", "uni_admin"),
    ("Student Affairs <affairs@campus.example.edu.vn>", "[Tuyển dụng] Example Group tuyển kỹ sư", "jobs"),
    ("Student Affairs <affairs@campus.example.edu.vn>", "Cuộc thi Example hackathon 2026", "hackathon"),
    ("Student Affairs <affairs@campus.example.edu.vn>", "Cuộc thi Example Jam 2026", "hackathon"),
])
def test_clear_university_words_are_sure(sender, subject, kind):
    assert sort(sender, subject) == RuleKind(kind, True)


def test_an_unsure_guess_is_never_grey():
    assert sort("Student Affairs <affairs@campus.example.edu.vn>", "Chương trình Đại nhạc hội mùa thu") == \
        RuleKind("news_events", False)
    assert sort("Someone <hello@example.com>", "Hello there") == RuleKind("unsorted", False)
    words = ["Thông báo", "Chương trình", "Khảo sát", "Mời tham gia", "Lịch", "Update", "Newsletter", "Hello",
             "Re:", "Fwd:"]
    senders = ["X <a@campus.example.edu.vn>", "Y <b@example.com>", "Z <c@mail.example.org>",
               "W <d@uni.example.edu.vn>", "V <e@gmail.com>"]
    for sender in senders:
        for word in words:
            result = sort(sender, f"{word} something")
            assert result.sure or result.kind not in GREY, (sender, word, result)


def test_vietnamese_words_match_with_or_without_accents():
    sender = "Student Affairs <affairs@campus.example.edu.vn>"
    for subject in ("THÔNG BÁO HỌC BỔNG 2027", "thong bao hoc bong 2027", unicodedata.normalize("NFD", "học bổng 2027")):
        assert sort(sender, subject).kind == "scholarship", subject


@pytest.mark.parametrize("subject", ["KHẨN - Nhắc đăng ký trước thứ Sáu", "[Bắt buộc] Về việc mua bảo hiểm",
                                     "Action Required | Join the server", "URGENT: confirm your seat",
                                     "Thông báo khẩn: đổi phòng thi", "Khẩn cấp: cập nhật lịch học"])
def test_urgent_words(subject):
    assert urgent_by_rules(subject)


@pytest.mark.parametrize("subject", ["Bắt đầu học kỳ mới", "Weekly digest", "Khảo sát nhu cầu",
                                     "Học bổng hỗ trợ sinh viên có hoàn cảnh khó khăn năm 2026",
                                     "Mời khán giả tham dự Đại nhạc hội", "Khan Academy: new course",
                                     "Non-urgent: weekly update", "Tặng khăn quàng"])
def test_not_urgent(subject):
    assert not urgent_by_rules(subject)


@pytest.mark.parametrize("sender, subject, expected", [
    ("Ed <notification@edstem.org>", "Login to Ed", RuleKind("accounts", True)),
    ("LinkedIn <updates@linkedin.com>", "Example Recruiter recently posted", RuleKind("social", True)),
    ("LinkedIn <updates@linkedin.com>", "AI Engineer: Example Co hired near you", RuleKind("jobs", True)),
    ("LinkedIn via LinkedIn <editors@linkedin.com>", "The skills shaping work now", RuleKind("promo", True)),
    ("TRẠM Y TẾ <clinic@campus.example.edu.vn>", "Mời tham dự buổi nói chuyện về sức khỏe tinh thần", RuleKind("news_events", False)),
    ("The Example team <news@vendor.example.com>", "Win a trip to ExampleConf!", RuleKind("hackathon", False)),
])
def test_the_tuned_rules(sender, subject, expected):
    # each case is a shape real mail takes (made-up words here)
    assert sort(sender, subject) == expected


def test_course_mail_from_ed_that_mentions_verifying_stays_a_course():
    # final review: any account word sent Ed course threads to Accounts, for sure
    marks = "New in 12345 Example Course: Please verify your marks by Friday"
    assert sort("Tutor via Ed <n@edstem.org>", marks) == RuleKind("study", True)
    assert sort("Tutor via Ed <n@edstem.org>", "Lab 3 thiết lập môi trường") == RuleKind("study", True)
    assert sort("Ed <n@edstem.org>", "Login to Ed") == RuleKind("accounts", True)


def test_a_linkedin_message_from_a_person_goes_to_the_ai():
    # final review: a recruiter's interview invitation was Social, for sure, and hidden in Other
    recruiter = "Recruiter Name (via LinkedIn) <messages-noreply@linkedin.com>"
    assert sort(recruiter, "Interview invitation: Backend role") == RuleKind("jobs", False)
    assert sort("A Person (via LinkedIn) <messages@linkedin.com>", "Still waiting for your reply") == \
        RuleKind("unsorted", False)


@pytest.mark.parametrize("sender, subject, expected", [
    ("Organisers <team@hackathon.example.org>", "Welcome to the Example Hackathon 2026! Next steps",
     RuleKind("hackathon", True)),
    ("Devpost <x@devpost.com>", "Welcome to the Example AI Hackathon", RuleKind("hackathon", True)),
    ("Google for Developers <x@google.com>", "Example Solution Challenge: hackathon registration open",
     RuleKind("hackathon", True)),
    ("Vendor <x@vendor.example.com>", "Vendor Academy scholarship: apply by Friday", RuleKind("scholarship", False)),
])
def test_hackathon_and_scholarship_words_beat_welcome_and_promo_senders(sender, subject, expected):
    assert sort(sender, subject) == expected
