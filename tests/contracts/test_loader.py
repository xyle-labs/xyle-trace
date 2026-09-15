import textwrap

import pytest

from xyle_trace.contracts.loader import ContractError, load_contracts, parse_contracts


def _write(tmp_path, text):
    path = tmp_path / "contracts.yaml"
    path.write_text(textwrap.dedent(text))
    return path


def test_loads_a_single_contract(tmp_path):
    path = _write(
        tmp_path,
        """
        - dataset: analysis.manifest
          require:
            every_row: [evidence_backed, decision_backed]
            evidence: exact_locator
          blocks: [analysis.engine.*]
        """,
    )
    contracts = load_contracts(path)
    assert len(contracts) == 1
    assert contracts[0].dataset == "analysis.manifest"
    assert contracts[0].evidence == "exact_locator"
    assert contracts[0].blocks == ["analysis.engine.*"]


def test_decision_backed_is_normalised_to_assumption_backed(tmp_path):
    path = _write(
        tmp_path,
        """
        - dataset: d
          require:
            every_row: [evidence_backed, decision_backed]
        """,
    )
    assert set(load_contracts(path)[0].every_row) == {"evidence_backed", "assumption_backed"}


def test_evidence_and_blocks_are_optional(tmp_path):
    path = _write(tmp_path, "- dataset: d\n  require:\n    every_row: [evidence_backed]\n")
    contract = load_contracts(path)[0]
    assert contract.evidence is None
    assert contract.blocks == []


def test_severity_defaults_to_block(tmp_path):
    path = _write(tmp_path, "- dataset: d\n  require:\n    every_row: [evidence_backed]\n")
    contract = load_contracts(path)[0]
    assert contract.severity == "block"


def test_severity_warn_is_loaded(tmp_path):
    path = _write(
        tmp_path,
        "- dataset: d\n  severity: warn\n  require:\n    every_row: [evidence_backed]\n",
    )
    contract = load_contracts(path)[0]
    assert contract.severity == "warn"


def test_unknown_severity_value_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        "- dataset: d\n  severity: nonsense\n  require:\n    every_row: [evidence_backed]\n",
    )
    with pytest.raises(ContractError):
        load_contracts(path)


def test_missing_dataset_key_is_rejected(tmp_path):
    path = _write(tmp_path, "- require:\n    every_row: [evidence_backed]\n")
    with pytest.raises(ContractError):
        load_contracts(path)


def test_unknown_status_is_rejected(tmp_path):
    path = _write(tmp_path, "- dataset: d\n  require:\n    every_row: [probably_fine]\n")
    with pytest.raises(ContractError):
        load_contracts(path)


def test_top_level_must_be_a_list(tmp_path):
    path = _write(tmp_path, "dataset: d\n")
    with pytest.raises(ContractError):
        load_contracts(path)


def test_require_as_list_is_rejected(tmp_path):
    path = _write(tmp_path, "- dataset: d\n  require: [a, b]\n")
    with pytest.raises(ContractError):
        load_contracts(path)


def test_require_as_string_is_rejected(tmp_path):
    path = _write(tmp_path, "- dataset: d\n  require: oops\n")
    with pytest.raises(ContractError):
        load_contracts(path)


def test_dataset_as_non_string_scalar_is_rejected(tmp_path):
    path = _write(tmp_path, "- dataset: 123\n  require:\n    every_row: [evidence_backed]\n")
    with pytest.raises(ContractError):
        load_contracts(path)


def test_evidence_typo_hyphen_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        """
        - dataset: d
          require:
            every_row: [evidence_backed]
            evidence: exact-locator
        """,
    )
    with pytest.raises(ContractError):
        load_contracts(path)


def test_unknown_evidence_value_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        """
        - dataset: d
          require:
            every_row: [evidence_backed]
            evidence: something_else
        """,
    )
    with pytest.raises(ContractError):
        load_contracts(path)


def test_evidence_as_a_list_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        """
        - dataset: d
          require:
            every_row: [evidence_backed]
            evidence: [foo]
        """,
    )
    with pytest.raises(ContractError):
        load_contracts(path)


def test_evidence_as_a_mapping_is_rejected(tmp_path):
    path = _write(
        tmp_path,
        """
        - dataset: d
          require:
            every_row: [evidence_backed]
            evidence: {a: b}
        """,
    )
    with pytest.raises(ContractError):
        load_contracts(path)


def test_evidence_exact_locator_loads_correctly(tmp_path):
    path = _write(
        tmp_path,
        """
        - dataset: d
          require:
            every_row: [evidence_backed]
            evidence: exact_locator
        """,
    )
    contracts = load_contracts(path)
    assert len(contracts) == 1
    assert contracts[0].evidence == "exact_locator"


@pytest.mark.parametrize(
    "text",
    [
        "- dataset: [\n",
        "- dataset: d\n  require:\n    every_row: [evidence_backed]\n    evidnce: exact_locator\n",
        "- dataset: d\n  require:\n    every_row: [evidence_backed]\n  blokcs: [engine.*]\n",
        "- dataset: d\n  require:\n    every_row: [evidence_backed]\n  blocks: false\n",
        "- dataset: d\n  require: []\n",
        "- dataset: d\n  require:\n    every_row: {evidence_backed: true}\n",
        "- dataset: d\n  require:\n    every_row: [[evidence_backed]]\n",
        "- dataset: d\n  require:\n    every_row: []\n",
        "- dataset: ' '\n  require:\n    every_row: [evidence_backed]\n",
    ],
)
def test_malformed_policy_fails_closed(tmp_path, text):
    with pytest.raises(ContractError):
        load_contracts(_write(tmp_path, text))


@pytest.mark.parametrize(
    "text",
    [
        "- dataset: d\n  require:\n    every_row: [evidence_backed]\n    evidence: exact_locator\n    evidence: null\n",
        "- dataset: d\n  require:\n    every_row: [evidence_backed]\n  blocks: [engine.*]\n  blocks: []\n",
        "- dataset: d\n  require:\n    every_row: [evidence_backed]\n  1: ignored\n",
    ],
)
def test_duplicate_or_non_string_policy_keys_are_rejected(tmp_path, text):
    with pytest.raises(ContractError):
        load_contracts(_write(tmp_path, text))


def test_already_read_policy_uses_the_file_loaders_rules(tmp_path):
    path = _write(tmp_path, "- dataset: d\n  require:\n    every_row: [decision_backed]\n")
    expected = load_contracts(path)
    assert parse_contracts(path.read_bytes()) == parse_contracts(path.read_text()) == expected
    with pytest.raises(ContractError):
        parse_contracts(
            "- dataset: d\n  dataset: other\n  require:\n    every_row: [evidence_backed]\n"
        )
