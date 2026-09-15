# Roadmap

This is the maintained delivery plan. GitHub issues track implementation and
acceptance; this file explains the order and repository boundaries. The
[concept](docs/concept.md) is background, and the [core contract](docs/core-contract.md)
describes what works today. No dates are promised before the work is scoped.

## Product goal

A new user can set up xyle-trace, complete a real analytical task in their own
project, select a final result, and understand exactly where its inputs came
from and how it was calculated. A short narrated walkthrough demonstrates that
journey using a separate public-transport example.

## Already available

The toolkit has a SQLite store, CLI and typed service, eight MCP tools, five
skills, source snapshots, record-level evidence/assumption backing, contracts,
correction/impact queries, retained execution/replay for compatible Python/JSON
programs, JSON export, and a basic offline HTML inspector. Repository privacy
hooks and CI are in place. These are the foundations for the next milestones.

The current inspector is a filterable node list with JSON detail. The connected
result-first visualization and curated report below are planned, not shipped.
Live agent adherence to the skills also remains unverified.

## 1. Onboarding and public beta

[Onboarding and public beta](https://github.com/xyle-labs/xyle-trace/milestone/1)

| Task | Acceptance | Issue |
| --- | --- | --- |
| Reliable first run | An installed package scaffolds discovery settings and produces one inspectable generic result; host setup needs no manual configuration repair. | [#3](https://github.com/xyle-labs/xyle-trace/issues/3) |
| Publication readiness | Dependency updates reviewed, effective GitHub safeguards checked, release artifacts inspected, and the documented installation route verified. | [#4](https://github.com/xyle-labs/xyle-trace/issues/4) |

The first-run discovery gap is concrete: `new` currently does not write
`.lineage/config.json`. Fix it and test setup from an installed distribution.
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

## 3. Separate ÖV example and video

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

Start with onboarding and the reusable explanation contract. Build the graph
and report on that contract. The external repository can be set up independently;
its results exercise the generic features and feed concrete gaps back as issues.
Record the final video after the demonstrated workflow and explorer work end to end.

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
