# Quickstart

Every command below was run in a scratch directory before this file was
committed. Output shown is real, not illustrative.

## 1. Install

```bash
pip install 'xyle-trace[mcp]'
```

The base package has no runtime dependencies beyond Pydantic and PyYAML. The
`mcp` extra adds the optional stdio server; skip it if you only want the CLI
and Python service.

## 2. Scaffold a project

```bash
xyle-trace new --project demo --path ./demo
```

```text
created project demo at /.../demo
next: cd demo && edit contracts.yaml, then start your agent there
```

This does the whole setup in one step: a local store at `.lineage/graph.db`,
the five portable skills, MCP configuration for your host (`--host claude`,
`codex`, or `both`; Claude Code is the default), a starting `contracts.yaml`,
and project guidance (`CLAUDE.md` or `AGENTS.md`). See
[project agent setup](agent-setup.md) for the three install routes — the
Claude Code plugin, `--host codex` (Codex has no plugin registry, so this is
the manual-config route with the boilerplate written for you), and installing
by hand into an existing project.

```bash
cd demo
```

From here, start your agent (Claude Code, Codex, or another MCP-capable
host). It has the five skills and the MCP server; ask it to do real work —
download a source, extract a value, run a calculation — and it records
provenance as it goes.

## 3. See the gate before you satisfy it

`new` writes a starting `contracts.yaml` that already governs a dataset named
`observations`, on the theory that you will rename it to your project's first
real dataset. Before any evidence exists, validation reports the honest gap:

```bash
xyle-trace validate --project demo --db .lineage/graph.db --contracts contracts.yaml
```

```text
observations		dataset_not_found	no records found for dataset observations
blocked runs: analysis.*
```

The command exits `1`. This is the toolkit's core mechanism: a dataset
declares what backing its rows need, and runs listed under `blocks` refuse to
execute until that holds. Nothing here is broken — there is simply no evidence
yet. Register the dataset, back its records with evidence or an assumption,
and `validate` reports `no violations` with exit code `0` instead.

## 4. A worked run, with verified replay

`new` scaffolds a project; it does not run a computation for you. From a
clone of this repository, [`examples/managed/demo.py`](../examples/managed/demo.py)
does: it registers a source and a snapshot, links two dataset records to that
evidence, executes a retained Python program that sums them, records a
correction that invalidates the input, shows a blocked rerun, then replays the
original run from its retained bundle and checks the output still matches.

```bash
python examples/managed/demo.py /tmp/xyle-managed-demo
```

```text
initialised project managed-demo at /tmp/xyle-managed-demo/.lineage/graph.db
Retained original total: 30. Verified replay. Explorer: /tmp/xyle-managed-demo/lineage.html
```

Open `lineage.html` in a browser for an offline, filterable view of the graph
this produced — no server, no network access. See
[execution and replay](execution.md) for what the local runner does and does
not guarantee.

## 5. Read evidence and assumptions back

Two read-only commands work against any project, including the one above:

```bash
xyle-trace show --project managed-demo --db /tmp/xyle-managed-demo/.lineage/graph.db \
  --root /tmp/xyle-managed-demo --snapshot source_snapshot:374d0131c85c61f0
```

```json
{
  "path": "/tmp/xyle-managed-demo/.lineage/snapshots/d285d70f686f298a51604be69da2ddfeb6cde64bf41d02b8ad2910d0c9cc4748",
  "content_hash": "sha256:d285d70f686f298a51604be69da2ddfeb6cde64bf41d02b8ad2910d0c9cc4748",
  "size_bytes": 24,
  "verified": true
}
```

`verified: true` means the bytes at that path were re-hashed on read and
matched the hash recorded at capture time — not that anything about their
content was judged correct. Add `--text` to include the decoded text for a
snapshot that has one.

```bash
xyle-trace ledger --project managed-demo --db /tmp/xyle-managed-demo/.lineage/graph.db
```

Lists every assumption decision and whether it currently backs a record
(`standing`) or backs none (`unbacked`). Add `--open-only` to see just the
standing ones — the assumptions a reviewer should look at before trusting a
result. See [Limitations](core-contract.md#limitations) for what `unbacked`
does and does not tell you.

## Next

[docs/core-contract.md](core-contract.md) is the current implementation
handoff: exact metadata requirements, every CLI and MCP behavior, and the
limitations section. [docs/concept.md](concept.md) is the original product
reasoning this toolkit was built from.

Planned onboarding improvements, result exploration, and the separate example
walkthrough are tracked in [ROADMAP.md](../ROADMAP.md).
