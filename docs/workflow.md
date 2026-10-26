# Capture, explain, review, and share

The toolkit records meaningful provenance in the consuming project. Its twelve
node types and eight MCP tools stay unchanged. These capabilities are available
through the typed Python service and the installed CLI; MCP gains request fields
on its existing query, validate, and export tools.

## Start with an installed example

```bash
xyle-trace demo --project demo --path ./worked-demo --host both
```

This scaffolds discovery settings and skills, captures synthetic observations,
computes 30, records a correction that blocks new computation, and verifies a
replay of the original inputs. `lineage.html`, `lineage.json`, and `runs.json`
live in the new directory. The current policy failure is deliberate.

## Capture without an MCP session

`record` accepts the same typed requests as the service. Put a request in a JSON
file or use `--request -` for standard input:

```bash
xyle-trace record --project analysis --db .lineage/graph.db \
  --root . --contracts contracts.yaml \
  --operation register --request source.json
```

The supported operations are `register`, `snapshot`, `record_run`, `link`, and
`decide`. All return JSON. Schema, conflict, policy, and reference failures exit
2 with a structured error; no computation is performed by `record`.

For an external calculation, record its `start` before running it, then `finish`
with the returned run ID and revision, explicit outcome, and output IDs. The
service checks contract gates, records hashes and capture gaps, and preserves
terminal outcomes on exact retries. This remains **reported capture**. Use
[`xyle-trace-run`](execution.md) for compatible retained-input execution and replay.
Registering a run after a calculation cannot establish that its preflight happened
before computation. External command execution and retrospective run import are
not automated by this CLI.

## Inspect a result or recorded JSON field

Copy a result ID from the explorer or a capture response. `--selector` is a JSON
pointer into a recorded JSON output; `/total` selects its `total` field:

```bash
xyle-trace explain --project analysis --db .lineage/graph.db --root . \
  --node ARTIFACT_ID --selector /total
```

The `explanation` contains selection, calculations, inputs, evidence, assumptions,
decisions, capture gaps, and execution status. `--run RUN_ID` disambiguates a
producer, and `--revision sha256:...` refuses a changed selected node. Recorded
program code is available only with `--include-code`.

`selection.value_available` distinguishes a recorded JSON `null` from an
unavailable value. Empty objects, lists, strings, `false`, and zero remain valid
recorded values. An interrupted managed run remains `started`, not `executed`.

Managed explanations use the retained bundle for historical inputs and backing.
Each report result keeps its own historical context, even when two runs used
different versions of the same logical dataset. Reported runs expose the absence
of retained historical inputs. Field inspection checks the exact bytes against
the recorded output hash and refuses to substitute changed files.
Retained bundles must agree with the selected run's recorded input IDs,
calculation, parameters, policy, and environment. A replay must retain its
original bundle reference. These checks detect inconsistent records; the stored
run reference remains authoritative for retained versions. Hashes do not
authenticate a database or archive writer who can rewrite that receipt.

Provisional and contradictory paths remain visible as context; they do not
qualify a run as the result's producer. A path to a source does not prove support.
The consuming application maps a published KPI/API field to its recorded node,
run, and selector. No authenticated API is fetched during inspection.

## Review current project health and changes

```bash
xyle-trace doctor --project analysis --db .lineage/graph.db \
  --root . --contracts contracts.yaml
xyle-trace impact --project analysis --db .lineage/graph.db --node SOURCE_ID
```

Doctor reports policy validity separately from integrity, output freshness,
capture gaps, and pending claim reviews. Integrity is `verified`, `failed`, or
`unknown`; an unobserved path is never passed. Add repeated `--receipt PATH`
options to check explicit local receipt files against the selected graph.
No automatic project-directory crawl occurs. Exit 1 indicates policy failure or
integrity that is not fully verified; pending reviews are listed separately.

Corrections recorded through `decide` now retain prior record values, revisions,
backing receipts, and rationale. Correction receipts are immutable; use a new key
for another correction. Older corrections and direct low-level record edits have
no reconstructed history. Methodology, exclusion, and correction references
participate in explanation and downstream impact. Explicit claim reviews still
require the observed revision and never change analytical support status.

## Explore and share selected findings

```bash
xyle-trace export --project analysis --db .lineage/graph.db --root . \
  --format html --out lineage.html
xyle-trace export --project analysis --db .lineage/graph.db --root . \
  --format report --node ARTIFACT_ID --preview
xyle-trace export --project analysis --db .lineage/graph.db --root . \
  --format report --node ARTIFACT_ID --title 'Reviewed findings' --out report.html
```

The explorer has result cards, a connected diagram, a linear keyboard path,
readable details, version-aware links, search, and navigation. Large diagrams
group branches by relationship and offer expansion, collapse, and 80-node paging;
the linear path retains all connected results. Reports accept
repeated `--node` selections and provide internal links and print context.

`--preview` returns the projected sharing data and the same rendered-content hash
as writing that report. Unselected results and unrelated batch-correction values
are excluded. Retained source bytes and program code are omitted. Common sensitive
fields and URI credentials/query/fragment components are removed. This field-based
projection cannot identify private facts in arbitrary prose: review the exact
preview and HTML before sharing. Export creates a local file, not a publication.

## Preserve or move a project

```bash
xyle-trace export --project analysis --db .lineage/graph.db --root . \
  --contracts contracts.yaml --format bundle --out project.zip
xyle-trace export --project analysis --db .lineage/graph.db --root . \
  --contracts contracts.yaml --format bundle --include-content --out complete.zip
xyle-trace restore --bundle complete.zip --path ../restored-analysis
```

Metadata-only is the default. `--include-content` explicitly includes referenced
retained snapshots and managed execution blobs. Handoffs verify manifest hashes,
project identities, relationships, safe paths, and size limits; blob transfers
stream with 8 GiB default limits. Python callers can configure those limits.
The manifest detects corruption; it does not authenticate an archive's author.

Restore requires a new destination and preserves project IDs. It restores the
graph, supplied policy, relative discovery configuration, and included opaque
retained blobs. It never restores working scripts, hooks, host configuration, or
ordinary output paths, and never executes imported code. `content_complete`
describes retained content only. Doctor may correctly report missing working
outputs after restore. Reconfigure your host for the new directory; bare
`xyle-trace-mcp` can use its discovery file. Supply a policy before serving an
archive whose manifest says no policy was included.

## Acceptance boundaries

Automated tests cover data integrity, privacy projections, historical inputs,
offline HTML structure, and JavaScript syntax. Interactive browser acceptance and
live host-agent adherence remain separate checks. Arbitrary environment replay,
hosted collaboration, domain analysis, and automatic evidence-support judgments
are outside this capability set.
