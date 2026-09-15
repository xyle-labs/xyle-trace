from xyle_trace.core.ids import make_id


def test_id_is_prefixed_by_node_type():
    node_id = make_id("proj1", "source", "psa-census-2020")
    assert node_id.startswith("source:")


def test_id_is_deterministic():
    assert make_id("proj1", "source", "psa") == make_id("proj1", "source", "psa")


def test_id_varies_by_project():
    assert make_id("proj1", "source", "psa") != make_id("proj2", "source", "psa")


def test_id_varies_by_natural_key():
    assert make_id("proj1", "source", "psa") != make_id("proj1", "source", "doe")


def test_id_suffix_is_sixteen_hex_chars():
    suffix = make_id("proj1", "source", "psa").split(":", 1)[1]
    assert len(suffix) == 16
    assert all(c in "0123456789abcdef" for c in suffix)
