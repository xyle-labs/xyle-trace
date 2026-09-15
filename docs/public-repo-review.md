# Public repository privacy review

Reviewed 2026-09-15. The repository is now public. This review covers
`xyle-labs/xyle-trace` and its publication safeguards. It does not cover unrelated
projects or copies held outside this repository.

The owner has explicitly approved `jesse@xyle.de` for all public project use
and `no-reply@xyle.de` for commits. New local Git authors and committers use
`Xyle Labs <no-reply@xyle.de>`; the earlier approved identity remains allowed.
Package metadata, plugin metadata, and documentation retain `jesse@xyle.de`
as the public contact. The exact identity `Jesse <no-reply@xyle.de>` is also
approved for GitHub-generated commits. The address is added to the account and
must not become its primary email. Repository-local Git configuration controls
local commits. The owner also approves `Jesse <jesse@j-apps.com>`, the exact
test-merge author observed for the GitHub account `Jesse-jApps` in
[PR #13](https://github.com/xyle-labs/xyle-trace/pull/13). The history guard allows
that identity and address explicitly. Local commits continue to use the project
identity, and squash merges explicitly select `no-reply@xyle.de`; no account
primary-email change is required.

## Clean publication history

Publication starts from a fresh root commit containing the reviewed source.
Earlier development commits, pull-request refs, remote-tracking refs, and tool
checkpoint refs are removed locally, with obsolete reflogs and objects purged.
Dependency updates must branch from this clean root; never import an old branch
or merge earlier development history back in.

The original GitHub repository was renamed to a private archive, and a fresh
private repository was created at `xyle-labs/xyle-trace` with a different
repository identity. Private archives retain earlier hosted history and must stay private. Roadmap
tasks are migrated separately from Git. Dependency updates are tracked as
issues with clean branches: opening PRs during migration caused GitHub to
generate test-merge commits with an unapproved account email, which the history
guard rejected. That intermediate repository was also archived privately.
GitHub-generated commits must use an approved identity, including the
owner-approved `Jesse-jApps` identity above. The pre-publication verification recorded
in [#4](https://github.com/xyle-labs/xyle-trace/issues/4) found all 15 checked old
or rejected commit IDs unavailable and the removed-example URL returning 404.
A new repository does not erase internal backups or copies held elsewhere.

## Safeguards

- `.gitignore` excludes credentials, local agent state, chats, transcripts,
  databases, logs, evidence stores, backups, archives, and source documents.
- `scripts/check_repo.py` checks working files, actual staged bytes, historical
  file versions, commit identities/messages, annotated tags, and direct tree
  or blob refs. It rejects private paths, unapproved email addresses, personal
  home paths, session links and conversation records, binary data, symlinks,
  submodules, and files over 1 MiB.
- Enabled commit and push hooks run the privacy policy and Gitleaks. Missing
  scanners fail closed. Inline Gitleaks suppression is disabled; encoded and
  archived content is inspected. Push scans include the entire object database.
- CI repeats history, metadata, and object scans and audits dependencies.
  Actions are SHA-pinned, use read-only tokens, and do not persist checkout
  credentials. Repository settings must enforce the corresponding restrictions.
- Source distributions use an explicit inclusion list; wheel contents are
  limited to the package and shared skills. Review archives before uploading.

Diagnostics report rule names and object IDs rather than matching content.
Hooks must be installed in each clone with `scripts/setup_hooks.sh`. Local hooks
can be bypassed, CI runs after upload, and pattern matching cannot detect every
private fact or credential. Review new data manually.

## Project review and validation

The project is a local Python provenance toolkit with a SQLite store, typed
service layer, CLI, MCP server, retained source capture, and graph exports.
No telemetry or automatic chat-session collection was found in the application
paths reviewed. Captured evidence and exports can contain private data by
design. The runner executes trusted local code and is not an OS sandbox;
see [SECURITY.md](../SECURITY.md).

Before repository recreation, 488 tests and Ruff passed, along with CI and
security checks. Wheel and source distributions were inspected (43 wheel files,
117 source-distribution files). Local policy and credential checks passed.
Hook probes rejected synthetic credentials, private paths, personal data,
secret commit messages, inline suppression, and secrets deleted in later commits.
After recreation, [main CI](https://github.com/xyle-labs/xyle-trace/actions/runs/34930433487)
and [Security](https://github.com/xyle-labs/xyle-trace/actions/runs/34930433434)
passed. The post-publication local baseline passed 493 tests, Ruff, and the
history policy scan. GitHub reported no secret-scanning or dependency alerts
at review time. CodeQL reported one TLS protocol finding; HTTPS capture now
explicitly requires TLS 1.2 or newer, with regression coverage for the protocol
floor and certificate/hostname verification. GitHub must rescan the merged fix
before the alert can be considered resolved there.

## Verified public repository protections

The following live GitHub settings were verified on 2026-09-15:

- Secret scanning and push protection are enabled.
- [The active main ruleset](https://github.com/xyle-labs/xyle-trace/rules/23409837)
  matches [the maintained policy](../.github/main-ruleset.json): PRs, linear
  history, resolved review threads, and all six CI/security checks are required;
  force pushes and deletion are blocked, with no bypass actors. The required
  approving-review count is zero.
- Private vulnerability reporting is enabled. Workflows from all external
  contributors require approval.
- Actions are limited to GitHub-owned actions, SHA pins are mandatory, workflow
  tokens default to read-only, and workflows cannot approve PRs.
- CodeQL default setup is enabled for Python and Actions with the extended query
  suite and a weekly schedule.

## Remaining release work

The native protections previously unavailable under the private plan are now
enabled. The observed `Jesse-jApps` test-merge identity is now owner-approved.
Dependency update review and package release remain tracked in
[#4](https://github.com/xyle-labs/xyle-trace/issues/4). The PyPI project metadata
endpoint returned 404 at review time; installation instructions use the public
Git repository until a release exists. Public repository visibility does not
itself authorize publishing a package.

References: [Gitleaks](https://github.com/gitleaks/gitleaks),
[GitHub secret scanning scope](https://docs.github.com/en/code-security/reference/secret-security/secret-scanning-scope),
[GitHub sensitive-data removal](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
