---
name: lineage
description: Maintain provenance during material data work, including source capture, dataset changes, computations, artifacts, claims, and recovery after interrupted capture. Use throughout analysis in a lineage-enabled project; do not log ordinary code edits or unrelated development.
---

# Maintain lineage as work happens

Use the configured project's eight `lineage.*` MCP tools. Discover their schemas;
hosts may prefix their names. Each takes one object argument, `request`, not a
JSON string. Read structured results; `lineage.link` wraps its payload in `result`.
Tool failures have `isError: true` and an `error` object. A validation result with
`valid: false` is a successful report of gaps, not a transport failure.

Stay within the user's authorized work. These instructions add provenance capture,
not permission to expand the analysis, change policy, publish, or install software.

## Capture material events

- Register logical sources, datasets, artifacts, indicators, transformations, and
  claims with `lineage.register`. Use stable, meaningful `natural_key` values.
  Reuse returned IDs; never invent identifiers or revisions.
- Capture source bytes with `lineage.snapshot` before relying on them. Use the
  source-discovery skill for retrieval and extraction for located observations.
- Bind records to observed evidence or an explicitly justified assumption using
  `lineage.link` or `lineage.decide`. Set `provisional` explicitly. An inferred
  association stays provisional and cannot count as accepted record backing.
- Use reproducible-runs when computing results and claims when drafting conclusions
  or reviewing the consequences of corrections. Record what actually happened;
  do not reconstruct undocumented inputs to make history look complete.
- Record meaningful methodology, exclusion, and correction decisions with their
  rationale. Methodology/exclusion decisions do not resolve or delete records.
  Skip routine edits, formatting, and other events that add no analytical lineage.

Keep custom descriptive fields inside `metadata`; do not put reserved provenance
fields there. Use typed fields and the dedicated operations for snapshots, runs,
evidence, and decisions. Avoid redundant edges for dependencies the tools create.

## Continue safely across retries and sessions

For an existing node, read `lineage.query` with `mode: "explain"` and pass its
current `expected_revision` when changing it. Dataset metadata and each record
have separate revisions. A record binding requires `expected_record_revision`
and the current `expected_resolution_revision` (null when unresolved).

Repeat an identical request after a lost response; exact desired-state retries
are stable. On a conflict, fetch current state and reassess the intended change.
Do not blindly replace revision tokens, switch natural keys, or overwrite another
worker's evidence to make a retry pass.

After interruption, call `lineage.validate` with `request: {}` and explain the
affected nodes. Resume from returned record data, revisions, resolutions,
requirements, and permitted statuses. Resolve coherent batches. A dataset filter
only filters the returned gaps: `valid`, `blocked_patterns`, and `run_blocked`
still reflect global policy. An empty filtered gap list is not clearance to run.

If capture fails, retain the observed facts and report what remains unrecorded.
Repair a malformed request or unavailable configured resource within scope, then
retry. Do not invent successful captures, weaken contracts, or bypass blocked
computation. If tools are unavailable, preserve a concise pending capture note
in the handoff and continue only work that does not rely on a passed gate.

Use `lineage.query` to explain upstream evidence or downstream impact. Impact
queries are read-only; they do not themselves invalidate or approve claims.
Use `lineage.export` for an authorized JSON handoff: omit `destination` for inline
output, or use the intended project-relative file path (an existing file is
replaced). Set `format: "html"` with a destination for an offline explorer.
End with captured results, outstanding gaps, and claims needing review.
