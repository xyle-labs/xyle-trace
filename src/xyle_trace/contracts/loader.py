import pathlib

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from xyle_trace.contracts.model import STATUS_ALIASES, Contract


class ContractError(Exception):
    """Raised when a contracts file is malformed."""


class _PolicyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise ContractError("policy mappings require unique string keys")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class _Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    every_row: list[str] = Field(min_length=1)
    evidence: str | None = None


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    dataset: str
    require: _Requirements
    blocks: list[str] = Field(default_factory=list)
    severity: str = "block"


def load_contracts(path: pathlib.Path) -> list[Contract]:
    return parse_contracts(pathlib.Path(path).read_bytes(), source=str(path))


def parse_contracts(text: str | bytes, *, source: str = "contracts") -> list[Contract]:
    """Parse an already-read policy using the same rules as the file loader."""
    try:
        raw = yaml.load(text, Loader=_PolicyLoader)
    except (yaml.YAMLError, UnicodeError) as e:
        raise ContractError(f"{source}: invalid YAML: {e}") from e
    if not isinstance(raw, list):
        raise ContractError(f"{source}: top level must be a list of contracts")

    contracts = []
    for index, entry in enumerate(raw):
        try:
            parsed = _Entry.model_validate(entry)
            contracts.append(
                Contract(
                    dataset=parsed.dataset,
                    every_row=[STATUS_ALIASES.get(s, s) for s in parsed.require.every_row],
                    evidence=parsed.require.evidence,
                    blocks=parsed.blocks,
                    severity=parsed.severity,
                )
            )
        except ValidationError as e:
            raise ContractError(f"{source}: contract {index} has malformed fields: {e}") from e
    return contracts
