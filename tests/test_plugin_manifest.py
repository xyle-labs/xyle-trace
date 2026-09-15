import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS = ("lineage", "source-discovery", "extraction", "reproducible-runs", "claims")


def test_plugin_manifest_is_valid_json_with_required_fields():
    manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "xyle-trace"
    assert manifest["description"]
    assert manifest["version"]


def test_plugin_ships_all_five_skills():
    for name in SKILLS:
        assert (ROOT / "skills" / name / "SKILL.md").is_file()


def test_plugin_declares_the_mcp_server():
    # .mcp.json is the single authoritative declaration of the server; plugin.json
    # does not duplicate it (see test_plugin_manifest_has_no_duplicate_mcp_declaration).
    config = json.loads((ROOT / ".mcp.json").read_text())
    server = config["mcpServers"]["xyle_trace"]
    assert server["command"] == "xyle-trace-mcp"
    assert server["type"] == "stdio"


def test_plugin_manifest_has_no_duplicate_mcp_declaration():
    manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    assert "mcpServers" not in manifest


def test_plugin_version_matches_the_package_version():
    import tomllib

    manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert manifest["version"] == project["version"]


def test_marketplace_lists_the_plugin_with_matching_metadata():
    manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    marketplace = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert marketplace["plugins"]
    entry = marketplace["plugins"][0]
    assert entry["name"] == manifest["name"]
    assert entry["source"] == "./"
