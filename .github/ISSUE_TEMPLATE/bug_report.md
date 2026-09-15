---
name: Bug report
about: Something in xyle-trace doesn't work the way it's documented to
title: ""
labels: bug
assignees: ""
---

## Describe the bug

A clear, concise description of what's wrong.

## Steps to reproduce

1.
2.
3.

Please include the exact command(s) run, and, if possible, a minimal
`contracts.yaml` / call sequence that reproduces the problem without depending
on any private data.

## Expected behavior

What you expected to happen instead.

## Actual behavior

What actually happened. Include the full error output/traceback if there is
one.

## Environment

- xyle-trace version (`pip show xyle-trace` or commit SHA):
- Python version:
- Installed extras (`dev`, `mcp`, both, neither):
- OS:
- Interface used (CLI, MCP tool, `Service` directly, local runner):

## Additional context

Anything else relevant: contracts configuration, whether this happens with a
fresh `.lineage/` store or only an existing one, whether `xyle-trace validate`
passes, etc.
