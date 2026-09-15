# Local execution and verified replay

The local runner retains the exact JSON input records, resolutions and lineage
context, direct file input bytes, program code, parameters, parsed policy receipt,
and interpreter/platform identity. It executes a trusted, self-contained Python
program and retains its canonical JSON output under `.lineage/snapshots/`.
The twelve graph node types are unchanged: managed runs use `Run`, and their
outputs use `Artifact`.

MCP `lineage.record_run` remains reported capture. To execute code, use the local
`xyle-trace-run` CLI or `xyle_trace.core.execution.execute` Python function.
No arbitrary-code execution tool has been added to MCP.

## Program contract

A program receives the input JSON path as `sys.argv[1]` and must write finite JSON
to the output path at `sys.argv[2]`. See [sum.py](../examples/managed/sum.py).

Input has `params` and an `inputs` object keyed by canonical node ID. Each entry
contains `node`, current dataset `records` with revision/resolution metadata, and
the captured lineage context. File inputs also contain `file_base64`; decode those
bytes instead of opening the original path. Record values are at
`entry["records"][i]["record"]["data"]`.

Programs run in a temporary working directory under the current interpreter with
`-I -S`, a minimal environment, and no site packages. Support is intentionally
limited to self-contained standard-library Python programs. Output is one JSON
value; embedded chart/table data can be rendered into shareable artifacts afterward.
This is a replay contract, not an operating-system sandbox: trusted code can still
access other files, clocks, or networks. Undeclared inputs can cause replay mismatch.

## Execute and replay

Initialize the store and register the transformation and inputs through the usual
CLI/service/MCP operations. Use returned canonical IDs in these commands:

```bash
xyle-trace-run execute \
  --project analysis --db /project/.lineage/graph.db --root /project \
  --contracts /project/contracts.yaml --key sum-1 \
  --transformation TRANSFORMATION_ID --input DATASET_ID --script sum.py
```

Repeat `--input` for additional datasets, source snapshots, or file artifacts.
Optional `--params params.json` reads a JSON object. `--timeout` defaults to 60
seconds. The script must be inside the project root; no shell interpolation is used.
New execution checks current contracts before capture and again before reserving
the run, rejecting changed metadata or policy during preparation.

The command prints a Run node with `data.bundle` and `data.output` content references.
Exit codes are 0 for success, 1 for a recorded execution failure, and 2 for invalid
configuration, a conflict, or a blocked run. Exact terminal retries return the
existing run after checking retained content; they do not execute again.

```bash
xyle-trace-run replay \
  --project analysis --db /project/.lineage/graph.db --root /project \
  --contracts /project/contracts.yaml --key replay-1 --run ORIGINAL_RUN_ID
```

Replay accepts only a successful managed run. It verifies retained bundle/output
hashes and interpreter/platform identity, executes the retained code against the
retained inputs, and compares canonical output hashes. It uses the original policy
receipt; it does not consume or authorize currently edited records. Current policy
configuration must still be present and valid to open the service.

The original run remains `capture_mode: "managed"`, `reproducible: false`.
A successful replay creates a separate managed run
with `replay_verified: true` and `reproducible: true`; the original is not rewritten.
These fields mean an identical JSON result was observed for the retained inputs
under the recorded interpreter/platform. They do not certify future determinism,
OS isolation, or portability to other environments. Python version, executable
hash, flags, and platform are checked; this is not a distributable environment image.

Exceptions, nonzero exit, timeout, missing/invalid output, and replay mismatch are
recorded as failures. Mismatching output remains inspectable. A process interruption
can leave `started`; inspect the run and external side effects before choosing a new
execution key. An identical request never reruns an ambiguous started execution.
Captured bundles and outputs are required for replay; graph JSON alone is insufficient.
The graph's ordinary input edges point to logical nodes, which may change later;
the retained bundle is the authority for the exact historical input contents.

## Run the complete example

From the source checkout with the base package installed:

```bash
.venv/bin/python examples/managed/demo.py /tmp/xyle-managed-example
```

Choose a new directory; the demo refuses to overwrite an existing one. It captures
two observations, computes 30, corrects one input and proves current computation
is blocked, then replays the original result from retained inputs. It writes
`runs.json`, `lineage.json`, and `lineage.html`. The correction remains explicitly
unresolved; replay does not bypass that current-data gate.

## Offline explorer

Call `lineage.export` with
`{"request":{"format":"html","destination":"lineage.html"}}`, or run:

```bash
xyle-trace export --project analysis --db /project/.lineage/graph.db \
  --format html --out /project/lineage.html
```

Open the resulting file in a browser. Select a node to inspect metadata, record
backing, and edge qualifiers; filter by type, text, or upstream/downstream lineage.
The file is self-contained and uses no network resources. Source text is rendered
as data. Provisional/historical paths remain visible and do not certify support.

CLI export atomically replaces the explicitly selected output file and refuses
database/sidecar aliases or an unknown project. The MCP/service exporter additionally
bounds destinations to the configured root and protects policy and snapshot files.
