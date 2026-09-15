---
name: reproducible-runs
description: Gate analytical computation, choose local retained-input execution or reported capture, and verify replay results. Use when executing, reproducing, or recovering transformations; distinguish verified replay from external execution reports.
---

# Gate computation and report its actual outcome

Use `lineage.register`, `lineage.validate`, `lineage.record_run`, and
`lineage.query` with the configured project. Discover actual tool names and schemas;
each takes one `request` object. The MCP server does not execute the computation.
Runs created by that MCP tool have `capture_mode: "reported"` and
`reproducible: false`. Its fingerprints do not freeze records or isolate execution.

## Choose local execution when the program fits

For an authorized self-contained standard-library Python program that reads JSON
from `sys.argv[1]` and writes finite JSON to `sys.argv[2]`, use the installed local
`xyle-trace-run execute` command. Supply `--project`, `--db`, `--root`,
`--contracts`, `--key`, `--transformation` (the registered ID), repeated `--input`
IDs, and a project-relative `--script`; `--params` optionally reads a JSON object
file. Use the project's configured paths. Do not create a duplicate reported run.

Input JSON contains `params` and `inputs` keyed by node ID. Each input includes
`node`, dataset `records` (values at `record.data`), and lineage context. File
inputs include `file_base64`; consume those bytes, not mutable original paths.
The runner retains code, concrete inputs, parameters, policy receipt, environment
identity, and canonical JSON outputs. It checks current gates before new execution.

Use `xyle-trace-run replay` with the same configuration, a fresh `--key`, and
`--run` equal to a successful managed run's ID. Replay consumes retained contents
and the original policy receipt; it does not authorize current edited inputs.
It refuses corrupt content or a different interpreter/platform and compares output
hashes. A successful replay has `capture_mode: "managed"`, `replay_verified: true`,
and `reproducible: true`; report this as a verified matching replay in the recorded
environment, not universal determinism or OS isolation. Original runs stay unchanged.

The runner uses `-I -S`, no site packages, a temporary directory, and a minimal
environment. It executes trusted code and is not a security sandbox. External
dependencies and hidden inputs fall outside this narrow replay contract. Preserve
the `.lineage/snapshots` contents with the database. Failed or ambiguous `started`
runs do not justify silently rerunning side effects; inspect before a new execution.
For programs outside this contract, use the reported capture workflow below.

## Before execution

1. Identify the real transformation and its stable `natural_key`, which is also
   the policy run name. Register its description and known `code_reference`.
   Keep the same identity when matching `blocks` patterns; never rename a run to
   evade a gate. Register actual inputs as datasets, snapshots, or artifacts.
2. Call `lineage.validate` with that `run_name`. Inspect global `run_blocked`,
   `blocked_patterns`, gaps, and `policy_hash`. Dataset filtering only narrows gap
   display. Missing or malformed policy is an error, not an empty policy.
3. Resolve blocking gaps using observed evidence or justified, permitted
   assumptions. Do not relax contracts merely to execute. Validation is preflight
   against current state, not a durable execution authorization.
4. Immediately before the computation, call `lineage.record_run` with
   `action: "start"`, a stable `execution_key` for this actual execution,
   `transformation_id`, `input_ids`, `params`, and the required `code_reference`
   (null when unknown). Start rechecks the matching policy and input state.
   Keep the returned run ID, revision, fingerprints, policy hash, and capture gaps.
   Do not proceed if start refuses the run.
5. Execute the authorized computation through the project's execution tools.
   For a Python callable, the core `run_guarded` helper in
   `xyle_trace.contracts.validator` can check contracts immediately before
   invoking it: pass the store, project ID, actual transformation natural key,
   freshly loaded contracts from `xyle_trace.contracts.loader.load_contracts`,
   and the zero-argument operation. This still does not isolate or freeze inputs.

Record the code/version and environment information actually available; keep
unknown versions explicit. Do not assert reproducibility even when a code
reference and all current file hashes are present. Reported capture alone is not
the local runner's verified replay.

## Finish or recover

Register actual output datasets, artifacts, or indicators. Call `lineage.record_run`
with `action: "finish"`, `run_id`, the returned `expected_revision`, `status`, and
`outputs` entries containing `node_id` and an optional project file `path`.
An output path must match its registered path when one exists.

- `succeeded` requires at least one output and no failure detail. Report success
  only after observing execution complete and inspecting its results.
- `failed` or `partial` requires `failure_detail`; outputs can be empty. Preserve
  real partial outputs and say which are missing. A missing file cannot become
  a successful produced artifact merely through registration.

Finish records the observed outcome even if policy has since changed. Inspect
returned output fingerprints, hashes, and `capture_gaps`; do not hide gaps in a
success summary. The caller reports status; server capture is not execution proof.

If a response is lost, retry the identical request. On restart, explain the run
before executing anything: a matching start retry may return an already finished
run. A persisted `started` state does not prove the computation ran or failed.
Recover from available execution evidence and avoid rerunning side effects blindly.
A changed input, policy, transformation, or report can produce a conflict; reassess
it rather than changing keys to force a retry. Use a new execution key only for
an actual new execution. Terminal run reports are not editable histories.
