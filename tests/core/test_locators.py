from xyle_trace.core.locators import decode_text, verify_anchor


def test_anchor_found_in_utf8_text():
    assert verify_anchor(b"Sample A count was 1,234 items", "1,234 items") is True


def test_anchor_absent_from_decodable_text():
    assert verify_anchor(b"Sample A count was 1,234 items", "9,999 items") is False


def test_binary_payload_is_unverifiable_not_false():
    # A PDF header followed by a compressed stream: decodable as text it is not.
    assert verify_anchor(b"%PDF-1.7\n\x80\x81\x82\xff\xfe", "anything") is None


def test_whitespace_is_normalised_before_matching():
    # PDF and HTML text extraction routinely changes runs of whitespace.
    assert verify_anchor(b"Total\n   Sales\t1 781 974", "Total Sales 1 781 974") is True


def test_cp1252_text_is_verified_the_same_regardless_of_byte_length_parity():
    """A BOM-less cp1252/latin-1 export must not be misread as UTF-16.

    Without a BOM, `bytes.decode("utf-16")` still succeeds whenever the byte
    count happens to be even, turning an accidental trailing newline into the
    difference between "found" and "not found" for the exact same content.
    Both parities of this Excel-style cp1252 CSV must agree.
    """
    text = "Región,Total\nRegión 8,1.234"
    odd_length = text.encode("cp1252")
    even_length = (text + "\n").encode("cp1252")
    assert len(odd_length) % 2 == 1
    assert len(even_length) % 2 == 0
    assert verify_anchor(odd_length, "1.234") is True
    assert verify_anchor(even_length, "1.234") is True


def test_bom_marked_utf16_text_is_verified():
    data = "Sample A total 1,234".encode("utf-16")
    assert verify_anchor(data, "1,234") is True


def test_decode_text_agrees_with_verify_anchor_on_utf16():
    """`decode_text` must decode with the same encoding `verify_anchor` accepted.

    Detecting bytes as UTF-16 (via `verify_anchor`) and then decoding them as
    UTF-8 elsewhere is how mojibake gets shown next to `verified: true`.
    """
    payload = "Sample A count was 1,234 items".encode("utf-16")
    assert verify_anchor(payload, "1,234 items") is True
    assert decode_text(payload) == "Sample A count was 1,234 items"


def test_decode_text_agrees_with_verify_anchor_on_latin1():
    payload = "Café count was 500 items".encode("latin-1")
    assert verify_anchor(payload, "500 items") is True
    assert decode_text(payload) == "Café count was 500 items"


def test_decode_text_returns_none_for_binary_payload():
    assert decode_text(b"%PDF-1.7\n\x80\x81\x82\xff\xfe") is None
