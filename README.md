# xyle-trace

An agent-native provenance toolkit: your AI coding agent records where every
source, dataset, transformation, and claim came from as it works, so you can
ask a question like "where does this number come from?" and get a real answer.

[![CI](https://github.com/xyle-labs/xyle-trace/actions/workflows/ci.yml/badge.svg)](https://github.com/xyle-labs/xyle-trace/actions/workflows/ci.yml)

## What it does

- Records sources, immutable snapshots, transformations, derived datasets, and
  evidence-backed claims as an agent works, through five installed skills and
  an eight-tool MCP server, or directly through typed Python and CLI interfaces.
- Enforces provenance as a precondition for computation: a project's
  `contracts.yaml` blocks a run until its inputs are backed by exact evidence
  or a recorded assumption, instead of relying on the agent to remember.
- Answers upstream, downstream, `explain`, `impact`, and `content` queries
  against a deterministic, local-first SQLite graph. Storage needs no hosted
  service. An agent host receives the data returned through MCP; its privacy
  settings and model processing remain the host's responsibility.
- Explains selected results using retained input versions, shows pending review
  and changed outputs, and provides a connected offline explorer and selected
  HTML reports. Portable handoffs preserve the graph and optionally retained
  evidence, without restoring executable working files.

## See it work

```
$ xyle-trace explain --project managed-demo --db .lineage/graph.db \
    --node artifact:6313a0dff677badf
{
  "node": "artifact:6313a0dff677badf",
  "upstream": [
    "run:0998654269f9e908", "transformation:8c12985bbc8abd68",
    "dataset:7f76069f42d3d874", "evidence_link:f8962d2860a94d96",
    "source_snapshot:374d0131c85c61f0", "source:96f0bab3dcf7be34"
  ],
  "sources": ["source:96f0bab3dcf7be34"]
}
```

That output — trimmed here to the ancestor IDs; the full response also carries
node/edge metadata and revisions — is real, produced by the retained-execution
example in [the quickstart](docs/quickstart.md). The artifact traces back
through a run, a transformation, a dataset, an evidence link, a source
snapshot, and finally the one source it came from.

## Install

Requires Python 3.11+ and Git. Until the first PyPI release, install from the
public repository:

```bash
pip install 'xyle-trace[mcp] @ git+https://github.com/xyle-labs/xyle-trace.git@main'
xyle-trace new --project demo --path ./demo
```

For a complete synthetic calculation, correction gate, and verified historical
replay from the installed package:

```bash
xyle-trace demo --project demo --path ./worked-demo
```

Open `worked-demo/lineage.html` to inspect the result. The demo deliberately
leaves a corrected input unresolved so the current-data gate remains visible.
See [capture, review, sharing, and handoff](docs/workflow.md) for the full workflow.

`main` tracks unreleased development; replace it with a reviewed commit ID
when you need a repeatable installation.

`new` scaffolds a project: local store, the five skills, MCP configuration for
Claude Code or Codex, and a starting `contracts.yaml`. Full walkthrough,
including what `validate` reports before any evidence exists and a worked
run with verified replay, in **[the quickstart](docs/quickstart.md)**.

## Why it's shaped this way

The product reasoning and domain model are in **[docs/concept.md](docs/concept.md)**.
Current implementation behavior, metadata requirements, and CLI/MCP details
are in **[docs/core-contract.md](docs/core-contract.md)**.

## Roadmap

The product scope is reliable capture, exact result explanations, review and
change impact, and sharing selected findings. Portable projects support this
workflow. See **[ROADMAP.md](ROADMAP.md)** for implementation and acceptance
boundaries. Domain analysis and specialist dependencies belong in consuming
projects.

## Limitations

The full detail is in [core-contract.md's limitations
section](docs/core-contract.md#limitations); the short version:

- MCP `record_run` is **reported** capture and is never reproducible; only
  `xyle-trace-run` executes and verifies.
- The local runner replays trusted, self-contained standard-library Python
  programs. It is a replay contract, not an OS sandbox or a distributable
  environment image.
- `verified_locator` proves an evidence anchor occurs in bytes that hash to
  the recorded content — it does not prove the passage supports the value.
  Binary payloads without a text layer report as unverifiable, honestly,
  rather than passed or failed.
- Node IDs are derived from the project ID; renaming a project re-derives
  them. Choose the project name deliberately.
- Live agent adherence depends on the host and task. Protocol compatibility
  is tested; [bounded live-host findings](docs/agent-acceptance.md) describe
  observed behavior and the remaining acceptance gaps.
- The toolkit records the provenance an agent reports. It cannot detect a
  source the agent never mentioned.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Project contact: [jesse@xyle.de](mailto:jesse@xyle.de).
