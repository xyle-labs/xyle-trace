from xyle_trace.core.hashing import hash_bytes, hash_file


def test_hash_bytes_has_sha256_prefix():
    assert hash_bytes(b"abc").startswith("sha256:")


def test_hash_bytes_matches_known_sha256():
    expected = "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert hash_bytes(b"abc") == expected


def test_hash_file_matches_hash_bytes(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_bytes(b"abc")
    assert hash_file(path) == hash_bytes(b"abc")


def test_hash_file_handles_content_larger_than_one_chunk(tmp_path):
    payload = b"x" * (1024 * 1024 + 7)
    path = tmp_path / "big.bin"
    path.write_bytes(payload)
    assert hash_file(path) == hash_bytes(payload)
