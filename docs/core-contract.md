# Core contract and implementation handoff

Updated 2026-09-12. The original core plan, all eight tasks of the MCP plan, and
the public-release-readiness plan (locator verification, contract severity,
ungoverned/orphan reporting, snapshot read-back, the assumption ledger, the
Claude Code plugin, CI, and Apache-2.0 licensing) are implemented. This
document records the behavior that the MCP layer must preserve.

## Dataset identity

Register a `dataset` node before writing its records. Record and resolution store
methods accept that node's ID or natural key within the specified project; writes
persist the canonical node ID. Contract `dataset` selectors accept either form.
Ambiguous references are rejected. A resolution must name an existing record.
An empty, missing, or unregistered contracted dataset fails validation.

The store can retain incomplete evidence while it is being researched. Existence,
type, and backing requirements are enforced by contract validation; storing a
resolution alone does not certify it.

## Evidence and assumptions

The twelve generic node types are unchanged. Required metadata is defined in
`src/xyle_trace/models/provenance.py` and checked by the contract validator.
Additional descriptive metadata is allowed on these nodes.

| Node | Required `data` fields |
| --- | --- |
| `source_snapshot` | `source_id`: ID of a source in this project; `content_hash`: `sha256:` followed by 64 lowercase hexadecimal characters |
| `evidence_link` | `snapshot_id`: ID of a source snapshot in this project |
| `decision` used for an assumption | `assumption_type`: nonempty string; `rationale`: nonempty string |

A contract's `evidence` requirement has two levels. `exact_locator` requires a
structured locator:

```json
{"snapshot_id": "source_snapshot:…", "locator": {"kind": "table", "value": "Table 1, row A"}}
```

Locator kinds are `page`, `table`, `section`, `path`, `range`, and `fragment`.
The value is a nonempty string identifying the location within the referenced
snapshot. A boolean, bare URL string, or whitespace-only value does not satisfy
this structure.

`verified_locator` additionally requires a nonempty `Locator.anchor` string and
checks it against the captured bytes: the validator reads the snapshot's blob
from the configured snapshot root, re-hashes it, and confirms the anchor
literally occurs in the decoded text. A blob that no longer hashes to the
snapshot's recorded `content_hash` fails as `locator_snapshot_mismatch`, never
silently passes. A blob that is not decodable as text (a binary PDF, for
example) reports `locator_unverifiable`, not verified and not failed — the
honest move is to capture the extracted text as its own snapshot and anchor
into that, which the extraction skill now documents. Missing anchor, missing
snapshot root, unreadable bytes, and an anchor that is not found each report
their own violation kind (`evidence_without_anchor`, `locator_unverifiable`,
`locator_not_found`) rather than being folded into one generic failure.

Both levels check structure and the reference chain; neither proves that the
cited passage actually *supports* the recorded value — that a caller-supplied
hash matches the bytes it names is exactly what `verified_locator` closes for
text content, but the semantic judgement that the passage supports the value
remains a human or agent call. Capture code must calculate the hash.

Evidence and assumption metadata accept `provisional: true`; provisional backing
cannot satisfy contracts. Omitted `provisional` means an observed or reviewed
assertion in the low-level models. The application service requires an explicit
boolean for both observed and provisional backing. Assumption
types remain domain-defined strings; domain-specific vocabularies belong in examples.

A source snapshot's data cannot be overwritten under the same natural key.
Register a new snapshot version for changed content. Existing nodes with incomplete
metadata remain inspectable, but cannot satisfy these backing requirements.

## Contract parsing and execution

```yaml
- dataset: inputs
  require:
    every_row: [evidence_backed, assumption_backed]
    evidence: exact_locator
  blocks: [analysis.*]
  severity: block
```

Unknown or duplicate keys, malformed YAML, empty row-status lists, invalid statuses,
and mistyped fields are errors. `decision_backed` is accepted as a YAML alias for
`assumption_backed`. An explicitly empty contract file (`[]`) means no policies.

`severity` is `block` (the default, and the only value earlier policies could
express) or `warn`. A `warn` contract's violations are reported by `validate`
exactly like a `block` contract's, but they never appear in `blocked_patterns`
and `run_guarded`/`record_run` never refuse to start because of them —
`is_blocked`/`blocked_runs` only ever collect patterns from `block`-severity
violations. Use `warn` for a policy you want visibility into without giving it
the power to stop analysis.

`validate_contracts` also reports two violation kinds that come from the graph
itself rather than from evaluating a specific contract, both always `warn`,
gated by a `sweeps` keyword (default `True`; see below): `ungoverned_dataset`
for a dataset holding records that no configured contract names (by ID or
natural key), and `orphan_artifact` for an artifact node with no incoming
*`produced`* edge — an incoming `used_input` (a run consumed it) or
`appears_in` (a claim cites it) edge says nothing about what made the file,
so neither clears it; only a `produced` edge answers "what made this file".
Both exist so that a project's blind spots stay visible without requiring a
contract for every dataset or making a stray artifact an error. Neither can
block a run.

`validate_contracts` returns violations, including each failing rule's
`blocked_patterns`. `blocked_runs` uses those patterns so a stricter rule does not
also block a passing rule's runs when both name the same dataset. Older or manually
constructed violations without these patterns retain the conservative dataset fallback.
`run_guarded(store, project_id, run_name, contracts, operation)` evaluates matching
contracts immediately before invoking the supplied zero-argument callable. A
failure raises `RunBlockedError` with the violations and never calls the operation.
Run-pattern matching is case-sensitive on every platform.

This is preflight enforcement for callers using `run_guarded`. It does not record
execution metadata or isolate a running computation from concurrent edits. The
local execution wrapper uses this gate and consumes retained input versions.
Registering a generic `run` node is only metadata storage.

## Traversal and corrections

Traversal includes explicit edges and these recorded dependency references:

- dataset → evidence/decision, through its current record resolutions;
- evidence → snapshot, through `snapshot_id`;
- snapshot → source, through `source_id`.

These references need no duplicate graph edges. `explain` returns ancestor IDs,
source IDs, node and edge metadata, and the implicit dependency pairs. JSON export
retains records, resolutions, and node metadata needed to reconstruct them.
Traversal is conservative: it includes provisional relationships, whose qualifiers
remain visible in explanation metadata. It is not a certification of those links.

Changing record data clears that record's resolution. Identical retries preserve
it. Changing an evidence or decision node clears resolutions targeting that node.
Changing or adding records, replacing resolutions, or changing an existing node
also marks reachable downstream claims with `data.review_status = "needs_review"`.
The claim's support/conflict `status` is preserved. Resolving an input again does
not automatically approve previously affected claims.

Record edits, resolution invalidation, and claim marking commit together; a failed
batch rolls back all three. `impact` itself remains a read-only query. The store
currently represents current record values and resolutions, not historical row
versions. Managed runs retain the concrete inputs they consumed in immutable bundles;
this does not provide a history of every edit to an ordinary dataset record.

## Compound writes and revisions

`Store.transaction()` now groups multiple public write calls atomically. Nested
calls use savepoints; a failed nested operation is rolled back even when its
exception is caught inside the outer transaction. Individual write calls retain
their existing behavior. Keep network transfers and computations outside these
short write transactions.

`core.revisions` provides canonical JSON revisions, `check_revision` for updates
that accept identical retries, and `require_revision` for strict preconditions
such as the record value being resolved. Run checks and writes inside the same
transaction. The application service enforces these preconditions; raw Store methods remain
available for trusted lower-level operations and do not implicitly require
caller revisions.

`Store.transaction(write=False)` provides a consistent read snapshot without
reserving a writer. Service validation, queries, and graph export use it so their
responses do not mix record or metadata versions across concurrent commits.
Tests cover composite rollback, nested failure recovery, interruption rollback,
competing updates, and a second connection committing during an explanation.

## Typed application operations

The service uses Pydantic's `JsonValue` for arbitrary dataset content. The package
now requires Pydantic 2.5.2 or later within v2, including its boolean serialization
fix. [Pydantic changelog](https://pydantic.dev/docs/validation/latest/get-started/changelog/).

`Service(store, project_id, contracts=...)` binds an existing initialized project
and an explicit contract list. Alternatively, configure `contracts_path=...` to
read policies from a file; exactly one policy source is required. It has eight
implemented operations: `register`, `link`, `decide`, `snapshot`, `validate`,
`query`, `export`, and `record_run`. Each accepts a validated request model or a dictionary and
returns typed state with opaque revisions. A `ServiceError` exposes a structured
`error` with a stable code, message, and details; revision conflicts identify the
affected resource. Requests cannot override project/database configuration.

`models/operations.py` defines their request/result schemas. The existing core
query, validator, exporter, and callable guard functions remain usable directly.

Registration uses a type discriminator. For example, after initializing the project:

```python
from xyle_trace.service import Service

service = Service(store, "p", contracts=contracts)
registered = service.register({
    "type": "dataset",
    "natural_key": "inputs",
    "records": [{"key": "a", "data": {"value": 10}}],
})
service.register({
    "type": "dataset",
    "natural_key": "inputs",
    "records": [{
        "key": "a",
        "data": {"value": 11},
        "expected_revision": registered.records[0].revision,
    }],
})
```

Source, dataset, artifact, indicator, claim, transformation, and agent-run nodes
have distinct typed data models. Descriptive metadata is nested under `metadata`;
reserved provenance and revision keys are rejected there. Record data remains
arbitrary JSON, since its fields belong to the user's dataset. Dataset and record
revisions are independent, so editing a row does not require changing dataset metadata.

Evidence links require a valid same-project snapshot/source chain. Evidence and
assumption operations can resolve a batch in one call; each binding supplies the
expected record revision and current resolution revision (null for no resolution).
The record check is strict even on an otherwise identical retry. Provisional
backing may be retained without bindings, but cannot resolve records. If an exact
locator required by a contract is still missing, the response retains that explicit
violation rather than certifying the record. Changing backing invalidates old
approvals; only records explicitly included in that update are re-resolved.

Decision variants are assumption, correction, exclusion, methodology, and review.
Only assumption decisions can back records. Corrections atomically record a
rationale and edit the specified rows, invalidating old approvals. Exclusion and
methodology decisions record subjects and rationale; they do not silently delete
rows or fabricate assumptions. A decision cannot change kind under the same key.

Claim review is a separate operation that creates an immutable review decision and
REVIEWED_BY edges, verifies the expected claim revisions, and records review state.
A repeated review succeeds only while the reviewed claim states still match its
stored receipts. After a correction, a new review decision is required. Registration
preserves server-owned review state; editing claim content marks it for review.
Adding or changing an edge also flags affected claims, while an identical retry
preserves timestamps and review state. No edge automatically changes a claim's
support/conflict status. Review-state changes alone do not alter analytical content
or invalidate downstream calculations.

Mutation responses include the current node/edge revision, touched record and
resolution revisions, affected claim states, and remaining violations for touched
records. `violations` can also carry `warn`-severity `ungoverned_dataset` entries
for the dataset just written to, alongside any `block`-severity gaps for its
records — a mutation response is not only block-severity results. Register, link, and decide each use one transaction, including invalidation and review
side effects. Unsupported fields, duplicate keys, stale revisions, invalid subjects,
or a failed final item leave the entire operation uncommitted.

File-backed services load policies at construction and before every validation,
run start, or register/link/decide operation. Missing or malformed files return `contract_error`
without falling back to cached policies. An explicit `[]` remains valid. Query,
snapshot, export, and run finish do not require a fresh policy read after construction.

## Validation, queries, and export

`validate({})` evaluates all configured contracts through the core evaluator.
Each gap includes the original violation, canonical dataset ID when available,
current record value, record/resolution revisions, current resolution target,
all relevant contracts, and the intersection of their permitted backing statuses.
Missing, ambiguous, empty, and legacy datasets remain explicit gaps; missing
record context is null. A fresh service session can use these revisions directly
in a resolution request.

Optional `dataset` and `run_name` selectors filter only the returned gaps. `valid`,
`blocked_patterns`, assumption metrics, and the requested run's `run_blocked`
decision always reflect the full policy evaluation. Therefore an empty filtered
gap list does not imply global validity. Dataset scopes accept canonical IDs or
natural keys; an unknown selector must name a configured contract or it fails.

Assumption metrics count each record once across the evaluated distinct datasets,
including when contracts use both a dataset's ID and natural key. The numerator
counts assumption-backed records that satisfy all applicable contracts. Missing
resolutions are `unresolved`; core-reported provisional backing is `provisional`;
other failing resolutions are `invalid`. Legacy dataset failures count their
records as invalid. Valid evidence contributes to the denominator without being
an assumption. No records yields `rate: null`.

For file policies, the returned `policy_hash` is SHA-256 of the exact bytes parsed
from one fresh read, using `parse_contracts` shared with the existing loader.
For an explicitly configured in-memory contract list, it is the canonical JSON
revision of `{"contracts": [contract.model_dump(mode="json"), ...]}`. Run-start
policy receipts use this same loading boundary.

`query` implements exactly upstream, downstream, explain, impact, and content.
Every response includes the requested node alongside reached nodes, full node/
edge metadata with revisions, and implicit dependencies among included nodes.
Walk modes accept nonnegative `max_depth`; zero includes only the requested node.
Explanation adds current records and resolution revisions for included datasets,
preserving legacy records under their stored identifiers. Locators and provisional
qualifiers remain visible; traversal does not certify them. Impact returns the
affected claims without modifying their review state.

`content` reaches no other nodes — its `nodes` list holds only the requested
snapshot, honoring the same "every response includes the requested node"
guarantee as the walk modes — and returns captured bytes in a `content`
field: the managed snapshot's path, content hash, byte size, a `verified`
flag, and decoded `text` when the bytes are text. `text` is always the full
decoded document, never a byte-count prefix — every caller, CLI or MCP, gets
the same complete text, decoded with
`xyle_trace.core.locators.decode_text`, the same detection-and-decoding
decision `verified_locator`'s anchor check makes on the same bytes (BOM-guarded
UTF-16, then UTF-8, then latin-1 behind a printability gate; `None` when the
bytes are not text). A truncated preview next to `verified: true` would let a
caller mistake a partial document for the whole one, and decoding it with a
different encoding than the one detection accepted would return mojibake
labelled `verified: true`; sharing one decode path between `content` mode and
`verified_locator` rules out both. `verified` is computed by re-reading the
blob and re-hashing it against the snapshot's recorded `content_hash`, never
inferred from the filename, so a tampered or corrupted blob comes back
inspectable (`verified: false`) instead of raising. This is the same
read-back path `verified_locator` anchor checks use internally; `content`
mode is what makes the retained bytes inspectable outside that check, through
`xyle-trace show --snapshot` or the equivalent `query` request.

`export` accepts JSON and HTML. JSON without a destination returns the existing graph
export inline. JSON with a project-relative destination writes the same canonical
UTF-8 JSON bytes atomically and returns the relative path, content hash, and node/
edge counts. Both JSON forms preserve legacy records and share the same hash.
HTML requires a destination and returns the explorer file's hash and counts. File
export requires a configured root and rejects root escapes, database files and
sidecars, the policy file, and managed snapshots, including aliases to protected
files. Invalid formats and destinations create no files; failed publication
removes temporary output and preserves an existing destination.

## Source snapshot capture

Configure a trusted project root with `Service(store, project_id, contracts=...,
root=project_root)`. The `snapshot` operation accepts either
`{"kind": "local", "source_id": "source:…", "path": "data/input.csv"}` or
`{"kind": "http", "source_id": "source:…"}`. HTTP capture uses the source's
registered URI. Requests cannot choose storage locations or transfer limits.

Local inputs must resolve to regular files within the project root; absolute
paths and symlink escapes fail. Managed `.lineage/snapshots/` directories cannot
be symlinks. Capture copies and hashes one stream into a temporary file there,
then atomically publishes the complete blob under its SHA-256 digest. Existing
blobs are verified before reuse; corruption fails without overwriting them.
Captured content remains opaque data and is never parsed or executed.

Snapshot identity combines the source ID and computed content hash. Identical
bytes return the original snapshot, including its timestamp and retrieval metadata;
changed bytes create a new immutable node. Different sources may share one blob
while retaining distinct snapshot nodes. Responses include the node revision,
hash, byte size, managed relative path, and `already_existed` (the snapshot node,
not merely the blob). Node data includes `source_id`, `content_hash`, `size_bytes`,
`path`, and `retrieval`; `created_at` records the first successful capture time.
Local retrieval records the resolved relative input path; HTTP retrieval records
the final URL while preserving the logical source reference.

`CaptureLimits` configures a default 25 MiB byte limit for both methods, a
30-second socket-operation timeout for HTTP, and at most five redirects.
The timeout follows the standard library's blocking-operation semantics, rather
than a total transfer deadline; DNS uses the system resolver.
[Python HTTP client documentation](https://docs.python.org/3/library/http.client.html).
Every redirect is checked for HTTP(S), URL credentials, and public targets.
The default transport rejects nonpublic DNS results and connects directly to a
validated address, retaining TLS certificate and hostname checks. It uses no
environment proxies, cookies, or authentication headers. Trusted callers may
inject an HTTP transport for deterministic tests.

Transfers occur before the short database transaction, and capture refuses to
run inside an already-open write transaction. Registration checks that the source
revision still matches the one read before capture. Failed transfers remove
temporary files and make no graph writes. A failed graph commit or concurrent
source edit can leave a complete unreferenced blob for a retry to reuse; automatic
garbage collection remains deferred.

## Reported run capture

`record_run` accepts `action: start` or `action: finish`. It records external
execution reports; it never executes code, infers success from existing files, or
claims to isolate a computation. Every result and run node has
`capture_mode: reported` and `reproducible: false`. Request schemas reject caller
overrides of those fields and of server-computed fingerprints.

Start requires an execution key, registered transformation ID, distinct input
IDs, parameters, and an explicit code reference or null for unknown. The execution
key is the run's natural key; a new execution needs a new key. The logical run
name comes from the transformation's natural key, so callers cannot rename a run
to evade blocking patterns. Inputs may be datasets, artifacts, or source snapshots
in the same project. Ambiguous and legacy dataset records are rejected.

Start reads and hashes one fresh policy version, then uses `run_guarded` to check
matching contracts. A blocked start returns `run_blocked` with violations and
makes no graph writes. After file observation, a short write transaction repeats
that guard against current records and verifies that the transformation and input
states still match. It commits the started run, its policy hash and evaluated
contracts, input fingerprints, parameters, code reference, and generated_by/
used_input edges together. A start receipt is preflight evidence, not permission
to skip the callable's own `run_guarded` check immediately before computation.

Fingerprints combine the node-data revision, dataset record and resolution
revisions where applicable, the declared file path, and its observed SHA-256 hash
or null. File hashes are returned separately from these composite fingerprints.
Hashing uses regular files inside the configured root, outside write transactions;
root escapes and detected file changes fail. Source snapshot bytes must match
their registered content hash. This captures observations of metadata and bytes,
not immutable copies of arbitrary inputs. `capture_gaps` records unobserved
execution, unknown environment, missing code references, unavailable files, and
input contents not retained as managed snapshots. The environment remains null;
the service's own runtime is not evidence of the external computation's environment.

Finish requires the run revision and an explicit succeeded, failed, or partial
status. Success requires at least one output and no failure detail; failed and
partial outcomes require a failure detail and may have no outputs. Output IDs
must reference same-project datasets, artifacts, or indicators. Optional output
paths must agree with a registered path when one exists. The service hashes
available files itself and rechecks output state before committing the terminal
report, finish timestamp, and produced edges. Missing declared files prevent a
success report; failed or partial reports retain null hashes and explicit gaps
for them, without adding produced edges for those missing files. Metadata-only
outputs remain possible and never receive invented file hashes. Finishing does
not reapply changed policies to erase an observed failure or earlier execution.

Exact retries preserve timestamps and graph state. Every start retry rechecks
current policies; changed input fingerprints, transformation revision, policy
bytes, or start parameters require a new execution key. A delayed matching start
after finish returns the terminal run and never restarts it. Terminal finish
reports are immutable: matching reports are idempotent, including a retry with
the original start revision; changed reports or output observations conflict.
Restarting a service leaves unfinished runs started and never reruns computation.
The local wrapper below retains and consumes immutable input contents and reports
verified replay separately. This reported interface cannot set `reproducible: true`.

## Local execution and retained versions

`xyle-trace-run` and `core.execution.execute` support trusted, self-contained
standard-library Python programs with a JSON input/output contract. They are local
interfaces; the MCP surface remains exactly eight tools. See [execution and replay](execution.md)
and [the runnable example](../examples/managed/demo.py).

A managed run retains code, parameters, concrete dataset records/resolutions,
lineage context, direct file input bytes, parsed policy receipt, and environment
identity in a content-addressed bundle. Generated bytes use the same atomic blob
storage as source capture without inventing SourceSnapshot nodes. New runs check
current gates and recheck metadata and policy at reservation. An execution key
reserves one run before the subprocess starts; an ambiguous started run is not
automatically executed again.

The program runs with `-I -S`, a temporary working directory, a minimal environment,
and no site packages. It receives retained JSON and emits finite JSON; output
is canonicalized and retained, then linked as an Artifact. Timeouts, nonzero exits,
invalid/missing output, and mismatching replay are recorded failures. The configured
timeout bounds the direct subprocess; this is not an OS sandbox or descendant-process
resource manager. Managed bundles and outputs use the default 25 MiB blob limit.

Replay verifies original bundle/output integrity and interpreter/platform identity,
then consumes those retained contents and the original policy receipt rather than
current logical records. A successful matching replay has `capture_mode: managed`,
`replay_verified: true`, and `reproducible: true`; its original run is not rewritten.
These fields mean a matching canonical JSON result was actually observed under the
recorded environment, not universal determinism or portability. Current logical
node edges remain useful for impact; the bundle is authoritative for historical
input contents. Current-data policy gaps remain unresolved by replay.

## Offline HTML export

`lineage.export` accepts `format: html` with a required project-relative destination,
using the existing protected atomic file export path. Its response remains a file
reference, hash, and node/edge counts. JSON inline/file behavior is unchanged.
The CLI also accepts `export --format html --out ...`, rejects unknown projects,
and atomically publishes output. It protects the database and sidecars, including
aliases. CLI `--out` is an explicitly chosen path; the MCP/service exporter adds
configured project-root, policy-file, and managed-snapshot protections.
The CLI also recognizes legacy project data without an explicit Project node;
an entirely unknown project is rejected.

The standalone file embeds graph data and the core's implicit dependencies. It
supports type/text filtering, upstream/downstream traversal, and inspection of
node metadata, dataset records/resolutions, and edge qualifiers. It loads no remote
resources. Embedded JSON escapes markup delimiters and displayed values use text
nodes, so captured source content cannot become HTML. Provisional and historical
paths remain visible; the explorer is not an evidence certification mechanism.

## CLI read commands

`xyle-trace show --project P --db DB --root ROOT --snapshot SNAPSHOT_ID`
wraps the `content` query mode: it reads captured bytes back through the
service, re-hashes them, and prints the JSON `ContentRef` (path, content hash,
size, `verified`). `--text` includes decoded text when the bytes have one; it
is dropped by default so a large captured file does not flood the terminal.
`--root` is required because content lookups need the project root captured
bytes live under, which the sibling-of-the-database guess `validate` uses is
not guaranteed to be. `show` opens the service with `contracts=[]`: reading
bytes back is not a policy decision, so there is nothing to validate against.

`xyle-trace ledger --project P --db DB [--open-only]` prints the assumption
ledger computed by `queries/ledger.py`: every assumption decision, whether it
currently backs a record (`standing`) or backs none (`unbacked`), and which
records it backs. `--open-only` filters to `standing` entries — the ones a
reviewer should look at before trusting a result they support. See
[Limitations](#limitations) for what `unbacked` deliberately does not claim.

## Optional MCP adapter

Install from this repository with `pip install '.[mcp]'` (or `'.[dev,mcp]'` for
tests). The base package does not require the SDK. In Claude Code, the
[plugin route](agent-setup.md#choose-an-install-route) (`/plugin marketplace add
xyle-labs/xyle-trace` then `/plugin install xyle-trace`) installs the
five skills and wires `/mcp` in one step, but still requires the separate
[Python package installation](quickstart.md#1-install) so `xyle-trace-mcp` resolves on `PATH` — a
plugin has no build step. Both entry points serve stdio directly:

```bash
xyle-trace-mcp --project p --db /project/.lineage/graph.db \
  --root /project --contracts /project/contracts.yaml
# Equivalent: python -m xyle_trace.mcp with the same arguments.
```

All four arguments are required. Initialize the project first using the existing
CLI; startup rejects missing databases, unrecognized schemas, missing projects,
and invalid policies. `Store(..., create=False)` opens SQLite with `mode=rw`, so
even a database removed after startup cannot be silently recreated. Server
configuration is frozen and paths are resolved at startup. Optional startup flags
`--capture-timeout`, `--capture-max-bytes`, and `--capture-max-redirects` configure
the existing transfer limits; requests cannot override them.

Exactly eight tools are exposed: lineage.register, lineage.snapshot,
lineage.record_run, lineage.link, lineage.decide, lineage.query, lineage.validate,
and lineage.export. Each takes a single `request` object matching the service
schema. Raw arguments are checked before SDK coercion, including unknown outer
fields and JSON strings where an object is required. Successes contain equivalent
JSON text and structured content. `lineage.link` uses an explicit
`{"result": <MutationResult or EdgeResult>}` wrapper for its two result variants;
other successes directly match their service result models. Expected errors have
`isError: true` and an `error` object with code, message, and details in both forms.
Validation gaps are successful results, not tool failures.

The SDK runs synchronous handlers in workers. Each invocation creates and closes
its own Store in that worker; no SQLite connection is shared across threads.
Startup validates policies once, and workers use the trusted
`validate_policies=False` service constructor option to avoid an extra policy
read before operations that reload policies themselves. Query, export, snapshot,
and finish retain their existing policy-loading behavior. Imports open no database
or server; startup diagnostics and logs use stderr, preserving protocol stdout.

The installed and tested SDK is 2.1.1, within the optional `mcp>=2,<3` constraint.
Its generated argument and union-output behavior was checked against local SDK
code and the [SDK structured output documentation](https://py.sdk.modelcontextprotocol.io/servers/structured-output/).
Tests exercise real stdio clients using the modern protocol and the legacy
handshake, including two independent servers competing over one database.
All tool result schemas are exercised. The continuation fixture also covers both
handshakes. This is SDK compatibility evidence; live Codex/Claude skill behavior
remains a separate unverified check.

A separate installation without MCP passes 408 core tests (four MCP test
modules skip: `test_main_discovery.py`, `test_server.py`, `test_stdio.py`, and
`test_workflow.py`), imports the service, and runs the installed core CLI. Its
optional MCP command exits with installation guidance rather than a traceback.
With MCP installed, 439 tests pass.

## Portable agent skills

The canonical [skills](../skills/) cover ongoing lineage, source discovery,
extraction, managed execution/replay and reported runs, and claims. They use the implemented request envelopes,
revision checks, explicit provisional states, batched resolution, and correction
review behavior. They contain no domain-specific policies. The run skill explicitly
distinguishes reported capture from a verified matching managed replay.

[Project setup](agent-setup.md) documents explicit copying into a consuming
project, the host-neutral stdio command, and Codex/Claude Code configuration checked
against official documentation on 2026-09-06. Nothing is installed globally and no
host configuration is rewritten. A project guidance snippet makes the intended
ongoing workflow explicit; a skill description alone cannot ensure it loads.

All five skills pass the skill-creator structural validator. Registration examples,
JSON/TOML client configurations, the sample contract, and local links were checked.
These checks do not certify live agent behavior. The protocol continuation fixture
below verifies the tool workflow; a live host comparison remains separate work.

## Protocol continuation fixture

[The minimal example](../examples/minimal/README.md) supplies two CSV source
releases, records, contracts, and replay instructions. `tests/mcp/test_workflow.py`
initializes through the CLI and uses real stdio tools for every subsequent graph
read and write. It covers all eight tools under the modern `2026-07-28` protocol
and legacy handshake with MCP SDK 2.1.1.

The first session binds two observed records with one evidence operation, captures
a reported sum of 30, and attaches a reviewed claim. A new captured release changes
one record: a correction clears its resolution, blocks validation/run start, and
persists claim review. No blocked run is created. The client/server then close.

The second server verifies `needs_review` before any mutation. It recovers from
fresh validation work items and a saved, unmodified snapshot receipt plus its local
bytes, without first-session in-memory IDs or direct database repair. Validation
provides record revisions and requirements; the receipt provides the new snapshot
ID. Recovery restores backing but leaves the claim pending until recomputation,
updated support/text, and a new explicit review. Historical outputs remain present.

Measured resolution calls are three per batch: validate → link → validate for
initial evidence and corrected evidence; validate → decide → validate for a
separate two-record hypothetical assumption scenario. Registration/capture setup,
retry probes, persistence inspection, runs, claim review, and export are separate
actions. This does not assert a three-call total for an analytical session.

The fixture verifies hash-to-byte agreement, stale-write refusal, stable retries,
preserved review state, graph equality across retries, and JSON export without
duplicates. Mutation response `claims` lists affected claims for that call: an exact
retry may return an empty list while preserving the same stored state. Run capture
remains reported and non-reproducible. Scripted fixture reviews are test assessments,
not evidence of live agent behavior or human approval.

## Database compatibility

The physical schema remains version 1. Fresh databases and recognized unversioned
lineage databases are stamped 1. Unsupported versions and unrelated schemas are
rejected without overwriting their version or adopting their tables.

Early builds permitted records keyed by a natural key or an unregistered dataset.
Such records remain available in JSON export, including when canonical records
also exist. Contracts fail on legacy natural-key records rather than ignoring them.
To recover them, export the old database, register the datasets in a fresh store,
replay records under their canonical IDs, and explicitly re-establish valid backing.
No automatic data migration or fabricated approvals are performed. Raw inspection
is also available with `records(..., resolve=False)` and `resolutions(..., resolve=False)`.

## Limitations

What follows is the complete list of things this toolkit does not do, or does
not prove, even when its commands succeed. This is the section that decides
whether to trust it.

- **MCP `record_run` is reported capture, never reproducible.** It records an
  external process's own report of what it did; it does not execute anything
  and cannot verify the report. Every result from it has `capture_mode:
  reported` and `reproducible: false`, by construction, not by omission. Only
  `xyle-trace-run` (and `core.execution.execute`) actually executes a
  program and can set `reproducible: true`, and only for the programs it
  supports.
- **The local runner is a replay contract, not a sandbox.** It supports
  trusted, self-contained standard-library Python programs with a JSON
  input/output contract. `-I -S`, a temporary working directory, and a minimal
  environment narrow what a program can accidentally depend on; they do not
  isolate it from the filesystem, clock, or network the way an OS-level
  sandbox would, and the retained bundle is not a distributable environment
  image — replay uses the same interpreter and platform identity it was
  recorded under, not a reproduction of them elsewhere.
- **`exact_locator` proves presence; `verified_locator` proves the anchor is
  in the bytes — neither proves the passage supports the value.**
  `exact_locator` checks that a structured locator exists. `verified_locator`
  additionally checks that its `anchor` string literally occurs in bytes that
  re-hash to the snapshot's recorded `content_hash` — so a tampered or
  mismatched blob is caught, not trusted. Binary payloads without a text layer
  (a PDF with no extracted text, for instance) report `locator_unverifiable`
  rather than passed or failed, because there is nothing to search. Capture
  the extracted text as its own snapshot and anchor into that when this
  matters; see [extraction](../skills/extraction/SKILL.md). Neither level
  proves the cited passage actually *supports* the recorded value — that a
  human or agent read the passage and judged it relevant is a semantic
  determination no automated check here makes.
- **Node IDs are derived from the project ID.** Renaming a project re-derives
  every node ID in it, which breaks every reference that named an ID
  explicitly (though natural-key lookups keep working). Retained execution
  bundles are the one exception: they keep the ID they were created under, not
  a re-derived one, so a replay bundle survives a rename even though live
  lookups by ID would not. Choose a project name deliberately before you have
  much to lose from changing it.
- **Live agent adherence to the five skills is unverified.** SDK protocol
  compatibility — that the MCP tools respond correctly over stdio, under both
  the modern and legacy handshakes — is tested (see [protocol continuation
  fixture](#protocol-continuation-fixture)). Whether a given host's agent
  actually reads and follows the installed skills during real work is a
  separate behavioral question this repository's tests do not answer.
- **The toolkit records what the agent reports, nothing it doesn't.** It has
  no way to detect a source the agent consulted but never registered, a
  transformation it ran but never recorded, or a claim it drafted but never
  linked to evidence. The graph is a complete record of what was captured, not
  a complete record of what happened.
- **The assumption ledger reports `standing` or `unbacked`, never `retired`.**
  `xyle-trace ledger` (`queries/ledger.py`) tells you which assumption
  decisions currently back at least one record (`standing`) and which back
  none (`unbacked`) — deliberately not a third `retired` state, because a
  correction deletes the resolution row linking the old assumption to the
  record it backed (`Store.put_records`), so by the time anything asks, the
  very link that would distinguish "superseded by better evidence" from
  "never used" is already gone. An `unbacked` assumption may be either; the
  toolkit will not guess which, and no field is added to fake the distinction.
- **Ungoverned datasets and orphan artifacts are warnings, never blocking.**
  `ungoverned_dataset` (a dataset with records no contract governs) and
  `orphan_artifact` (an artifact with no incoming *`produced`* edge — an
  incoming `used_input` or `appears_in` edge proves consumption or citation,
  not production, and does not clear it) are always `severity: warn` and
  never added to `blocked_patterns`, so neither can stop a run. Both are
  whole-project sweeps, computed once against every contract rather than
  once per contract (`validate_contracts(..., sweeps=True)`, the default);
  `run_guarded`'s per-run preflight evaluates only the contracts applicable
  to that run and turns the sweeps off (`sweeps=False`), since "nothing
  governs this" is not a safe conclusion from a filtered contract set. Both
  kinds are reported by `Service.validate` (`_validation` always runs the
  sweeps). In a mutation response, `ungoverned_dataset` appears when that
  mutation touches a record in the ungoverned dataset; `orphan_artifact` does
  not currently surface there — registering or linking an artifact does not
  touch dataset records, which is what mutation responses filter violations
  against — so seeing it requires a `validate` call. Adding a scratch dataset
  or an ad hoc artifact to a project cannot break its existing contracts.

## Verification and next phase

From an environment with the project development dependencies installed:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
PYTHONPATH=src .venv/bin/python -m xyle_trace.cli.main --help
```

`tests/test_provenance_workflow.py` exercises registration, an execution refusal,
evidence resolution, successful computation, CLI validation and explanation,
correction, persisted claim review state, and recovery from a fresh store session.
The remaining tests cover policy errors, project isolation, immutable snapshots,
rollback, schema compatibility, and export of legacy records.

The MCP server and skills work is complete, along with the toolkit-only
completion work: retained-input execution/replay and HTML exploration. Domain
work is no longer excluded: consuming projects live outside this repository and
outside the package, and feed requirements back against this contract. Toolkit
completion does not assert that any particular domain workload built on top of
it is complete.

The next product milestones are onboarding, result-first visualization/reporting,
and validation through a separate consuming project. See [ROADMAP.md](../ROADMAP.md)
for the maintained scope and acceptance criteria; these improvements are not
implied to be complete by the protocol and execution work above.
