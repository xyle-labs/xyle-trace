---
name: claims
description: Register analytical claims, distinguish evidence from interpretation and conflicting sources, and explicitly review claims after upstream corrections. Use when drafting conclusions, attaching support or contradictions, or assessing whether a prior conclusion still holds.
---

# Make claims inspectable and reviewable

Use the configured `lineage.register`, `lineage.link`, `lineage.query`, and
`lineage.decide` tools. Discover current schemas and host-prefixed names. Each
takes one `request` object; link results are wrapped in `result`.

1. Express one assessable claim and register it with a stable `natural_key`,
   `type: "claim"`, and `data.text`. Choose `data.status` deliberately:

   | Status | Meaning to preserve |
   | --- | --- |
   | `supported` | Inspected evidence supports the stated scope. |
   | `partially_supported` | Only part of the statement has support. |
   | `unsupported` | Adequate backing is absent; the default. |
   | `conflicting_sources` | Relevant sources disagree; preserve the disagreement. |
   | `analytical_inference` | A reasoned conclusion beyond direct observation. |
   | `editorial_interpretation` | Interpretive framing rather than an observed result. |

2. Inspect the underlying records, exact evidence locations, assumptions, and
   run limitations. Attach `supports` or `contradicts` edges using `lineage.link`
   with `kind: "edge"`, `src` equal to the supporting/contradicting node ID,
   `dst` equal to the claim ID, and `data` containing explicit `provisional` and
   a useful `rationale`. Allowed supporting node types are dataset, artifact,
   indicator, evidence link, and source snapshot. A speculative association is
   provisional. Do not treat structural reachability as proof of the assertion.
3. Preserve both sides of conflicting evidence, including scope differences.
   Do not silently average contradictions or hide a conflicting source. Edges do
   not automatically change claim status: update the claim explicitly with its
   current `expected_revision` after judging what the sources establish.
4. Link a claim to its containing report artifact with an `appears_in` edge,
   `src` claim and `dst` artifact. Explain the claim via `lineage.query` with
   `mode: "explain"` to check that its path back to source bytes is inspectable.

## Review after change

Claim support status and `review_status` are separate. Upstream corrections and
other relevant mutations can persist `needs_review` on affected claims; replaying
registration must not be used as approval. An `impact` query is read-only and is
useful for finding affected claims, not for marking them reviewed.

Reinspect corrected records, their restored backing, contradictory evidence,
and any outputs computed from old values. Recompute results when needed using
reproducible-runs; a reviewed claim does not make a stale run current. Revise the
claim's text or support status if warranted, then explain it again to obtain its
current revision.

Record the actual assessment with `lineage.decide`: `kind: "review"`, a fresh
`natural_key` for this review, a specific `rationale`, and `claims` entries with
`claim_id`, `expected_revision`, and `review_status` of `reviewed` or `needs_review`.
Use `reviewed` only when that assessment has occurred; do not simulate a human's
approval. Review records are immutable: later corrections require a new review,
not replay of an old approval. On a revision conflict, reread and reassess.

Report remaining unsupported/conflicting claims and outstanding reviews plainly.
An explicit review records an assessment; it does not automatically turn a claim
into `supported` or certify the reproducibility of its inputs.
