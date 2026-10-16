"""Text folding for matching: one comparable form per word, accents removed.

Why? Vietnamese text arrives in several byte forms for the same words:
precomposed (NFC) or decomposed (NFD, e.g. from Unikey's "Unicode tổ hợp"),
old- and new-style tone placement ("hoá" vs "hóa"), and bank mail often
has no accents at all ("ma xac thuc"). Folding all of them to plain ASCII
letters lets one rule (or one profile keyword) match every spelling.
"""

from __future__ import annotations

import unicodedata


def fold(text: str, keep_case: bool = False) -> str:
    """'Mã XÁC THỰC Đăng nhập' -> 'ma xac thuc dang nhap'.

    keep_case=True strips accents but keeps capitals ('TUYỂN AI' -> 'TUYEN AI'),
    for keywords that only count when written in capitals.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("Đ", "D").replace("đ", "d") if keep_case else text.casefold().replace("đ", "d")
    stripped = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", stripped)
