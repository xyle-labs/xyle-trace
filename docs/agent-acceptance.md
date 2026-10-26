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
reported it honestly. Future shipped guidance now explains that copying a
retained result to an ordinary output path is a separate materialization that
needs its own observed run receipt. That guidance was not injected into this
exercise or used to repair its graph.

The initial approved phase demonstrates useful capture in one real task, with a
known output gap. The continuation demonstrates persisted corrections, backing,
and retained results, but did not complete as a native host turn. Its terminal
failure cause remains unknown; no third phase was used to make the result look
complete. Future observers should retain a concise sanitized terminal error
sentence alongside aggregate counters when a native turn fails.

**Host acceptance remains open:** Codex continuation completion, working-output
capture, and observed transport/ordering need another deliberately scoped check;
Claude live behavior has not been observed. These findings do not certify general
host adherence or replace the separate installed-package and protocol tests.

The final integrated package was separately verified by an outside-source wheel
smoke test and exact source/skill byte comparison, with wheel SHA-256
`6b0e2e8c22b1d011a351c28aae7d9c7c1ed9556442137e14b9223b0a716a84b9`.
That is package verification, not an additional live-host exercise.

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
