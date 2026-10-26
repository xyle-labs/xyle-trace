# Roadmap

This is the maintained delivery plan. GitHub issues track implementation and
acceptance; this file explains the order and repository boundaries. The
[concept](docs/concept.md) is background, and the [core contract](docs/core-contract.md)
describes what works today. No dates are promised before the work is scoped.

## Product goal

A new user can set up xyle-trace, complete a real analytical task in their own
project, select a final result, understand its exact inputs and calculation,
review changes, and share selected findings. The agreed scope is reliable
capture → result explanation → review/change impact → sharing. Portable projects
support that workflow; verified replay retains its deliberately narrow scope.

## Already available

The toolkit has a SQLite store, CLI and typed service, eight MCP tools, five
skills, source snapshots, record-level evidence/assumption backing, contracts,
correction/impact queries, retained execution/replay for compatible Python/JSON
programs, JSON export, and a basic offline HTML inspector. Repository privacy
hooks and CI are in place. These are the foundations for the next milestones.

The current implementation adds an installed synthetic demo and CLI capture,
project health inspection, retained result explanations, correction receipts,
a connected offline explorer, selected reports with preview, and verified
project handoffs. The interfaces and limitations are in [the workflow](docs/workflow.md).
Interactive browser acceptance and the cross-host comparison remain outstanding.
[Bounded host findings](docs/agent-acceptance.md) record synthetic Codex CLI capture
and recovery; Claude, live MCP onboarding, and exact preflight ordering remain
unverified. Implemented capabilities do not close those acceptance gates by themselves.

## Capability boundaries

| Include | Boundary |
| --- | --- |
| Reliable capture | Existing typed mutations through MCP, Python, and CLI; external computation stays reported. |
| Exact explanations | Retained managed input versions, explicit gaps, JSON field selectors, and version pins. |
| Versions and review | Immutable new correction receipts, current change impact, health checks, explicit claim review. |
| Explorer and sharing | Offline result navigation and selected printable reports; field-based privacy projection and author review. |
| Portable projects | Verified graph/policy and opted-in retained blobs; new-directory restore without executing content. |
| Scoped verified replay | Existing standard-library Python/JSON retained execution contract. |

Hosted accounts/collaboration, automatic chat collection, domain-specific
analysis, arbitrary-environment replay, and automatic evidence-support judgments
are outside the current scope. Individual source adapters need demonstrated use.

## 1. Onboarding and public beta

[Onboarding and public beta](https://github.com/xyle-labs/xyle-trace/milestone/1)

| Task | Acceptance | Issue |
| --- | --- | --- |
| Reliable first run | An installed package scaffolds discovery settings and produces one inspectable generic result; host setup needs no manual configuration repair. | [#3](https://github.com/xyle-labs/xyle-trace/issues/3) |
| Publication readiness | Dependency updates reviewed, effective GitHub safeguards checked, release artifacts inspected, and the documented installation route verified. | [#4](https://github.com/xyle-labs/xyle-trace/issues/4) |

`new` now writes `.lineage/config.json`, validates discovery settings, and
handles Unicode host configuration. The installed `demo` and built-wheel CI
smoke exercise the first result without a source checkout. Live host setup
acceptance and public package release remain separate.
A generic small example remains in the toolkit; real domain analysis stays in
its own repository.

## 2. Result explorer and reports

[Result explorer and reports](https://github.com/xyle-labs/xyle-trace/milestone/2)

| Task | Acceptance | Issue |
| --- | --- | --- |
| Explain a selected result | Resolve a KPI/artifact/API response field to a specific calculation, input versions, backing records, sources, and assumptions. | [#5](https://github.com/xyle-labs/xyle-trace/issues/5) |
| Connected visual exploration | Select a result and click through an expandable graph with readable calculation/evidence panels, deep links, and keyboard access. | [#6](https://github.com/xyle-labs/xyle-trace/issues/6) |
| Shareable report | Export reviewed results and provenance in self-contained HTML with a useful print view and no automatic disclosure of source bytes. | [#7](https://github.com/xyle-labs/xyle-trace/issues/7) |

The interaction and data requirements are in [the explorer specification](docs/result-explorer.md).
Reuse the existing graph vocabulary and queries. Missing evidence, historical
versions, reported execution, and verified replay must remain distinguishable.
The report cannot imply a source supports a value merely because a path exists.

## 3. Live validation and optional example/video

[ÖV example and walkthrough](https://github.com/xyle-labs/xyle-trace/milestone/3)

**Fragestellung: Wo ist der ÖV zu weit entfernt, sodass Scooter für die letzte
Meile sinnvoll sein können?**

| Task | Acceptance | Issue |
| --- | --- | --- |
| Separate example repository | A scoped transit-access comparison installs xyle-trace as a dependency and traces one actual result. | [#8](https://github.com/xyle-labs/xyle-trace/issues/8) |
| Live agent validation | Observe capture and continuation in Claude Code and Codex; measure missing records, incorrect links, and manual intervention. | [#9](https://github.com/xyle-labs/xyle-trace/issues/9) |
| Narrated example video | Show setup, a real result, source/calculation drill-down, and the effect of an input or assumption change. | [#10](https://github.com/xyle-labs/xyle-trace/issues/10) |

See [the external-project brief and video outline](docs/oev-example-brief.md).
The example repository is planned; no analytical implementation or data is being
created here. Its location, study area, sources, and method will be chosen there.
The setup issue is the handoff point; move analytical tasks to the new repository
once it exists and add its link here.

A long distance to transit identifies a possible access problem. Scooter
suitability needs an explicit baseline, usable service, travel-time comparison,
and recorded assumptions. No numerical thresholds or research conclusions have
been selected in this toolkit roadmap.

## Order and ownership

Validate the implemented capture, explanation, health, report, and handoff flow
from an installed package and through real agent sessions. Complete interactive
explorer/report acceptance before treating the UI milestones as finished.
The separate analytical repository and video are optional later work; no
geography or domain conclusions are needed to deliver the toolkit capabilities.

| xyle-trace | Separate example repository |
| --- | --- |
| Installation, host setup, generic fixtures, provenance queries, visualization/report features, privacy safeguards | Geography, source acquisition, geospatial dependencies, domain calculations, maps/API, research conclusions, and video assets |

The toolkit stays domain-neutral. Any geospatial execution outside the current
retained runner must be labeled honestly; source registration or reported run
capture alone does not establish verified replay.

## Later, driven by demonstrated needs

Adapters, published schemas, broader execution environments, snapshot cleanup,
and team synchronization remain candidates. Promote them into scoped GitHub
issues when the example or another consuming project demonstrates a concrete
need. The historical concept document does not commit the project to all of them.
