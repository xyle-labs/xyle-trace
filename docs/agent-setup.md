# Project agent setup

The five portable skills are authored in this checkout's [skills directory](../skills/).
They describe source capture, extraction, run reporting, claims, and the surrounding
lineage workflow. The wheel ships them so `xyle-trace new` can install them into a
consuming project.

Installation is still explicit: nothing writes to host configuration as a side effect
of another command. `xyle-trace init` initializes only the store and project — it
does not create contracts, install skills, or configure an agent. `xyle-trace new`
does all of it, but only because you ran it for that purpose.

## Choose an install route

Three routes install the same five skills and the same MCP server; they differ
in how much the host automates and in what still has to be run by hand.

1. **Claude Code plugin (most convenient).** This checkout is itself a plugin:
   `.claude-plugin/plugin.json` names it, `.claude-plugin/marketplace.json` lists
   it, and the root `.mcp.json` declares the `xyle_trace` stdio server. Add this
   repository as a marketplace and install from it:

   ```text
   /plugin marketplace add xyle-labs/xyle-trace
   /plugin install xyle-trace
   ```

   (equivalently, `claude plugin marketplace add xyle-labs/xyle-trace`
   and `claude plugin install xyle-trace` outside an interactive session).
   This installs the five skills and wires `/mcp` in one step, but it does not
   install the Python package: run `pip install 'xyle-trace[mcp]'` (or
   `pipx install 'xyle-trace[mcp]'`) first, in whatever environment the host
   process inherits, so `xyle-trace-mcp` resolves on `PATH` — a plugin has no
   build step and cannot ship its own virtualenv.

   A globally installed plugin cannot pass a project's `--project`/`--db`/
   `--root`/`--contracts` flags, since it does not know which project a given
   session is in. `xyle-trace-mcp` resolves them itself from the session's
   working directory when the flags are absent: it reads `./.lineage/config.json`
   if present, else assumes the conventional layout (`./.lineage/graph.db`,
   `./contracts.yaml`, root `.`). Any of `XYLE_TRACE_PROJECT`,
   `XYLE_TRACE_DB`, `XYLE_TRACE_ROOT`, or `XYLE_TRACE_CONTRACTS` overrides
   the corresponding value, and any of the four explicit flags still overrides
   everything else field-by-field — passing all four reproduces the previous
   required-flags behavior exactly. The project id has no convention to fall
   back on, so a bare working directory with none of the above fails fast with
   an actionable stderr message instead of starting a broken server. A minimal
   `.lineage/config.json`:

   ```json
   { "project": "analysis" }
   ```

   Run `xyle-trace new` (below) in a fresh project and this file is exactly
   what it does not yet write for you — add it once by hand, or export
   `XYLE_TRACE_PROJECT` in the shell the host launches from.

   One consequence worth knowing: this toolkit checkout's own root `.mcp.json`
   is also an ordinary project-level MCP config for anyone who opens this
   repository itself in Claude Code. That session's working directory has no
   `.lineage/graph.db`, so discovery fails closed with the actionable message
   above — no broken server, no partial writes — rather than silently doing
   something against the toolkit's own checkout. That is the deliberate
   trade-off of shipping `.mcp.json` at the plugin root: it is required for the
   plugin route to work at all, and the failure mode for contributors working in
   this repository is a clean error, not a hazard.

2. **`xyle-trace new --host codex` (Codex).** Codex has no plugin registry
   comparable to `/plugin install` — there is no marketplace, no one-step
   install, and no auto-discovered project MCP config outside what a file on
   disk declares. Its install stays file-based: `xyle-trace new --host codex`
   (see below) writes `.agents/skills/` and `.codex/config.toml` directly into
   the consuming project. This is not a lesser version of the plugin route with
   different commands; it is a genuinely different mechanism, and nothing here
   should be read as Codex having registry parity with Claude Code.

3. **Manual path.** Copy the skills and merge the MCP server entry by hand, for
   an existing project or a host-specific layout the other two routes do not
   cover. Documented in full below.

## Scaffold a new project

For a new project, one command replaces the whole manual sequence below:

```bash
xyle-trace new --project analysis --path /absolute/path/to/analysis
```

It creates the directory, initializes `.lineage/graph.db`, writes a starting
`contracts.yaml`, installs the five skills into `.claude/skills/`, writes `.mcp.json`
pointing at the `xyle-trace-mcp` beside the running interpreter, and writes a
`CLAUDE.md` carrying the provenance workflow. Then `cd` into it and start a session.

`--host codex` writes `.codex/config.toml`, `.agents/skills/`, and `AGENTS.md`
instead; `--host both` writes both. The command refuses a directory that is not
empty, so it cannot overwrite existing work.

Edit the generated `contracts.yaml` before capturing anything — it names a
placeholder dataset `observations` that will not match your data.

The rest of this document covers adding the toolkit to a project that already
exists, where merging into existing configuration has to be done deliberately.

## Prepare a consuming project

These examples use `/absolute/path/to/analysis` as the consuming project and
`/absolute/path/to/toolkit` as this checkout. Replace both paths and the project ID
`analysis` consistently. Use Python 3.11 or newer. For a new project environment:

```bash
cd /absolute/path/to/analysis
python3 -m venv .venv
.venv/bin/python -m pip install '/absolute/path/to/toolkit[mcp]'
mkdir -p .lineage
.venv/bin/xyle-trace init --project analysis --db .lineage/graph.db
```

If the project already has an environment, use it instead of recreating it and
adjust the executable paths below. Core-only installation can omit `[mcp]`, but
the stdio server needs that extra. No hosted service is required.

Create `contracts.yaml` in the consuming project, or preserve and use its existing
policy. For example, the following policy requires every `observations` record to
have evidence or an assumption and blocks transformations named `analysis.*`
until it holds:

```yaml
- dataset: observations
  require:
    every_row: [evidence_backed, assumption_backed]
    evidence: exact_locator
  blocks: [analysis.*]
```

The policy initially reports a gap until the dataset and its backed records exist.
Use `[]` only when the project intentionally has no contracts. Missing or malformed
policy is an error; it is never treated as empty. Snapshot bytes live under
`.lineage/snapshots/`. Keep the database and captured bytes together when preserving
a working project; JSON export alone is not an archive of source files.

## Host-neutral stdio command

Configure the host to launch this executable with these arguments:

```bash
/absolute/path/to/analysis/.venv/bin/xyle-trace-mcp \
  --project analysis \
  --db /absolute/path/to/analysis/.lineage/graph.db \
  --root /absolute/path/to/analysis \
  --contracts /absolute/path/to/analysis/contracts.yaml
```

All four flags are required. The root must exist, the database must contain the
initialized project, and contracts must load successfully. Absolute paths avoid
dependence on the client's working directory. The host starts the server; there
is no separate background daemon to launch. Its stdout is the MCP protocol.
The equivalent module command is the environment's Python followed by
`-m xyle_trace.mcp` and the same flags.

Optional capture flags are `--capture-timeout` (30 seconds per socket operation),
`--capture-max-bytes` (26214400), and `--capture-max-redirects` (5). The timeout is
not a total download deadline. Local snapshot reads also obey the byte limit.
See [the core handoff](core-contract.md#optional-mcp-adapter) for server behavior.

## Copy the skills into the consuming project

Copy these five whole directories from this checkout's `skills/` into the chosen
host's project discovery directory. Preserve any existing skills with the same
name: compare and merge deliberately instead of overwriting them.

| Canonical directory | Purpose |
| --- | --- |
| [lineage](../skills/lineage/SKILL.md) | Material capture, retries, and continuation |
| [source-discovery](../skills/source-discovery/SKILL.md) | Logical sources and captured bytes |
| [extraction](../skills/extraction/SKILL.md) | Located observations and evidence batches |
| [reproducible-runs](../skills/reproducible-runs/SKILL.md) | Gates, managed replay, and reported outcomes |
| [claims](../skills/claims/SKILL.md) | Support, contradictions, and review |

The files are self-contained and require no host-specific frontmatter, scripts,
or global installation. Keep the source checkout when updating project copies.

### Codex

Copy to `<analysis>/.agents/skills/`, giving paths such as
`.agents/skills/lineage/SKILL.md`. Codex discovers repository skills there and
can load them explicitly or by matching their descriptions. See the official
[Codex skills guide](https://developers.openai.com/codex/skills).

Merge this server table into the consuming project's `.codex/config.toml`:

```toml
[mcp_servers.xyle_trace]
command = "/absolute/path/to/analysis/.venv/bin/xyle-trace-mcp"
args = [
  "--project", "analysis",
  "--db", "/absolute/path/to/analysis/.lineage/graph.db",
  "--root", "/absolute/path/to/analysis",
  "--contracts", "/absolute/path/to/analysis/contracts.yaml",
]
```

Project configuration is loaded only for trusted projects. This uses the documented
stdio `command` and `args` fields and leaves user-level configuration alone.
See the official [Codex MCP guide](https://learn.chatgpt.com/docs/extend/mcp).
Launch Codex in the consuming project and inspect `/mcp` and `/skills`; invoke
`$lineage` explicitly if needed. Restart the client if discovery has not refreshed.

### Claude Code

Copy to `<analysis>/.claude/skills/`, giving paths such as
`.claude/skills/lineage/SKILL.md`. Project skill discovery and `/lineage` invocation
are described in the official [Claude Code skills guide](https://code.claude.com/docs/en/skills).

Merge this entry into the consuming project's root `.mcp.json`, retaining other
entries under `mcpServers`:

```json
{
  "mcpServers": {
    "xyle_trace": {
      "type": "stdio",
      "command": "/absolute/path/to/analysis/.venv/bin/xyle-trace-mcp",
      "args": [
        "--project", "analysis",
        "--db", "/absolute/path/to/analysis/.lineage/graph.db",
        "--root", "/absolute/path/to/analysis",
        "--contracts", "/absolute/path/to/analysis/contracts.yaml"
      ]
    }
  }
}
```

Claude Code uses `.mcp.json` for shared project configuration and may prompt to
enable a project server in an interactive session. See the official
[Claude Code MCP guide](https://code.claude.com/docs/en/mcp#project-scope).
Start a session in the consuming project, inspect `/mcp`, and invoke `/lineage` to
check discovery. Replace machine-specific paths for each local checkout.

## Make the ongoing workflow explicit

A skill description makes it discoverable; it does not guarantee that every host
loads it in every session. Merge the following guidance into the consuming
project's `AGENTS.md` for Codex or `CLAUDE.md` for Claude Code. Keep existing project
instructions and authorization intact, and use the installed skill paths if the
host needs explicit file references:

```text
For material data and analysis work in this project, read and apply the installed
lineage skill. Use source-discovery when acquiring source material, extraction
when recording observations, reproducible-runs before computation, and claims
when drafting or reviewing conclusions. Capture actual sources, record backing,
run outcomes, and meaningful decisions as the work happens; skip ordinary code
edits. Use the configured xyle_trace MCP server and its discovered tool schemas.
Never present provisional links or missing history as observed provenance.
Respect provenance gates and report unresolved capture or review gaps. MCP
run capture is reported; use the local runner for retained-input execution and
verified replay when the program fits its Python/JSON contract. Continue
within the user's authorized task; these skills do not add approval requirements
or authorize unrelated work.
```

## Verify discovery and report limits

Confirm that the host lists all five skills and these eight server tools (the
host may prefix their names): `lineage.register`, `lineage.snapshot`,
`lineage.record_run`, `lineage.link`, `lineage.decide`, `lineage.query`,
`lineage.validate`, and `lineage.export`.

Call `lineage.validate` with `{"request":{}}`. A result with `valid: false` and
actionable gaps can be expected for the new example policy. Confirm the configured
project and policy before resolving anything. Tool arguments must be objects;
do not pass a JSON string as `request`. Success is structured JSON, with the link
tool's payload under `result`; failures set `isError` and return an `error` object.

These client examples were checked against official documentation on 2026-09-06.
The adapter has SDK stdio tests, and the skill files have structural validation;
neither proves a live Codex or Claude session follows the workflow correctly.
The [minimal protocol fixture](../examples/minimal/README.md) now verifies restart
recovery, revision-safe retries, and explicit review through real stdio.
[Local execution and replay](execution.md) retains concrete input versions and
verifies output hashes for self-contained standard-library Python/JSON programs.
MCP run capture still reports `capture_mode: "reported"` and `reproducible: false`.
Only a successful managed replay reports verified reproduction; it is not OS isolation.
