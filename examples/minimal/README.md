# Minimal MCP continuation fixture

This example follows two sample counts through source capture, extraction, a
reported sum, a claim, a correction, and recovery after restarting the server.
It is generic and entirely local. No hosted service or live agent account is needed.

- `source-v1.csv`: A=10 and B=20 count, with a total of 30.
- `records.json`: the corresponding initial dataset records.
- `source-v2.csv`: a later release revises A to 12; B remains 20.
- `contracts.yaml`: requires evidence or explicit assumptions for every record
  in `observations`, including exact evidence locations, and blocks `analysis.*`.

## Replay through real stdio

From the toolkit checkout, use an environment with the development and MCP extras
installed (`python -m pip install '.[dev,mcp]'`). Then run:

```bash
.venv/bin/python -m pytest -q -s tests/mcp/test_workflow.py
```

The [test](../../tests/mcp/test_workflow.py) copies these files into a temporary
project, initializes it through the CLI, and launches real MCP subprocesses.
Every graph read and write after initialization goes through a tool. There are
no direct service/store calls or database repairs. It covers both the modern
`2026-07-28` protocol and the SDK's legacy handshake, plus an independent assumed
scenario batch. Without the MCP extra the module skips; a skip is not a passing
protocol check.

To preserve output at an explicit temporary location for inspection:

```bash
replay_root=$(mktemp -d /tmp/xyle-minimal.XXXXXX)
.venv/bin/python -m pytest -q -s tests/mcp/test_workflow.py --basetemp "$replay_root/results"
printf '%s\n' "$replay_root/results"
```

Each workflow test's subdirectory contains the SQLite database, both captured
source versions, `capture-receipt.json`, `sum-v1.json`, `sum-v2.json`, and the final
`lineage.json`. Use the fresh path above: pytest clears its `--basetemp` directory
before running. Normal pytest temporary output follows pytest's retention policy.

## What the replay proves

The first server registers a logical source and captures v1, verifying the
server-calculated hash against the bytes. It registers both observations, reads
the validation gaps, and binds both records to one exact evidence locator in a
single `lineage.link` call. Validation then permits `analysis.sum`.

The client starts a reported run, computes the sum locally, writes an artifact,
and finishes the run with the observed output. It attaches the result to a claim,
records an explicit fixture review, and explains the path back to the captured
source bytes. Exact registration, snapshot, evidence, run, and review retries
preserve identity and graph state.

Next, the first server captures v2. The client saves that actual snapshot tool
response as `capture-receipt.json`, then records a correction to A. The correction
clears A's resolution, leaves B backed, and flags the claim `needs_review`.
Validation fails and a new run-start request returns `run_blocked` without creating
a run. Stale record/evidence writes and replay of the old review return conflicts.
The first client and server then close completely.

The second server reports the persisted claim review flag before any write. Its
recovery routine receives only the project location and a new client, with no
first-session Python responses or remembered IDs. It reads the saved capture
receipt and verifies the local captured bytes, then uses fresh validation work
items for the dataset ID, row data, revisions, allowed statuses, and requirements.
It binds the corrected row to its exact v2 location and validates again. The
receipt supplies the snapshot ID; validation alone does not discover new sources.

Recovery leaves the claim pending review. The client recomputes the total as 32,
retains the historical output of 30, marks its old support edge provisional with
an explicit supersession rationale, and attaches the new output. Only after
updating the claim and recording a new explicit review does it become `reviewed`.
Final JSON export retains both snapshots and runs, without duplicate records or
resolutions. Export bytes match the returned content hash.

## Resolution action counts

The fixture asserts the actual call sequences below, including gap discovery and
post-resolution validation. Registration/capture setup, retry probes, persistence
inspection, run capture, claim review, and export are separate workflow actions;
three is not a claim about the total session length.

| Batch | Records | Resolution calls | Count |
| --- | ---: | --- | ---: |
| Initial observed evidence | 2 | validate → link → validate | 3 |
| Corrected evidence after restart | 1 | validate → link → validate | 3 |
| Independent hypothetical assumptions | 2 | validate → decide → validate | 3 |

The assumed scenario deliberately treats A=10 and B=20 as hypothetical inputs.
It does not claim observation or source capture, and reports an assumption rate
of 1. This scenario is independent of the observed correction workflow.

## Agent replay and limitations

For a live host, follow [project agent setup](../../docs/agent-setup.md), copy these
four input files into a new consuming project, and ask the agent to follow the
sequence above using its discovered tool schemas. The installed skills supply
the capture, revision, and review instructions. Preserve the snapshot receipt
before ending the first session; do not copy IDs or revision tokens from a
different run of this fixture.

The automated replay checks protocol behavior, not whether a live Codex or Claude
session independently follows the skills. Fixture review decisions are scripted
test assessments, not human approvals. Runs always report
`capture_mode: "reported"` and `reproducible: false`: MCP does not execute or
isolate the computation, and dataset fingerprints do not retain immutable input
contents. The graph retains historical paths, including provisional edges, so
reachability alone is not evidence that an old result supports a current claim.
