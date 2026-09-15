import hashlib


def make_id(project_id: str, node_type: str, natural_key: str) -> str:
    """Derive a stable node id from its project, type, and natural key.

    The id must not change when a file moves or is renamed, so it is derived
    from the natural key rather than from any filesystem path.
    """
    material = f"{project_id}\x00{node_type}\x00{natural_key}".encode()
    digest = hashlib.sha256(material).hexdigest()[:16]
    return f"{node_type}:{digest}"
