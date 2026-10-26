# Observe capture in a real agent host

Installed-package and protocol fixtures establish that capture operations work.
They do not establish that a host agent chooses correct operations during an
ordinary task. This check starts with a fresh synthetic project and audits what
an actual host recorded, including missing captures and interventions.

## Bounded protocol

1. Build a wheel and install it into a separate virtual environment. Reusing
   local development dependencies is acceptable; verify that `xyle_trace` itself
   imports from the new installation. Scaffold a new project with the installed
   `xyle-trace new --project host-acceptance --path PROJECT --host codex` command.
   Do not seed provenance beyond the scaffold's project node.
2. Create `observations.csv` containing `sample,count`, `A,10`, and `B,20` on
   separate lines. Preserve the generated policy, guidance, and installed skills.
3. Use the host's supported authentication-status command to establish an
   existing subscription login. Retain the authentication category only. Stop
   if subscription authentication cannot be established; do not substitute an
   API key, copy credentials, or inspect authentication files.
4. From the synthetic project, run one normal noninteractive host session. For
   the tested Codex interface the invocation is:

   ```bash
   codex --no-daemon -a never exec --ephemeral --ignore-user-config \
     --strict-config --skip-git-repo-check --sandbox workspace-write \
     -C PROJECT --json -c 'forced_login_method="chatgpt"' -
   ```

   Read the prompt from standard input. Ask the agent to read the CSV, calculate
   the combined count, write `result.json`, follow the installed project
   provenance guidance, and explain the source, calculation, and capture gaps.
   Do not supply a sequence of capture requests. Prohibit policy changes,
   installations, network use, publication, delegation, and inspection outside
   the project. Keep the final response below 200 words.
5. Bound each phase externally: 180 seconds, 24 tool starts, and 128 KiB of host
   output. Ask for at most 20 tool calls. Consume events in memory to enforce
   caps and retain aggregate counts only. Do not publish raw session output,
   system prompts, authentication details, or private host settings. Stop at an
   authentication, trust, permission, or infrastructure gate; do not bypass it.
6. Independently inspect the persisted graph and actual output. Check captured
   source bytes, record values, precise evidence locations, resolutions, run
   outcome, output hashes, and remaining policy gaps. Inspect whether a gate was
   observed before calculation; a final graph alone cannot prove execution
   ordering or semantic support. Distinguish reported runs from managed runs and
   verified matching replay. Record manual interventions separately.
7. After a completed first phase, change only the synthetic CSV to `A,12` and
   `B,20`. Start a fresh host process over the same project and ask it to update
   the result from the changed source, preserving provenance and identifying
   effects on prior findings. Do not reuse a saved host conversation or seed
   correction/evidence records. Audit the graph again: expected current total
   is 32; prior results and capture gaps must remain honest. Historical input
   inspection is possible only where retained contents actually exist.

The normal host runtime needs its usual local state and IPC permissions. A
startup failure before agent events is a host-launch failure, not an adherence
result. When the enclosing execution sandbox denies ordinary host startup,
request narrowly scoped approval for the same normal host invocation; retain the
host's workspace sandbox and existing trust/authentication controls. If approval
is rejected, preserve the rejection and stop. Do not relocate authentication
state, weaken trust, or use dangerous bypass options to make the check pass.

A CLI capture fallback is useful when MCP is unavailable, but it has the same
schema, revision, and policy constraints. `record_run` through either transport
remains reported capture and does not execute the calculation.

## Findings

The findings below distinguish a launch attempt from observed task behavior.

### Package and launch boundary (2026-10-26)

The observed host exercise used an offline-built wheel with SHA-256
`13f6a56a08540097527662c62a0fdc97decdb2e7c1d27f8feaa0d34117e21537`.
It was installed into a new virtual environment; existing local dependencies
were reused. The wheel included the shipped CLI fallback guidance. Subsequent
repository changes were not retroactively exercised by that host session; a
separate fresh-wheel smoke check validates the final integrated package.

Supported status commands established existing ChatGPT authentication for Codex.
Claude subscription authentication was not established, so no Claude task was
attempted. Authentication material and private account identifiers were neither
printed nor copied.

Two enclosing-sandbox launch attempts exited before any agent event or tool
call. The actionable diagnostics were a read-only native logs database and
permission denial while initializing the in-process app-server client. These
were runtime state/IPC launch failures, not capture failures. Approval review
then authorized the same normal host invocation with ordinary runtime access;
the child agent retained its workspace sandbox and existing trust/authentication
controls. No global configuration or authentication state was replaced.

The first approved phase completed in 144.65 seconds, with 16 tool starts and
76,905 output bytes. None of its external caps fired. No human supplied capture
requests or repaired the graph. The observer retained counts rather than a tool
transcript, so the capture transport and preflight-versus-computation ordering
are unobserved for this phase. Graph reachability and a final policy pass do not
establish either fact.

| Check | Initial approved phase | Fresh-host continuation |
| --- | --- | --- |
| Native session outcome | Completed, exit 0. | Durable updates observed; native `turn.failed`, exit 1. Terminal error cause was not retained. |
| External bounds | 144.65 s; 16 tool starts; 76,905 bytes. No cap fired. | 156.38 s; 15 tool starts; 119,659 bytes. No cap fired. |
| Capture transport | Unobserved; aggregate counter did not retain tool kinds. | 15 shell-command starts; 0 MCP tool starts. Whether shell commands used CLI capture or Python service calls was not retained. |
| Source and observations | One source, one verified snapshot; A=10, B=20. | Same logical source, two verified snapshots; current A=12, B=20. |
| Record backing | Both rows evidence backed at the actual CSV lines 2–3. | Both current rows evidence backed; independent comparison against their referenced snapshot matched 2/2 rows. |
| Calculation and output | Total 30. One failed managed attempt was preserved, followed by a successful managed run and verified matching replay. | Total 32. Two further successful managed runs, including verified matching replay, were persisted before the native turn failed. |
| Retained output hashes | Original and replay output hashes matched retained bytes. | All four successful retained outputs matched their hashes; prior total 30 remained available. |
| Historical inputs | Retained counts 10 and 20. | Explicit historical explanation still returns 30 from retained 10/20; the newer result returns 32 from retained 12/20. Current rows were not substituted into the older result. |
| Correction history | No correction yet. | One correction receipt preserves A=10, its revision, prior resolution, and evidence receipt before A became 12. |
| Claims | One claim marked supported/reviewed by the agent. | One current claim explicitly states 32 replaces 30 and is marked supported/reviewed by the agent. This is not human review or an independent support judgment. |
| Material capture gap | Working `result.json` was registered without a producing-run receipt or recorded output hash. | The same orphan/unobserved working artifact remained. Retained managed outputs were traced; their working-file copy was not. |
| Current policy and integrity | Policy passed; retained bytes verified; integrity unknown because the working artifact was unobserved. | Generated policy unchanged and passing; no missing retained bytes; integrity still unknown for the working artifact. |
| Preflight ordering | Unobserved by this observer. | Unobserved by this observer. |
| Human capture intervention | None; no seeded evidence or repaired receipts. | None; only the synthetic CSV was changed between fresh host processes. |

The preserved failed managed attempt records a normal program failure (`program
exited with code 1`); it was not rewritten into success. The later working-file
copy gap is an observed agent capture omission. Existing health inspection
reported it honestly. This earlier wheel predated runner-managed `--out`
delivery. Its graph was not repaired; the later candidate below adds that
capability and was exercised in a new project.

The initial approved phase demonstrates useful capture in one real task, with a
known output gap. The continuation demonstrates persisted corrections, backing,
and retained results, but did not complete as a native host turn. Its terminal
failure cause remains unknown; no third phase was used for this earlier wheel.
Future observers should retain a concise sanitized terminal error
sentence alongside aggregate counters when a native turn fails.

**Outcome for this earlier wheel:** Codex continuation completion, working-output
capture, and observed transport/ordering remained open at that stage; Claude
live behavior had not been observed. These findings do not certify general host
adherence or replace the separate installed-package and protocol tests.

At that stage, an integrated package was separately verified by an outside-source wheel
smoke test and exact source/skill byte comparison, with wheel SHA-256
`6b0e2e8c22b1d011a351c28aae7d9c7c1ed9556442137e14b9223b0a716a84b9`.
That is package verification, not an additional live-host exercise.

### Delivery-capable candidate wheel host exercise (2026-10-26)

A later, fresh synthetic project used the installed candidate wheel with SHA-256
`8c40159945f9d9878e912ac86aac1eef387512d1477029b7979e659fb397f306`.
The wheel's 42 packaged source and skill entries byte-matched the frozen tree;
an outside-source package smoke check had already passed. Existing ChatGPT
subscription login was confirmed. The native child retained workspace-write,
approval-never, ephemeral execution, and ignored user configuration. No capture
requests or `--out` procedure were supplied in either task prompt, and no graph
record was manually repaired.

| Check | Initial A=10, B=20 | Fresh-process A=12, B=20 continuation |
| --- | --- | --- |
| Native turn and observer | Completed, exit 0; 78.12 s, 18 tool starts, 86,428 bytes; no cap. | Observer stopped the turn at its 24-start budget when start 25 arrived; SIGTERM exit -15 after 58.71 s, 85,793 bytes. This is a self-imposed observation limit, not a host failure. |
| Observed transport and ordering | Shell command kinds included eight lineage record calls, four managed-run invocations, and one separate validate start; no MCP starts. | Shell command kinds included six lineage record calls, four managed-run invocations, and three separate validate starts; no MCP starts. |
| Source and row backing | One verified snapshot captured A=10/B=20; both records resolved as evidence backed to the captured CSV lines 2–3. | A second verified snapshot captured A=12/B=20; both current rows matched their referenced snapshot and evidence locator. |
| Calculation and working output | One preserved failed managed attempt, then a successful total 30 and verified matching replay. `result.json` was 30, had a producing-run receipt, and matched its recorded working-file hash. | Successful total 32 and verified matching replay were durable before the cap. `result.json` was 32 and matched the updated working-file receipt and producing run. |
| History and review | A claim for 30 was marked supported and reviewed by the agent. | A correction receipt preserved old A=10, its backing, and revision; explicit historical explanation still returned 30 from retained 10/20 while the new result returned 32 from retained 12/20. The old and new claims both remained `needs_review` when stopped. |
| Integrity and policy | Doctor reported valid policy, verified retained integrity, no capture gaps or orphan artifacts. | Doctor again reported valid policy, verified retained integrity, no capture gaps or orphan artifacts, and no started run. The generated policy and calculation script hashes were unchanged. |

The observer kept aggregate event and safe command-kind counts, plus generic
sanitized error categories. It did not retain raw commands, event bodies, prompts,
or outputs. A separate validation command start was visible in each phase; the
managed runner's internal policy gate and actual computation order were not
observable from those start events. The observer therefore records preflight
ordering as unknown, rather than treating a final valid policy as execution-order
proof. The continuation's generic process-exit category reflects the observer's
own SIGTERM after its tool cap and does not describe a native Codex error.

The initial turn closed the previous working-output capture gap for this wheel.
The stopped continuation left correct durable computation and historical
records. Its historical claim's earlier agent review no longer covered the
changed claim version, and the new current claim had no recorded review yet;
both were `needs_review`. The original stopped attempt and its pending reviews
remain part of this observation, rather than being reclassified as a completed
turn.

#### Recovery of the saved continuation

One separately authorized natural recovery prompt asked a fresh host process to
finish the saved work using its existing results. It did not supply capture
requests or a repair recipe. The CSV, calculation script, policy, installed
wheel, and graph were not manually edited. This phase used an explicit budget
of 36 tool starts, 180 seconds, and 192 KiB; it did not rerun the stopped prompt
under an undisclosed larger cap.

| Recovery check | Observed result |
| --- | --- |
| Native turn | Completed, exit 0; 101.06 s, 13 tool starts, 123,579 bytes. No external cap fired. |
| Transport and computation | Shell inspection/capture/export/validation starts were observed; no MCP starts or managed execution invocation was observed. |
| Persisted run set | Exactly the same five run IDs and run receipts as the stopped continuation: one preserved failed attempt and four successful managed runs. Recovery added no recorded execution. |
| Working output and backing | `result.json` remained 32 with a matching registered hash and successful producing-run receipt. Current A=12/B=20 both matched verified captured rows and retained evidence resolutions. |
| Retained history | All five bundle hashes and all four existing output hashes verified; retained outputs remained 30, 30, 32, 32. Explicit explanations returned 30 from retained 10/20 and 32 from retained 12/20. The correction still preserved old A=10, revision, resolution, and evidence receipt. |
| Claim review | One new review decision covered both historical and current claims, with matching current-revision receipts. Their texts and support statuses were unchanged; both ended supported/reviewed. These are agent-authored review records, not human judgment or independent support certification. |
| Health and unchanged inputs | Policy valid, integrity verified, zero pending claims, orphan artifacts, capture gaps, or started runs. Policy and calculation-script hashes matched both earlier audits. |
| Independent audit | Used the installed wheel and a SQLite read-only/query-only connection. Graph hash unchanged and zero connection writes. No graph repair or model session was performed by the auditor. |

The completed recovery demonstrates continuation from the persisted synthetic
graph after an observer-imposed stop. It preserves both the interrupted turn
and the later successful outcome. Observer command categories and the unchanged
run set support reuse of existing recorded results; they do not prove that no
unrecorded arithmetic occurred internally or establish preflight execution
ordering.

**Remaining acceptance gates:** Claude live behavior, the live MCP/onboarding
path (no MCP use was observed), and exact preflight ordering remain unverified.
Interactive browser acceptance remains a separate blocked gate. This bounded
CLI-path exercise supports the synthetic capture, working-file receipt, history,
and fresh-host recovery; it does not certify general host adherence or semantic
support for arbitrary claims.

After this host exercise, the HTML renderer gained separate projected-view
checksums in its links. This detects changed record values even when node
metadata revisions are unchanged. That pre-merge candidate wheel's SHA-256 is
`5dba981a8b41e1b8d4b511c56b0f0e8e5bc9df85ae829c77f3cd929c486a1a1c`.
All other 41 packaged source/skill entries, including execution, capture, and
installed guidance, were byte-identical to the live-exercise wheel. That candidate
separately passed the outside-source delivery/explanation/health/report/
handoff smoke test. JavaScript behavior tests cover link mismatch warnings;
this is not interactive browser acceptance or another live-host observation.

### Post-review package verification (2026-10-26)

The subsequent review fixed managed-result connection paths, explicit gaps for
omitted current relationships, natural-key correction history/report projection,
and policy-free restore discovery. It also reconciled the documentation and
updated all CI jobs to the reviewed Actions versions.

The new wheel SHA-256 is
`b1c6e1e74e894dcaa555adf2755829366f0fcc30fcf5aaa455762c10501df2af`.
All 42 packaged source/skill entries matched the reviewed source. A fresh virtual
environment installed that wheel outside the checkout and passed the capture,
delivery, doctor, exact-result, report-hash, bundle-restore, and history smoke
test. The full local suite passed 712 tests, with Ruff and diff checks clean.
These are package and regression checks; no additional live-host exercise or
interactive browser acceptance is claimed for this artifact.

## Supported-interface references

The local CLI help was checked before use. The official
[Codex developer-command reference](https://learn.chatgpt.com/docs/developer-commands#codex-exec)
documents noninteractive execution, ephemeral sessions, sandbox selection, and
JSON events. The official
[authentication requirements guidance](https://learn.chatgpt.com/docs/enterprise/managed-configuration#manage-authentication-locally)
describes ChatGPT versus API login restrictions and the relationship of
`forced_login_method` to managed requirements. Neither documentation nor a login
status check establishes successful task capture; the observations above are
separate evidence.
