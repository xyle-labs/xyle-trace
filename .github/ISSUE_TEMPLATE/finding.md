---
name: Finding — the toolkit couldn't express something my project needed
about: The public surface (twelve node types, eight MCP tools, contracts) fell short of what your project needed to record
title: "Finding: "
labels: finding
assignees: ""
---

<!--
This template exists because that's exactly how most of this project's own
design decisions have been made: a real consuming project ran into a shape it
couldn't express through the public surface, and that gap became a finding
that fed back into the toolkit's direction. Yours can do the same.

Read CONTRIBUTING.md's "node types and MCP tools are closed sets" section
before filing — it explains why the fix is very unlikely to be a thirteenth
node type or a ninth tool, and much more likely to be a new mode on an
existing one (a new `kind` on `Decision`, a new field on a request, a new
`format` on export). That's useful context for describing the gap, not a
reason not to file it.
-->

## Your project's shape

What kind of project is this, and what does it produce? (e.g. "a Jupyter-based
analysis pipeline producing quarterly forecasts", "a batch ETL job feeding a
BI dashboard", "an agent that drafts report claims from spreadsheet inputs.")
Rough size/complexity is useful too — one dataset and one transformation, or a
graph with dozens of sources and derived artifacts.

## What you were trying to record or query

Describe the concrete thing you were trying to represent in the lineage graph
or ask of it — not the workaround, the actual intent. E.g. "I needed to record
that this dataset's records came from re-running the same source snapshot
under two different unit conversions" or "I needed to ask which claims
ultimately depend on any *unresolved* correction, not just any correction."

## What you attempted through the public surface

Which node type(s), MCP tool(s) (`lineage.register`, `.snapshot`,
`.record_run`, `.link`, `.decide`, `.query`, `.validate`, `.export`), CLI
command, or contract you tried to use, and specifically where it fell short —
a missing field, a request shape that couldn't represent the relationship, a
validation rule that didn't fit, a query that had no way to ask what you
needed.

```
# a minimal request/response, CLI invocation, or contracts.yaml snippet
# that shows the gap, if you can produce one without private data
```

## What you had to work around it with

What did you actually do instead? (e.g. stuffed extra structure into a
`Dataset`'s free-form fields, tracked the relationship outside the graph
entirely, gave up on expressing it and accepted an incomplete lineage record.)
Be explicit about the workaround's cost — what it makes harder to query, what
it makes invisible, what a future contributor to your project would need to
know that the graph no longer tells them.

## What you think would close the gap

Optional, but useful if you have a view: a new mode on an existing tool, a new
field on an existing request/node, a new contract capability, or something
else. It's fine to just describe the gap and leave the design to the
maintainers.
