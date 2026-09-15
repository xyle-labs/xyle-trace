import pytest
from pydantic import ValidationError

from xyle_trace.contracts.model import Contract


def test_constructing_a_contract_with_a_bad_evidence_value_is_rejected():
    with pytest.raises(ValidationError):
        Contract(dataset="d", every_row=["evidence_backed"], evidence="exact-locator")


def test_constructing_a_contract_with_an_unknown_status_is_rejected():
    with pytest.raises(ValidationError):
        Contract(dataset="d", every_row=["probably_fine"])


@pytest.mark.parametrize(
    "fields",
    [
        {"dataset": " ", "every_row": ["evidence_backed"]},
        {"dataset": "d", "every_row": []},
        {"dataset": "d", "every_row": ["evidence_backed"], "blokcs": ["engine.*"]},
        {"dataset": "d", "every_row": ["evidence_backed"], "blocks": [""]},
    ],
)
def test_direct_contracts_reject_empty_fields_and_policy_typos(fields):
    with pytest.raises(ValidationError):
        Contract(**fields)
