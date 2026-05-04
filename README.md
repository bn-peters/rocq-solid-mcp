# Ben's Rocq MCP

If you notice that your agent misbehaves with this MCP, please let me know! It
would be particularly helpful if you could share your transcripts, which you
can find at ~/.codex/sessions/.

The MCP exposes two tools:
1. `check`, which generates diagnostics for a given file. It can return errors
   from multiple proofs at once. Proof states are cached. 
2. `run_at`, which runs one-off commands at a given position in a file. This is
   intended to be used for queries like `Search` and `Check`, or for one-off
   debugging commands.

## Installation

This package depends on a patched version of `coq-lsp`. It is compatible with
Rocq 9.0, 9.1, and 9.2. To install it:
```bash
opam repo add bpeters https://gitlab.mpi-sws.org/bpeters/opam.git
opam install bens-rocq-lsp
```
You might be asked to uninstall `coq-lsp`. Don't worry, `bens-rocq-lsp` is
(probably) compatible with your other tools. It must be installed in all opam
switches you plan to use Ben's Rocq MCP with.

Then:
```
uv tool install -e .
```

## Usage

To teach Codex about Ben's Rocq MCP:
```bash
codex mcp add bens-rocq-mcp bens-rocq-mcp
```

The MCP figures out the right switch itself if you have linked with `opam
switch link . [switch]`. The MCP currently does not support `dune`-based
projects; Your project must contain a `_CoqProject` or `_RocqProject` file with
suitable `-R` and `-Q` entries.

You should consider disabling all other Rocq MCPs. If you have slow-to-compile
files, you should consider increasing tool timeout; This MCP has its own
timeout mechanism. Add this to `.codex/config.toml` in your repository:
```
[mcp_servers.rocq-mcp]
enabled = false

[mcp_servers.bens-rocq-mcp]
tool_timeout_sec = 300
env = { ROCQ_LSP_TIMEOUT = "290" }
```
Set `ROCQ_LSP_TIMEOUT` slightly lower than `tool_timeout_sec` so the MCP can
return partial timeout results before Codex cancels the whole tool call. The
`check` tool is considerably slower than standard compilation, but I expect
that caching makes up for this overall.

## Tools

The server exposes two tools: `check` for obtaining file diagnostics and
`run_at` for running one-off commands.

Find the full tool definitions in
[src/rocq_lsp_mcp/server.py](src/rocq_lsp_mcp/server.py). **You should read
those!**






**The rest of this file is a long AI-generated repository summary. I didn't
feel like deleting it, but I'm not convinced it's useful :)**

# bens-rocq-mcp


Prototype MCP server that checks Rocq files and runs one-off Rocq commands
through persistent project-scoped `coq-lsp` sessions. Check diagnostics report
errors only and expose a human-facing `level`, `location`, message, optional
`proof_name`, and compact proof-state context when available. Recompile the
file with Rocq, for example `rocq c path/to/file.v`, when warnings are needed.
`run_at` results expose compact feedback output for the provided commands
string, including warning feedback when `coq-lsp` returns it. Check results include
`checking_time_seconds`, rounded to
milliseconds, and may include `long_running_commands` entries with source text
and location for commands that actually ran during that check and exceeded the
80% alert threshold derived from the configured request timeout. Results
include the file relative to the workspace. Raw LSP
plumbing such as document URIs, positions, duplicated ranges, versions, empty
lists, and null fields is omitted from the MCP response. Agent-facing check
results keep only the first error for each `proof_name` and, after the first
error without a
`proof_name`, suppress later errors that also lack a `proof_name`. Warnings and
infos are retained internally but omitted from the check tool response. This
broad cascade guard can hide later independent top-level errors that also lack
`proof_name`. Returned error diagnostics are limited by `ROCQ_LSP_MESSAGE_LIMIT`,
which defaults to 20 and is capped at 50. Check results include `counts` for
nonzero error diagnostics only; clean results omit `counts`. Timeout results include
a compact `timeout_context` that reports the request operation, timeout
kind and settings, the latest checked position, the latest processing range,
and a small preview of the next unchecked source line when available. The soft
coq-lsp partial-result deadline is configured with `ROCQ_LSP_TIMEOUT`; the
default is 60 seconds and the maximum is 3600 seconds. Set it slightly lower
than the host-level MCP tool timeout so the server can return partial
diagnostics before the host cancels the whole tool call, leaving room for
`ROCQ_LSP_HARD_TIMEOUT_GRACE`. Optional per-command timeouts use
coq-lsp sentence execution notifications, so the timeout unit is one Rocq
sentence such as `sleep 5.` or `repeat ... .`, not an internal Ltac substep. The
prototype talks to `coq-lsp` directly over stdio JSON-RPC.

Check calls can pass `restart=True` to tell `coq-lsp` that externally
compiled dependencies may have changed before checking the requested file.

## Architecture

- `server.py` registers the MCP tools, manages one `RocqLspManager`
  for the server lifespan, and renders internal results for MCP output.
- `manager.py` resolves files and workspaces, keeps the session registry,
  evicts idle sessions, and orchestrates internal check and `run_at` results.
- `session.py` owns each workspace session lifecycle, document versions, and
  notification store.
- `rocq_client.py` is transport-only JSON-RPC framing, requests, notifications
  such as `coq/workspace_update`, cancellation, `coq/getDocument`,
  `proof/goals`, `petanque/run_at_point`, and process cleanup.
- `notifications.py` stores diagnostics, latest file-progress notifications,
  file performance data, and sentence execution notifications published by
  `coq-lsp`.
- `pipeline.py` converts raw diagnostics plus proof-state data into internal
  `Diagnostic` models without deciding what the MCP response should include.
- `models.py` holds internal result models, `TimeoutContext`, and the
  `RenderOptions`-driven render step that applies agent-facing filtering,
  error-only check rendering, message limits, location formatting, timeout
  formatting, long-running command formatting, check-time formatting, and
  empty-field pruning.

For `Attempt to save an incomplete proof` diagnostics, the `Qed.` span may only
contain the save error. The manager asks `proof/goals` with `mode: "Prev"` at
that diagnostic position and merges the returned compact goals and bullet state
into the diagnostic proof state.

`run_at` execution uses `petanque/run_at_point`, because `proof/goals` with the
`command` parameter does not return feedback from commands such as `Search` or
`Print` in current `coq-lsp`. Call `run_at` with one string containing one or
more complete commands, for one-off tasks such as `Check`, `Search`, `Require`,
`Locate`, or testing notations in a local context. Prefer calling it right after
imports when possible, or between lemmas/definitions when local declarations,
scopes, or modules are needed. The commands are ephemeral and do not edit the
source file.

The manager infers a workspace by walking upward from the requested file and
using the first directory with `_RocqProject` or `_CoqProject`. If no project
file is found, the file's parent directory is used. Each workspace gets its own
cached `coq-lsp` process, so different projects can be checked concurrently while
files inside one project reuse the same `coq-lsp` cache. By default, each
process starts as `opam exec -- coq-lsp` with `cwd` set to the inferred
workspace so local opam switches select the matching Rocq and `coq-lsp`
versions.

If a check call has `restart=True`, the manager sends `coq/workspace_update`
with empty object params before sending the file's `didOpen` or `didChange`.
This asks the existing `coq-lsp` process to invalidate cached `Require` state
after `.vo` files have changed; it does not restart the server process and does
not rebuild dependencies. Recompile edited dependencies externally first.

When the soft `coq/getDocument` deadline fires, the response status is
`timeout` and the rendered result includes `timeout_context`. `checked_until`
is taken from the partial `completed.range.end` when cancellation returns a
partial document; if not, it falls back to the start of the latest
`$/coq/fileProgress` processing range. The context also includes
`latest_processing_range` and `next_unchecked` as compact 1-based locations
without exposing raw LSP progress payloads.

When command timeout is enabled, the client watches `$/coq/executionInformation`
notifications during `coq/getDocument`. Each new sentence resets the command
deadline. If the current sentence exceeds the configured limit, the client sends
`$/cancelRequest` and returns `status: "timeout"` with
`timeout_context.timeout_kind: "command"`, the timed-out sentence range, and a
source preview. The `ROCQ_LSP_TIMEOUT` request deadline remains the outer
server-side partial-result deadline for parsing stalls, missing execution
notifications, or non-interruptible work. MCP host cancellation is a harder
outer abort: the server sends `$/cancelRequest` to `coq-lsp` and may reset the
workspace session, but the client may already have abandoned the result.

`coq-lsp` performance data is enabled with `send_perf_data`. After successful
`coq/getDocument` calls, the manager waits briefly for `$/coq/filePerfData` and
uses sentence execution notifications from the same document version to keep
only commands that ran during the current check step. Entries with
`info.cache_hit: true` are ignored, entries below 80% of
the configured request timeout are ignored, and the rendered result is capped
at 10 long-running commands.

## Run

```bash
uv run bens-rocq-mcp
```

The server exposes two MCP tools:

- `rocq_check_file(file: str,
  command_timeout_seconds: float | None = None,
  restart: bool = False)`
- `rocq_run_at(file: str, commands: str, line: int, character: int = 1)`

`rocq_check_file` returns error diagnostics only. Warning and info
diagnostics are intentionally omitted from this tool response; recompile the
file with Rocq, for example `rocq c path/to/file.v`, when warnings are needed.
`ROCQ_LSP_MESSAGE_LIMIT` caps returned errors, defaults to 20, and is capped to
50. When errors are omitted, the response includes `omitted_counts` and
`message_limit` alongside total `counts`. The limit is applied only while
rendering the MCP response; internal check results retain all normalized
diagnostics.
`ROCQ_LSP_TIMEOUT` controls the soft `coq-lsp` partial-result deadline for
check and `run_at` calls. If it fires before the MCP host's own tool-call
timeout or cancellation, the server cancels the underlying `coq-lsp` request
and returns partial diagnostics, progress context, or partial `run_at` output
when available. It defaults to 60 seconds and may be at most 3600 seconds. Set
it slightly lower than the MCP host tool timeout, leaving room for
`ROCQ_LSP_HARD_TIMEOUT_GRACE`, if callers should receive these partial timeout
results.
`command_timeout_seconds` overrides `ROCQ_LSP_COMMAND_TIMEOUT` for one check:
`None` uses the server default of 5 seconds, `0` disables the sentence
watchdog, and positive values enable it for that call.
`long_running_commands` reports commands that actually ran during the check
and took at least 80% of the configured request timeout.
`restart=True` sends `coq/workspace_update` before checking so `coq-lsp`
refreshes imports from recompiled dependencies while keeping the same server
process alive.

`rocq_run_at` takes a 1-based line and character position in `file`.
`commands` is one string containing one or more complete Rocq commands ending in
`.`; punctuation is not added automatically. It can run `Require` as well as
one-off queries such as `Check`, `Search`, `Locate`, or notation experiments.
The tool returns one rendered result for the full commands string.

## Environment

- `ROCQ_LSP_BINARY` defaults to `coq-lsp`
- `ROCQ_LSP_USE_OPAM` defaults to `1`; set to `0` to run the binary directly
- `ROCQ_LSP_TIMEOUT` is the default soft partial-result deadline; it defaults
  to `60`, is capped at `3600`, and should be set slightly lower than the
  overall MCP tool timeout
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` defaults to `5`
- `ROCQ_LSP_COMMAND_TIMEOUT` defaults to `5`; set `0` to disable per-sentence
  command timeouts by default
- `ROCQ_LSP_MESSAGE_LIMIT` defaults to `20`; values above `50` are capped
- `ROCQ_LSP_MAX_ERRORS` defaults to `1000000`
- `ROCQ_LSP_WORKSPACE` optionally pins the workspace root
- `ROCQ_LSP_IDLE_TTL_SECONDS` defaults to `1800`

Idle sessions older than `ROCQ_LSP_IDLE_TTL_SECONDS` are cleaned up by the MCP
server's background reaper and opportunistically during check calls. Sessions
currently checking, or queued for a check, are not stopped.

## Tests

Run the full suite with:

```bash
make test
```

Run narrower suites by directory:

```bash
make unit
make integration
make e2e
```

These targets run tests under `opam exec` and in parallel with `pytest-xdist` by
default. Override the worker count with a Make variable, for example
`make test PYTEST_WORKERS=4`.

Integration and e2e tests skip themselves when `opam` or `coq-lsp` is
unavailable. E2e scenarios with compiled dependencies also skip when `rocq` is
unavailable.
Scenario check and `run_at` steps may include timing assertions for
cache-sensitive behavior; those timings are asserted by the harness but omitted
from promoted transcripts. Scenario `run_at` steps call `rocq_run_at`
through the same cached manager. Scenarios may also define a top-level
`timeout_policy` to override the harness defaults for timeout-specific coverage,
including `command_seconds` for per-sentence timeout scenarios. Individual
check steps may pass `command_timeout_seconds` or the manager-level
`long_running_seconds` threshold to exercise per-call check timeout overrides
and long-running command reporting.
The e2e suite covers error-only check rendering and dense error limits,
`run_at` output info levels for `Search`, `Check`, and `Compute`, `run_at`
caching after slow proofs, soft partial-result timeout recovery, hard-timeout
session reset, per-call check command timeout overrides, per-sentence command
timeout behavior, soft deadlines resuming from cached progress while preserving
diagnostics, cached repeated check errors, long-running command reporting with
cached rechecks ignored, and lemma-header errors that would otherwise cascade
into proof-mode diagnostics.
`tests/e2e/test_check_restart.py` covers refreshing a client file after a
dependency `.vo` is rebuilt.
Use `make promote` to refresh promoted e2e transcripts after intentional output
changes; promotion runs serially because it rewrites golden files.

Use `opam exec -- .venv/bin/python -m pytest ...` for focused test runs outside
of the Makefile. Use `make clean` to remove test artifacts.

## GitLab CI

The GitLab pipeline is defined entirely in `.gitlab-ci.yml`. It runs
`make test` in a matrix over `rocq-core` 9.0.0, 9.1.0, and 9.2.0. Each job uses
the `ocaml/opam:ubuntu-24.04-ocaml-5.2` image, adds the `coq-released` and
`bpeters` opam repositories, installs native apt prerequisites including
`build-essential`, `libgmp-dev`, `m4`, and `pkg-config`, installs the pinned
`rocq-core` version plus unconstrained `rocq-stdlib` and `bens-rocq-lsp`,
creates the repository-local `.venv/`, and installs the Python package with
development test dependencies before running the Makefile target. GitLab caches
pip downloads under `.cache/pip/` per branch and stores opam roots under
`.cache/opam/rocq-${ROCQ_CORE_VERSION}/`, with separate cache keys for each
Rocq version in the matrix. The cached opam root is initialized as a bare root
and uses a `default` switch pinned to `ocaml-base-compiler.5.2.0`; cache reuse
still depends on the GitLab runner having the local cache available.
