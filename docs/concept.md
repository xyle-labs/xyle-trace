# Concept: the full reasoning behind xyle-trace

> This document provides background on the product and its domain model.
> Implementation status is documented in [core-contract.md](core-contract.md).
> The maintained delivery plan and issue list are in [ROADMAP.md](../ROADMAP.md).
> Historical proposals below are context, not an active task backlog.

## The problem

AI development tools make it cheap to collect data, write transformations, produce charts, and draft reports. They do not automatically preserve a trustworthy account of how a result was produced.

A real research or data-science project typically mixes:

- websites, APIs, PDFs, spreadsheets, emails, and manually supplied files;
- Python, SQL, notebooks, shell commands, GIS tools, and manual corrections;
- raw, cleaned, canonical, joined, and derived datasets;
- assumptions, parameters, classifications, and judgment calls;
- tables, maps, charts, narrative claims, and final recommendations;
- several agents and humans working across many sessions.

After a few weeks, apparently simple questions become expensive:

- Which exact source supports this number?
- Which version of the source was used?
- How was the number calculated?
- Which code, parameters, and input datasets produced this table?
- Which report claims depend on a dataset that has changed?
- Was this value measured, inferred, estimated, or manually entered?
- Can another person or agent reproduce the result?
- Where do two sources contradict one another?
- Which results are no longer trustworthy after correcting an upstream input?

Git helps with the history of files and code. It does not, by itself, capture the semantic relationship between a source table, an extracted value, a transformation, a chart, and a sentence in a report.

Enterprise lineage and governance platforms solve parts of this problem, especially inside managed databases and production pipelines. They are often too heavy, closed, infrastructure-centered, or detached from the actual AI-assisted research workflow. The target here is the missing layer between an AI coding agent and the heterogeneous evidence inside a working project.

## Core hypothesis

If provenance tools are available through MCP and their use is encoded in a project skill, an AI agent can maintain useful lineage continuously with little additional effort from the user.

The toolkit should feel like part of the development environment, not like a separate governance application that somebody must update later.

```text
Source evidence
    -> immutable snapshot
    -> extracted or imported data
    -> transformation run
    -> derived dataset or analysis
    -> table, chart, map, or model output
    -> claim in a report
    -> publication or decision
```

The lineage graph is both human-inspectable and machine-readable. Agents can use it to answer questions, detect missing evidence, assess downstream impact, and avoid silently breaking earlier conclusions.

## Intended users

- Data scientists and analysts working with AI coding agents.
- Researchers combining public data, documents, and derived calculations.
- Small engineering teams that need provenance without deploying an enterprise data catalog.
- Newsrooms and evidence-led publications.
- Consultants producing decision-support reports from heterogeneous sources.
- Open-source projects whose users need to inspect and reproduce analytical outputs.

The first users are expected to be developers and technical researchers. A polished non-technical governance UI is not required for the MVP.

## Design principles

### Agent-native

The primary interface is MCP plus a concise skill for Codex, Claude Code, and compatible agents. A CLI and file formats remain available for humans, CI, and systems without MCP.

### Install once, then work normally

Initialization should add the local store, configuration, MCP integration, and agent instructions. Provenance capture should then happen during ordinary project work rather than through a separate documentation phase.

### Local-first and open-source

The project must be useful without a hosted service. Metadata should be portable, inspectable, versionable, and exportable. Users should not lose access to their project history when a vendor or model provider changes.

### Evidence before visualization

The graph is not the product by itself. Its value comes from reliable evidence links, reproducible transformations, impact analysis, and validation. A beautiful graph of guessed relationships is not sufficient.

### Deterministic core, AI-assisted interpretation

Hashes, identifiers, graph edges, validation, state transitions, and lineage queries should be deterministic. Models may help classify sources, map fields, extract candidate claims, or suggest links, but they must not silently invent provenance.

### Explicit uncertainty

The system must distinguish:

- measured facts;
- calculated results;
- analytical inferences;
- estimates or proxies;
- editorial interpretations;
- assumptions;
- unsupported or disputed claims.

### Incremental adoption

A project should receive value after registering one source and one derived output. It should not require complete organizational metadata, a central catalog, or migration of every existing pipeline.

### Storage and tool independence

The provenance model should not depend on one database, notebook environment, cloud provider, LLM, or orchestration framework.

## What should be captured

The graph needs to connect five related kinds of lineage.

### 1. Source provenance

Where information came from:

- source URL, organization, title, and publication date;
- access time and retrieval method;
- immutable snapshot or content hash;
- file type, license, and relevant metadata;
- precise locator such as page, table, sheet, cell range, paragraph, or API response path;
- superseded versions and known corrections.

### 2. Data lineage

How datasets changed:

- raw input and canonical dataset identities;
- schemas and semantic field mappings;
- row, table, file, or column-level relationships where available;
- joins, filters, aggregations, unit conversions, geospatial operations, and manual corrections;
- code reference, environment, parameters, and run status;
- input and output hashes.

### 3. Analytical lineage

How indicators and analytical results were constructed:

- indicator definition and formula;
- numerator, denominator, time period, geography, and unit;
- assumptions, proxies, exclusions, and missing-data treatment;
- software/model version and run configuration;
- sensitivity or scenario relationship;
- generated tables, charts, maps, and model outputs.

### 4. Claim and evidence lineage

How narrative statements relate to evidence:

- claim text or normalized proposition;
- claim type and status;
- supporting and contradicting evidence;
- exact source locators;
- derived results used as support;
- confidence and review state;
- where the claim appears in reports or publications.

A claim ledger could use states such as:

```text
supported
partially_supported
unsupported
conflicting_sources
analytical_inference
editorial_interpretation
```

Unsupported material claims should be detectable before publication.

### 5. Agent and workflow lineage

What automated process produced an artifact:

- agent or human actor;
- parent and child task IDs;
- trace ID;
- workflow, prompt, schema, and policy versions;
- model and provider identifiers where relevant;
- input references and hashes;
- validation warnings and review decisions.

The goal is not to log every token or private chain of thought. It is to retain the operational facts necessary to explain and reproduce the output.

## Proposed domain model

The smallest useful graph will likely contain the following node types.

| Node | Purpose |
| --- | --- |
| `Project` | Isolation boundary for one repository or research project. |
| `Source` | Logical external or internal origin, such as a PSA dataset or DOE report. |
| `SourceSnapshot` | Immutable version retrieved at a particular time. |
| `Dataset` | Structured data at a defined stage and schema. |
| `Transformation` | Reusable operation or code definition. |
| `Run` | Execution of a transformation with concrete inputs and parameters. |
| `Artifact` | File or result such as a table, chart, map, model, or report. |
| `Indicator` | Defined analytical measure with unit, geography, period, and formula. |
| `Claim` | Material assertion used in an output. |
| `EvidenceLink` | Qualified support or contradiction with an exact locator. |
| `AgentRun` | Agent workflow that created or modified entities. |
| `Decision` | Human approval, correction, exclusion, or methodological choice. |

Important relationships include:

```text
SNAPSHOT_OF
EXTRACTED_FROM
DERIVED_FROM
GENERATED_BY
USED_INPUT
PRODUCED
SUPPORTS
CONTRADICTS
APPEARS_IN
SUPERSEDES
REVIEWED_BY
DEPENDS_ON
```

Edges need metadata. For example, `DERIVED_FROM` may include the fields or rows used, while `SUPPORTS` should include a locator, support type, and review status.

Identifiers should remain stable when filenames move. Content hashes should identify immutable versions without replacing human-readable source identities.

## Expected user experience

### Initialize a project

```bash
xyle-trace new --project analysis --path ./analysis
```

`new` scaffolds a consuming project and does the whole setup, because that is what
you invoked it for:

1. Create the project directory and a local metadata store at `.lineage/graph.db`.
2. Write the MCP configuration (`--host claude`, `codex`, or `both`).
3. Install the five agent skills and project-level agent instructions.
4. Create storage for source snapshots and generated exports.
5. Write a starting `contracts.yaml` that reports a gap until you edit it.

It refuses a directory that is not empty. For a project that already exists,
`xyle-trace init` creates only the store and the project node — it never writes
host configuration or installs skills as a side effect of something else. See
[project agent setup](agent-setup.md).

### Work with an agent

The user continues with normal requests:

```text
Download the latest PSA population table, clean the municipality names,
join it to the boundary layer, and calculate population density.
```

The skill instructs the agent to:

1. Register the source and immutable snapshot.
2. Record the extraction/import step.
3. Register the cleaned and joined datasets.
4. Record code, parameters, inputs, outputs, hashes, and warnings.
5. Register the derived indicator and resulting artifacts.
6. Link any narrative claims to the indicator and underlying evidence.
7. Flag missing, ambiguous, conflicting, or unsupported information.

### Ask provenance questions

```text
Where does this number come from?
Show everything that depends on the 2022 household count.
Which published claims use an outdated source?
Can you reproduce Figure 4?
Which outputs contain manual corrections?
Find claims without exact evidence locators.
Compare the provenance of these two estimates.
```

## Proposed components

### Core library

A deterministic library for entities, versioning, edges, validation, hashing, traversal, and import/export.

### Local metadata store

SQLite is the implemented local store, with JSON export for review and offline HTML
for exploration. PostgreSQL or a dedicated graph database remains a future choice
only if a hosted deployment requires it.

### CLI

`xyle-trace` provides `new`, `init`, `validate`, `explain`, and JSON/HTML `export`.
`xyle-trace-run` executes and replays retained Python/JSON programs. Registration,
decisions, and the full query surface are available through Python and MCP.

### MCP server

Structured tools agents can call without shell-specific parsing or direct database access.

The implemented server exposes exactly eight tools:

```text
lineage.register
lineage.snapshot
lineage.record_run
lineage.link
lineage.decide
lineage.query
lineage.validate
lineage.export
```

Each accepts one typed `request` object. Operations use discriminators for related
actions; validation returns actionable gaps. Initialization remains an explicit
CLI step. See [project setup](agent-setup.md) for request envelopes and configuration.

### Agent skill

The skill is the behavioral layer. It tells the agent when provenance is mandatory, how to call the MCP tools, what not to infer, and how to recover when capture fails.

The skill should require provenance capture when an agent:

- downloads or receives a material source;
- extracts structured facts from a document;
- creates or changes a dataset;
- performs a material calculation;
- generates a chart, table, map, or report;
- introduces a substantive claim;
- changes a methodology or assumption;
- discovers a contradiction or correction.

It should avoid noisy records for inconsequential temporary files and ordinary code edits that have no effect on data or evidence.

### Adapters and hooks

Later adapters may capture metadata from:

- Python scripts and notebooks;
- SQL and dbt projects;
- CSV, Excel, Parquet, DuckDB, and PostgreSQL;
- geospatial files and PostGIS;
- PDFs and web snapshots;
- Git commits and CI runs;
- existing OpenLineage-compatible events.

The MVP should use a hybrid strategy: explicit agent calls plus deterministic file hashes and run wrappers. Attempting perfect automatic lineage inference across every tool would make the first version unreliable and too broad.

### Graph explorer

The first visualization can be a static HTML or JSON/graph export rather than a large web application. It should support upstream/downstream traversal, filtering by entity type, and inspection of edge metadata.

## Repository structure

```text
xyle-trace/
  README.md
  pyproject.toml
  src/xyle_trace/
    core/          hashing, ids, snapshots, revisions, execution
    models/        the twelve node types and provenance metadata
    store/         SQLite schema and store
    contracts/     policy model, loader, validator
    queries/       traversal and explanation
    exporters/     JSON and offline HTML
    cli/           xyle-trace and xyle-trace-run
    mcp/           optional stdio adapter, eight tools
  skills/          the five portable agent skills
  examples/
    minimal/       protocol replay fixture
    managed/       retained-input execution fixture
  docs/
  tests/
  projects/        consuming projects; git-ignored, not part of the package
  misc/            use-case reference artifacts; kept on disk, untracked
```

`adapters/` and a published `schemas/` directory are future work, not present
today. The implementation uses Python 3.11+ and the working package name
`xyle-trace`. The persisted model and protocol remain language-neutral.

## External example projects

Real analytical examples live in separate repositories that install xyle-trace.
Their datasets, assumptions, calculations, and publication assets belong to
those consuming projects. This repository provides reusable provenance tools
and small synthetic fixtures for testing and onboarding.

## Relationship to NanoNets Graft

Graft was an important challenge to the idea: if a repository knowledge graph already lets agents understand files and their relationships, is a separate lineage toolkit necessary?

The critical conclusion was that a generic repository or code graph comes close enough that building another one would not be a compelling MVP. The project only has distinct value if it treats analytical provenance as a first-class, enforceable contract.

The differentiation should therefore be:

- exact external source snapshots and locators;
- semantic data transformations rather than file relationships alone;
- indicators, assumptions, and methodology as explicit entities;
- claim-to-evidence lineage across documents and derived data;
- reproducibility metadata and input/output hashes;
- validation of missing or contradictory support;
- downstream impact analysis;
- agent behavior that maintains the graph while doing the work;
- portability across Codex, Claude Code, repositories, and model providers.

Graft could remain adjacent infrastructure, an integration, or even a reusable repository-graph layer. Replacing it is not itself a goal. The question for implementation is whether integration produces a simpler and more useful product than maintaining an overlapping code-knowledge graph.

## Relationship to existing lineage tools

Existing systems already cover important parts of the space:

- pipeline and job events;
- warehouse/table/column lineage;
- data catalogs and governance;
- dbt model dependencies and documentation;
- experiment and model tracking;
- reproducible workflow engines;
- research provenance standards;
- repository and code knowledge graphs.

The toolkit should interoperate where practical rather than replace all of them. Its proposed center is different:

> A lightweight, local-first provenance contract used directly by AI development agents across source documents, datasets, code, analysis artifacts, and narrative claims.

Before implementation, a fresh competitive review should test whether this combination is still underserved. If the toolkit only reproduces existing table lineage with an MCP wrapper, it should not be built.

## MVP

### MVP goal

Prove that an agent can maintain trustworthy, useful provenance for one real analytical workflow with minimal user intervention.

### In scope

- One local project.
- SQLite metadata store.
- Stable IDs and immutable source/artifact versions.
- Files and URLs as sources.
- CSV, Excel, Parquet, JSON, PDF, and Markdown metadata.
- Explicit transformations and run records.
- Input/output hashes and Git references.
- Dataset, artifact, indicator, claim, and evidence entities.
- Upstream, downstream, explanation, gap, and impact queries.
- Typed MCP server.
- Codex/Claude-compatible skill.
- CLI validation and JSON export.
- A simple graph visualization.
- One reproducible external example vertical slice.

### Not in the first MVP

- A closed multi-tenant SaaS platform.
- Enterprise access governance and organization-wide cataloging.
- Automatic column-level lineage for every database and language.
- Continuous ingestion from dozens of connectors.
- A large dashboard builder.
- Storage of every prompt, token, or internal reasoning trace.
- Blockchain or cryptographic claims beyond useful hashing and signatures.
- Fully automatic truth assessment.
- Replacement of Git, dbt, OpenLineage, DataHub, Graft, or workflow engines.
- Perfect reconstruction of undocumented historical work.

### MVP demonstration

A convincing demonstration should answer all of these from one repository:

```text
Explain where this chart came from.
Show the exact source pages and tables.
Re-run the calculation.
List every assumption and manual correction.
Find the report claims supported by this result.
Change one upstream value and show the affected outputs.
Identify claims that now require review.
```

## Delivery plan

The maintained milestones, open tasks, and example-project boundaries are in
[ROADMAP.md](../ROADMAP.md). This concept document is background, not a second backlog.

## Validation and guardrails

The implementation should enforce at least the following:

- A derived artifact cannot claim reproducibility without concrete input versions and a recorded run.
- A source URL alone is not an immutable source snapshot.
- Every material quantitative claim must have evidence or be explicitly marked unsupported/inferred.
- Source IDs and locators referenced by agents must exist.
- Input and output hashes must be calculated deterministically.
- Project isolation must bind every read and write to `project_id` plus resource ID.
- Failed or partial runs must not be presented as successful lineage.
- Corrections and superseded sources remain visible.
- AI-suggested links remain provisional until deterministic validation or review.
- Sensitive source content should not be copied into logs unnecessarily.

## Security and privacy

- Default to local metadata and optional local snapshots.
- Store credentials outside provenance records.
- Redact authorization headers and secrets.
- Permit metadata-only registration when source content cannot legally be copied.
- Make content retention configurable by source classification.
- Record hashes, sizes, types, and safe identifiers instead of full confidential content where appropriate.
- Separate access to metadata from access to protected source files.
- Treat downloaded documents and webpages as untrusted data, not agent instructions.

## Open-source and commercial strategy

The toolkit should be genuinely useful as an open-source project and easy for organizations to inspect, modify, and operate with their own AI development tools.

The commercial opportunity is not a conventional locked-down SaaS product. Xyle sells on three layers that run in parallel. None supersedes another, and a user may sit on any one of them indefinitely.

### 1. The open-source core

The toolkit is free, installable, and genuinely useful on its own. A data scientist who never pays anything should still work substantially better with it than without it. Nothing is withheld or degraded to create pressure to upgrade, and no capability is reserved for paying users that the open core would naturally have.

This is a constraint on the product, not a marketing position. If the open core stops being independently valuable, the model has failed.

### 2. Xyle membership

Membership is not a better version of the tool. It supplies the two things a local tool cannot provide for itself, and that a real data-science project always needs.

**Pre-computed indicators.** Finding, retrieving, reconciling, and normalizing source data is routinely the largest share of the work in a data-science project — often larger than the analysis it exists to support. Xyle maintains indicators that are already sourced, snapshotted, normalized, and computed, each carrying the same provenance the toolkit would have recorded had the member collected it themselves. A project starts from evidence rather than from collection.

**Persistent storage, memory, and project infrastructure.** A serious project outlives a session, a machine, and often a person. Membership provides hosted durable state, shared project memory, and the operational infrastructure that keeps a long-running analysis continuable across people and agents.

### 3. Professional and enterprise services

Individual engagements, where Xyle may apply its own tooling and membership services or build something custom for the client:

- methodology and architecture consulting;
- implementation in existing repositories and data platforms;
- custom adapters and integrations;
- provenance-policy design;
- team training;
- migration or reconstruction of existing analytical projects;
- support and maintenance;
- optional managed hosting or operation.

### How the layers reinforce each other

The three are not merely coexisting price points. A consulting engagement is where it becomes clear which indicators are worth maintaining centrally, and those indicators become membership assets. Membership data makes the next engagement cheaper to deliver. Both feed requirements back into the open core.


### The same pattern applies beyond this repository

Xyle's other products follow the same shape. The core AI service is open source; the membership alternative is not having to host, configure, and extend it yourself.

Across all three layers what is sold is convenience, data, operational confidence, and expertise — never artificial lock-in.

The toolkit uses Apache-2.0; see [LICENSE](../LICENSE).

## Measures of success

Useful metrics for the first test include:

- percentage of material claims with valid evidence;
- percentage of artifacts with complete upstream lineage;
- percentage of selected results reproducible from recorded information;
- time required to explain an unfamiliar result;
- time required to assess an upstream correction;
- manual provenance actions required per analytical task;
- invalid or hallucinated links created by agents;
- graph noise versus actionable relationships;
- ability of a new agent session to continue work correctly.

The most important qualitative test is simple: does the toolkit make the project easier and safer to continue, or does it merely produce more metadata?

## Major risks

### No unique value

Existing tools or Graft may already provide most of the useful functionality. The MVP must validate claim-level and cross-artifact provenance rather than assuming it is distinctive.

### Capture friction

If agents must make too many lineage calls, work becomes slower and the graph becomes noisy. The skill and API must identify only material events.

### False confidence

A recorded edge may look authoritative even when an agent inferred it incorrectly. Provenance quality and review status must be explicit.

### Retrofitting difficulty

Historical projects often lack immutable sources, precise parameters, or documented manual changes. The system must represent unknowns instead of fabricating completeness.

### Overbuilding the graph

A graph database, complex ontology, or polished explorer can consume the project before the core workflow is proven. SQLite and focused queries are enough to begin.

### Agent dependence

The project must remain usable from CLI and open formats. Its data cannot become dependent on one model provider or one proprietary agent interface.

## Future design considerations

- Evolution of the implemented SQLite v1 schema and JSON export format.
- Whether snapshots are stored by the toolkit or referenced externally.
- Required granularity: file, table, column, row, cell, or claim.
- How source locators are normalized across PDFs, spreadsheets, webpages, and APIs.
- Which agent actions require mandatory capture.
- How provisional AI-inferred relationships become trusted.
- Whether Git commits are required or optional references.
- How much automatic instrumentation belongs in the core.
- Whether to integrate Graft, reuse its graph, or remain independent.
- Which established standards should be imported or emitted.
- Whether Graphiti adds useful temporal knowledge behavior or unnecessary complexity.
- The minimum graph explorer needed for the first public demonstration.
- How to synchronize metadata safely across a team without turning the project into SaaS.
