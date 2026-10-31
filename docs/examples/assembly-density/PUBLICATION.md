# Public example review

This directory is an explicit publication selection from a real Codex/Xyle
Trace run. It is not a copy of the consuming project or its private store.

## Included

- Aggregate city and daily counts, population denominators, rates and exclusions.
- Original public publication URLs, representation notes and captured-byte hashes.
- The resulting map and source graph, with selected original node identities
  and recorded dependency paths. All 21 projected paths were checked.
- A silent 33.7-second recording of actual setup, prompt and tool activity.
  Cuts condense time; redactions change local details only, not tool outcomes,
  calculations or city results.

The current revision adds the approved logo in the editorial header during setup
and tool activity. The underlying recording and result scenes are unchanged.
The standalone map and source-graph SVGs now carry the logo as well; their data
and dependency paths are unchanged. Prior reviewed GIF hashes remain approved
for history scans. See the [brand review](../../branding.md#publication-review)
for the separate social card.

## Excluded or redacted

- Machine user paths and workspace locations are replaced with explicit redaction
  markers in setup output. Unrelated tool connections, account/model/runtime
  footers and local directory displays are hidden or cropped.
- No credentials, environment files, host configuration, agent sessions, raw
  terminal streams, raw execution logs or private cross-project material.
- No raw agency HTML/JSON, copied workbooks/archives, event-level observations,
  assembly topics, organiser names, street locations or participant details.
- No SQLite store, retained source bodies or executable replay bundles.
- Hidden SVG projection metadata is removed. The separate public provenance view
  contains selected identities and relationships, without original record payloads
  or execution-environment metadata.

The diagram's `result.json` node describes the original managed result retained
with the consuming project. The published `summary.json` is a deliberately
minimized view, with a different content hash; it is not substituted for that
retained artifact. This public selection cannot independently replay the original
extraction. The standard-library example checks the published ratio arithmetic.

## Review and checks

- Decoded all **674 composed GIF frames**. Applied local OCR to all **160 unique
  frames**, covering every displayed frame, and checked for user paths, private
  identities, unrelated project names, runtime footers, credentials and chat links.
- Inspected the recording's scene contact sheet and activation, prompt, source,
  calculation, map and graph views. Genuine tool activity remains visible.
- Scanned the publication text and repository history with the repository policy
  guard and redacted Gitleaks checks. The guard retains its rejection of other
  binaries and allows only reviewed media at exact paths, sizes and SHA-256 hashes.
- Numeric totals and rates match an independent source check; the original
  retained calculation replay matched. Source coverage and the lower-fidelity
  Rostock extract remain explicit in the example.
- Verified the selected source-to-KPI paths and public file links. The original
  publication passed all 720 tests, including media-policy regression tests.
  Branding changes are verified separately in their pull request.

Reviewed GIF: `.github/assets/codex-workflow.gif`

SHA-256: `dbc3069d7dcdaf4c6da182545ce79c125447cdbf95e72948824146f48f1368fe`

Size: 7,040,530 bytes. Dimensions: 1280 × 900. Silent, looping GIF.

The numeric replay guarantee applies to the original retained calculation in its
recorded environment. Rendering/export runs remain reported capture; one failed
rendering attempt was retained before the corrected output succeeded. Three
nonblocking warnings concerned supporting datasets without dedicated contracts.
Claim review was performed by the agent, not a human reviewer.

The change is proposed through a pull request for manual review and merge.
