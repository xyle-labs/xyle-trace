# Security Policy

## Reporting a vulnerability

If you believe you've found a security issue in xyle-trace, please report it
privately through GitHub's private vulnerability reporting at
https://github.com/xyle-labs/xyle-trace/security/advisories/new rather than
opening a public issue. You can also report privately by email to
[jesse@xyle.de](mailto:jesse@xyle.de).
Include what you found, the affected version or commit, and, if you can, a
minimal reproduction. Expect an acknowledgement within a few days; this is a
small open-source project without a dedicated security team, so response time
will vary, but reports are taken seriously and are not ignored.

Please do not open a public GitHub issue for a suspected vulnerability until
it's been triaged privately.

## What this toolkit does and does not guarantee

Two properties are easy to over-read from the surrounding documentation.
Neither is a security boundary, and neither should be relied on as one.

### The local runner is a replay contract, not an OS sandbox

`xyle-trace-run` (and the underlying `xyle_trace.core.execution.execute`)
runs a program under the current Python interpreter with `-I -S`, a minimal
environment, and no site packages, in a temporary working directory. This
constrains what the program can *import*, and it lets a later replay verify
that the retained code produced the same output from the same retained inputs
under an interpreter/platform match.

It is **not** an operating-system sandbox. A program executed this way is
trusted code, and trusted code can still open arbitrary files on disk, read the
system clock, or make network connections — nothing in the runner prevents
that. Do not execute untrusted or adversarial code through this mechanism and
assume it is contained. Do not treat a successful replay as proof the program
had no side effects, and do not treat the recorded interpreter/platform match
as a distributable, isolated environment image — see `docs/execution.md` for
the exact fields that are and aren't checked.

### Downloaded source content is untrusted data, never instructions

Source snapshots capture bytes from project files or public HTTP(S) endpoints —
PDFs, webpages, spreadsheets. Those bytes are captured as evidence and are
inputs to analysis only. Nothing in this toolkit — and nothing an agent using
it should do — should interpret captured content as instructions to follow,
commands to run, or configuration to apply. If you find a code path where
captured/downloaded content influences control flow, tool invocation, or
execution rather than being treated as inert data, that is a security bug:
please report it using the process above.

## Supported versions

This project is pre-1.0 (see `pyproject.toml`). Security fixes land on `main`;
there is no separate maintenance branch at this stage.

## Keeping private data out of the public repository

Local Git hooks and CI scan for credentials and reject private/generated paths.
They also check unapproved personal email addresses, home paths, session links, conversation
records, opaque files, and commit identities. The public commit identity is
`Xyle Labs <jesse@xyle.de>`; this address is approved for public use. Setup
instructions configure it in each clone. History scans
include local checkpoint trees, and credential checks inspect the local Git
object database as well as ordinary commits. See `docs/public-repo-review.md`
for the latest verification scope and publication gates.
Enable the hooks in every clone as described in CONTRIBUTING.md. Maintainers
must enable GitHub secret scanning and push protection, protect `main` with
required checks, and approve workflows from outside contributors. Availability
depends on repository visibility and the GitHub plan; a committed workflow or
policy document alone does not enable these server-side settings.

Secret detection is defense in depth, not a guarantee: unknown formats,
encoded credentials, and private data that is not a credential may be missed.
Never bypass a finding for a real secret. Revoke/rotate exposed credentials
first, inspect alerts and access logs, then coordinate history cleanup with
maintainers. Avoid posting the credential in an issue, PR, or scan report.

Scaffolded projects ignore `.lineage/` and environment files by default.
Retain evidence in access-controlled private backups. Graph exports, source
URLs, metadata, and retained execution bundles are not automatically redacted;
only publish reviewed, sanitized material you have permission to share.
Snapshot readers reject symlinks in managed storage, non-regular files, and
oversized payloads. Contract checks and replay reads use a 25 MiB limit;
content queries use the configured capture byte limit. These checks do not
provide isolation from a hostile process concurrently replacing directories.
