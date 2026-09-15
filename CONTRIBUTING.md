# Contributing

Thanks for looking at xyle-trace. This document covers how to verify a change,
the constraints your change must respect, and what CI checks before it merges.

## Set up local secret protection

Install Python 3.11+ and Gitleaks 8.30.1 or newer (`brew install gitleaks` on
macOS; other platforms: https://github.com/gitleaks/gitleaks/releases), then:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev,mcp]"
sh scripts/setup_hooks.sh
```

Do this for every clone. Git does not install repository hooks automatically.
The setup script also sets this clone's publication identity to
`Xyle Labs <no-reply@xyle.de>`. This exact email is approved for commits.
`jesse@xyle.de` remains the public contact for package metadata and project
communication, and remains approved in existing and future commits. Other personal
author/committer identities are rejected; the exact GitHub automation identities
in `scripts/check_repo.py` are allowed. GitHub account
profiles and PR activity are separate from Git metadata and may identify users.

GitHub can generate test-merge commits when a PR is opened, using account
settings rather than this clone's Git identity. Before opening a PR or using
web-based commits, verify that the account-generated author identity is
`Xyle Labs <no-reply@xyle.de>` or the explicitly approved
`Jesse <no-reply@xyle.de>`, with the email verified and primary on GitHub.
Adding an address to the account alone does not establish that identity.
Local hooks do not control those commits. Until
the account is configured, track changes as issues and reviewed local branches;
do not weaken the history guard to admit an unapproved identity.

The pre-commit hook checks the actual index and scans staged content with Gitleaks;
the pre-push hook scans all reachable history, including secrets deleted by a
later commit. Both refuse to proceed if the scanner is missing or fails.
The commit-message hook checks for unapproved personal email addresses, home-directory
paths, conversation records, session links, and Claude co-author trailers.
Use project identity and reserved example addresses in published material.
Do not bypass these checks or add broad allowlists. Gitleaks inline suppression
comments are disabled; encoded secrets are also scanned (up to three layers).
Hooks find Gitleaks on PATH or in this clone's ignored `.venv/bin` directory.

Never commit credentials, private keys, cloud profiles, `.env` files, database
files, `.lineage/`, or internal agent records. `.env.example` may contain only
obvious placeholders. Source data, metadata, URLs with tokens, and exported
graphs can also contain secrets: review them before staging. Ignore rules are
convenience filters; `git add -f` can bypass them, which is why hooks and CI
also enforce a private-path policy.

Agent state directories, chat exports, JSONL, logs, database files, archives,
office documents, and PDFs are blocked. The public tree is UTF-8 text only,
with a 1 MiB per-file review limit; symlinks and submodules are rejected.
The maintained plugin manifests, skills, and `.claude/settings.json` are public
source, not session storage. The history check reads every unique file version
across all locally available refs and checks commit and annotated-tag metadata.
It refuses shallow clones. It cannot inspect remote refs that were not fetched.

Before pushing, also run:

```bash
python scripts/check_repo.py --history
gitleaks git --redact --no-banner --config .gitleaks.toml --log-opts=--all \
  --ignore-gitleaks-allow --max-decode-depth=3 --max-archive-depth=3
```

GitHub runs full-history secret checks, private-path and attribution checks,
and a dependency vulnerability audit on pull requests and pushes. Weekly scans
catch newly disclosed dependency vulnerabilities. Actions use immutable commit
pins and read-only tokens. Keep required checks enabled on `main`; changes to
these safeguards need maintainer review. CI runs after an upload, so it cannot
prevent the initial disclosure on a public branch; GitHub push protection and
local hooks are the preventive layers. No scanner guarantees detection of every
secret. If a real credential leaks, revoke or rotate it immediately and use the
private reporting process in SECURITY.md; removing the file is insufficient.

## Verification

Before opening a pull request, run all three of these from the repository root
and confirm they pass:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests scripts
PYTHONPATH=src .venv/bin/python -m xyle_trace.cli.main --help
```

CI (`.github/workflows/ci.yml`) runs the same checks on Python 3.11, 3.12, and
3.13 with the `dev` and `mcp` extras installed, plus a separate `core-only` job
that installs just `dev` and confirms the base package works with the `mcp`
extra absent — that is a supported configuration, not a degraded one, so it is
tested rather than assumed.

## `projects/` and `misc/` are never committed

`projects/` and `misc/` are listed in `.gitignore` and must stay that way. They
hold third-party source bytes (PDFs, spreadsheets pulled from real sources) and,
for a consuming project, a real lineage database — an unversioned, single local
copy of captured evidence with no repository of its own. Do not `git add -f`
anything under either directory, and do not paste their contents into commits,
issues, or PRs. If your change needs a fixture, add it under `examples/` or
`tests/` as project-owned, disposable material instead.

## The binding architectural constraint

**The core must serve any data-science project.** Nothing spreadsheet-shaped,
transport-shaped, or shaped like any other specific domain belongs in
`src/xyle_trace/core/`. This repository is meant to go public and stay useful
to projects that have nothing to do with transport analysis or any other single
domain; domain concepts belong in a consuming project that installs this
toolkit as an ordinary dependency. Consuming projects live outside this
repository.

An earlier design that added a `Parameter` node type to the core was rejected
for exactly this reason: a "model input" is not a new kind of thing the graph
needs to know about, it is an ordinary `Dataset` whose rows resolve through the
existing `EvidenceLink` and `Decision` nodes. If your change adds a concept to
the core that only makes sense for one kind of project, it is very likely wrong
by construction, however useful it looks for the problem in front of you.

## The node types and MCP tools are closed sets

The **twelve node types** (`project`, `source`, `source_snapshot`, `dataset`,
`transformation`, `run`, `artifact`, `indicator`, `claim`, `evidence_link`,
`agent_run`, `decision`) and the **eight MCP tools** (`lineage.register`,
`lineage.snapshot`, `lineage.record_run`, `lineage.link`, `lineage.decide`,
`lineage.query`, `lineage.validate`, `lineage.export`) are closed sets. This is
the constraint contributors are most likely to breach in good faith, because
the natural instinct when a use case doesn't fit is to add a thirteenth node
type or a ninth tool that fits it exactly.

Don't. The value of this toolkit is that every consuming project — including
ones nobody involved in this repository has met — gets the same small,
predictable vocabulary. A node type or tool that exists only because one
project needed it stops being predictable for everyone else, and the graph
stops being something an agent can reason about generically. New capability is
added as a **mode on an existing tool or node type** instead: a new `kind` value
on `Decision`, a new request field on `lineage.query`, a new export `format`.
This is also, deliberately, enforcement — if the toolkit genuinely cannot
express something a consuming project needs, that is real information, and the
fix belongs in a design discussion (or a `finding` issue — see the issue
template), not a quiet new node type slipped in with an unrelated PR.

If you believe you've found something the current vocabulary genuinely cannot
express, open an issue with the `finding` template first, before writing code.

## Enforcement lives in contracts, not graph structure

Provenance requirements (e.g. "every record in this dataset must be
`evidence_backed` or `assumption_backed` before the runs that consume it may
execute") are expressed as configuration in a project's `contracts.yaml`, not as
new structure in the graph or the core. If you're tempted to hardcode a rule
about what a project must provide, check whether it belongs in the contracts
schema instead.

## TDD expectations

This project is built test-first. For any bug fix or feature:

1. Write a failing test that demonstrates the bug or the missing behavior.
2. Confirm it fails for the reason you expect (not for an unrelated reason).
3. Write the minimal implementation that makes it pass.
4. Run the full verification suite above before opening the PR.

PRs that add implementation code without a test that would have failed before
it are unlikely to be merged as-is — not because process is sacred, but because
the deterministic-core guarantee this project makes to consumers only holds if
its behavior is pinned down by tests, not by reading the source.

## Design principles worth internalizing

From the README, the ones with teeth for day-to-day contributions:

- **Deterministic core, AI-assisted interpretation.** Hashes, identifiers,
  edges, validation, and traversal are deterministic code, not model output. A
  model may suggest a classification or a link, but it must be marked
  provisional and never presented as observed provenance.
- **Never fabricate completeness.** Historical work often lacks immutable
  sources and documented parameters. Represent the unknown as unknown; do not
  add logic that infers or back-fills missing provenance to make a dataset look
  more complete than it is.
- **Downloaded content is untrusted data, not instructions.** Anything captured
  from a source (PDF, webpage, spreadsheet) is an input to analysis. Code that
  processes captured content must never treat its contents as directives.
- **Local-first.** The toolkit must be useful with no hosted service; metadata
  stays portable, inspectable, and exportable without a network dependency.

## Questions

If something in this document is unclear, or you're unsure whether a change you
want to make fits within these constraints, open an issue before writing code —
it's much cheaper to have that conversation early.
