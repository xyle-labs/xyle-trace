"""Check that a locator's anchor actually occurs in the captured bytes.

A locator names where a value was read. Until the named place is checked
against the retained bytes, an exact-looking locator proves nothing: a wrong
page number reads exactly like a right one. This module does that check for
anything that decodes as text, and refuses to guess for anything that does not.
"""

import re

_WHITESPACE = re.compile(r"\s+")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def decode_text(data: bytes) -> str | None:
    """Return the decoded text if `data` looks like text, else None.

    This is the single place that decides both *whether* bytes are text and
    *how* to decode them. `verify_anchor` and any caller that hands decoded
    text back to a user (e.g. content-mode reads) must go through this
    function so detection and decoding can never disagree: deciding the
    encoding one way and decoding it another is how mojibake gets labelled
    `verified: true`.
    """
    # utf-16 without a BOM is only tried when a BOM marks it explicitly: without
    # one, `bytes.decode("utf-16")` still succeeds on plenty of *other* encodings'
    # bytes (a cp1252/latin-1 export, say) whenever the length happens to be even,
    # turning an accidental byte count into a silent false accusation about
    # provenance. latin-1 always succeeds and is tried last as the permissive
    # fallback, gated by the printability check below.
    encodings = ["utf-8"]
    if data[:2] in _UTF16_BOMS:
        encodings.append("utf-16")
    encodings.append("latin-1")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
        # utf-16 and latin-1 decode almost any byte string to *something*, so a
        # successful decode alone does not mean the bytes are text: compressed
        # or otherwise binary payloads happily "decode" into unprintable junk.
        # Only trust the result when it actually looks like text.
        printable = sum(c.isprintable() or c.isspace() for c in text)
        if text and printable < 0.9 * len(text):
            return None
        return text
    return None


def verify_anchor(data: bytes, anchor: str) -> bool | None:
    """Return True if found, False if absent, None if the bytes are not text.

    None is not a failure. Binary formats without a text layer cannot be
    checked here, and reporting that honestly is the point: capture the
    extracted text as its own snapshot and anchor into that instead.
    """
    text = decode_text(data)
    if text is None:
        return None
    return _normalise(anchor) in _normalise(text)
