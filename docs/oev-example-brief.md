# Separate ÖV last-mile example

Status: planned external project. This file is its scope and acceptance brief;
the analysis is not implemented in this repository.

## Research question

**Wo ist der ÖV zu weit entfernt, sodass Scooter für die letzte Meile sinnvoll
sein können?**

Identify places with poor access to usable public transport and examine whether
a scooter connection could improve that access. Distance alone cannot establish
that scooters are useful: service times/frequency, accessible routes, transfer
time, and the chosen suitability assumptions affect the conclusion.

## Repository boundary

Create a separate repository dedicated to this scenario; `oev-last-mile` is a
proposed name, not an existing repository link. It installs a released or pinned
version of xyle-trace and uses its public CLI, Python service, and/or MCP tools.
It does not copy or fork the toolkit internals.

The separate repository owns the geography, data acquisition, geospatial
libraries, calculations, API/map/report, methodology, and video assets. This
repository owns generic onboarding, provenance queries, visualization/report
features, and synthetic tests. Track the repository setup here until the
external repository exists; then move its analytical tasks there.

## Scope to decide in that repository

- One bounded study area, time period, and target travel direction.
- Public and reusable source datasets: candidate inputs include transit stops
  and service schedules, a routable walking/cycling network, and aggregated
  origins or population. Record provider, license, retrieval date, and version.
- A definition of usable transit service and a configurable walking-access
  threshold. Record why each assumption was chosen; no threshold is fixed here.
- A baseline walking-to-transit route and a comparable scooter-to-transit route,
  with explicit speeds, waiting/transfer/access times, and suitability constraints.
- A clearly scoped output: candidate areas plus an interpretable accessibility
  measure. Do not present suitability assumptions as observed demand or proven
  commercial viability. Use aggregate locations, not personal movement traces.

## Demonstration chain

Source versions → normalized stops/network/origins → baseline accessibility →
scooter-access comparison → candidate areas and KPI → map/report or API result.

A reader selects a published result or recorded API response field and follows
its calculation, exact input records, source snapshots, and assumptions through
xyle-trace's generic explorer. Change a threshold or correct one source value;
show which results require review and how a newly recorded run updates them.

Every material input and transformation must be recorded honestly. Geospatial
work may run in an external environment and be captured as a reported run.
The current retained runner supports standard-library Python with JSON inputs
and outputs; demonstrate verified replay only for a compatible calculation.
A reproducible external environment and a trace-verified replay are distinct
claims. If the workflow needs additional execution support, file a concrete
xyle-trace finding rather than silently labeling a reported run verified.

## Acceptance

- A new user can follow the setup in an empty environment using the declared
  xyle-trace version and the example's own instructions.
- At least one real result links to its sources, calculation, parameters,
  assumptions, and recorded run; incomplete backing is visibly incomplete.
- The published example includes the baseline, comparison, limitations, and
  a demonstrated correction or sensitivity change. It does not invent results.
- Analysis code and data remain outside xyle-trace. Shared evidence and media
  are reviewed for licensing and private information before publication.
- A live agent continuation exercise and a short narrated video use this same
  actual workflow. The video is linked from toolkit onboarding after publication.

## Video outline

Target roughly 4–6 minutes, with readable captions and commands linked to the
example's versioned instructions:

1. State the question and open the separate example project.
2. Install xyle-trace, initialize the project, and confirm host discovery.
3. Register a source version, inspect one input, and explain an assumption.
4. Run the actual analysis and open its map/KPI or recorded API result.
5. Click the result and follow calculation → input → exact source in the explorer.
6. Change one assumption or input and show downstream impact and updated output.

Record a clean workspace with reviewed data. Keep raw agent chat/session logs,
local credentials, and unrelated personal material out of published media.
The narration and final recording belong to the external example repository
or its chosen publication location, not to the toolkit's package.
