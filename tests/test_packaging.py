import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_license_file_is_apache_2_0():
    text = (ROOT / "LICENSE").read_text()
    assert "Apache License" in text
    assert "Version 2.0" in text


def test_project_metadata_is_complete_for_publication():
    project = _pyproject()["project"]
    assert project["license"] == "Apache-2.0"
    assert project["readme"] == "README.md"
    assert project["description"]
    assert project["urls"]["Homepage"]
    assert project["urls"]["Source"]
    assert any(c.startswith("Programming Language :: Python :: 3.11") for c in project["classifiers"])


def test_third_party_licenses_are_documented():
    text = (ROOT / "docs/third-party-licenses.md").read_text()
    for package in ("pydantic", "PyYAML", "typing-extensions", "mcp"):
        assert package in text
