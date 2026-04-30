# rocq-lsp-mcp-prototype

Prototype MCP server that compiles Rocq files and runs single Rocq commands
through persistent project-scoped `coq-lsp` sessions. Compile diagnostics expose
a human-facing `level`, `location`, message, optional `proof_name`, and compact
proof-state context when available. Command results expose compact feedback
output. Compile results include `compilation_time_seconds`, rounded to
milliseconds, and may include `long_running_commands` entries with source text
and location for commands that actually ran during that compile and exceeded the
requested threshold. Results include the file relative to the workspace. Raw LSP
plumbing such as document URIs, positions, duplicated ranges, versions, empty
lists, and null fields is omitted from the MCP response. Agent-facing compile
results keep only the first error for each `proof_name` and, after the first
error without a
`proof_name`, suppress later errors that also lack a `proof_name`. Warnings and
infos are preserved. This broad cascade guard can hide later independent
top-level errors that also lack `proof_name`. Returned diagnostics are limited
per severity bucket: the default is 10 errors, 10 warnings, and 10 info/hint
callers may request up to the hard cap of 50 per bucket. Callers may also set
`severity` to `info`, `warning`, or `error` to return only diagnostics at or
above that level. Compile results include `counts` for nonzero diagnostic
buckets only; clean results omit `counts`. Timeout results include
a compact `timeout_context` that reports the request operation, timeout
kind and settings, the latest checked position, the latest processing range,
and a small preview of the next unchecked source line when available. Optional
soft coq-lsp partial-result deadlines can be passed per compile or
command/Search call; the default is 60 seconds and the maximum is 3600 seconds.
If a host-level MCP timeout or cancellation fires first, the server cleans up
`coq-lsp` but cannot guarantee a normal tool result, so host timeouts should be
greater than `overall_timeout_seconds + ROCQ_LSP_HARD_TIMEOUT_GRACE` when
callers want partial diagnostics back. Optional per-command timeouts use
coq-lsp sentence execution notifications, so the timeout unit is one Rocq
sentence such as `sleep 5.` or `repeat ... .`, not an internal Ltac substep. The
prototype talks to `coq-lsp` directly over stdio JSON-RPC.

Compile calls can pass `restart=True` to tell `coq-lsp` that externally
compiled dependencies may have changed before checking the requested file.

## Architecture

- `server.py` registers the MCP tools, manages one `RocqLspManager`
  for the server lifespan, and renders internal results for MCP output.
- `manager.py` resolves files and workspaces, keeps the session registry,
  evicts idle sessions, and orchestrates internal compile and command results.
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
  severity filtering, message limits, location formatting, timeout formatting,
  long-running command formatting, compile-time formatting, and empty-field
  pruning.

For `Attempt to save an incomplete proof` diagnostics, the `Qed.` span may only
contain the save error. The manager asks `proof/goals` with `mode: "Prev"` at
that diagnostic position and merges the returned compact goals and bullet state
into the diagnostic proof state.

Command execution uses `petanque/run_at_point`, because `proof/goals` with the
`command` parameter does not return feedback from commands such as `Search` or
`Print` in current `coq-lsp`. Call the command tool right after imports when
possible, or between lemmas/definitions when local declarations, scopes, or
modules are needed. The command is ephemeral and does not edit the source file.

The manager infers a workspace by walking upward from the requested file and
using the first directory with `_RocqProject` or `_CoqProject`. If no project
file is found, the file's parent directory is used. Each workspace gets its own
cached `coq-lsp` process, so different projects can compile concurrently while
files inside one project reuse the same `coq-lsp` cache. By default, each
process starts as `opam exec -- coq-lsp` with `cwd` set to the inferred
workspace so local opam switches select the matching Rocq and `coq-lsp`
versions.

If a compile call has `restart=True`, the manager sends `coq/workspace_update`
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
source preview. The `overall_timeout_seconds` soft deadline remains the outer
server-side partial-result deadline for parsing stalls, missing execution
notifications, or non-interruptible work. MCP host cancellation is a harder
outer abort: the server sends `$/cancelRequest` to `coq-lsp` and may reset the
workspace session, but the client may already have abandoned the result.

`coq-lsp` performance data is enabled with `send_perf_data`. After successful
`coq/getDocument` calls, the manager waits briefly for `$/coq/filePerfData` and
uses sentence execution notifications from the same document version to keep
only commands that ran during the current compile step. Entries with
`info.cache_hit: true` are ignored, entries below `long_running_seconds` are
ignored, and the rendered result is capped at 10 long-running commands. Set
`long_running_seconds=0` to disable this extra reporting for a call.

## Run

```bash
uv run rocq-lsp-mcp
```

The server exposes two MCP tools:

- `rocq_lsp_compile_file(file: str, message_limit: int = 10,
  severity: str = "info",
  overall_timeout_seconds: float = 60,
  command_timeout_seconds: float | None = None,
  long_running_seconds: float = 3,
  restart: bool = False)`
- `rocq_lsp_run_command(file: str, command: str, line: int, character: int = 1,
  overall_timeout_seconds: float = 60)`

`message_limit` caps returned diagnostics per severity bucket. Values above 50
are capped to 50. When diagnostics are omitted, the response includes
`omitted_counts` and `message_limit` alongside total `counts`. The limit is
applied only while rendering the MCP response; internal compile results retain
all normalized diagnostics. `counts` includes only nonzero `errors`, `warnings`,
and `infos` buckets and is omitted when no diagnostics remain after filtering.
`severity` is a render-only minimum diagnostic level. Allowed values are
`info`, `warning`, and `error`; `info` preserves the default behavior,
`warning` returns warnings and errors, and `error` returns errors only.
`overall_timeout_seconds` controls the soft `coq-lsp` partial-result deadline
for compile and command/Search calls. If it fires before the MCP host's own
tool-call timeout or cancellation, the server cancels the underlying
`coq-lsp` request and returns partial diagnostics, progress context, or partial
command output when available. It defaults to 60 seconds and may be at most
3600 seconds. Configure MCP host timeouts above
`overall_timeout_seconds + ROCQ_LSP_HARD_TIMEOUT_GRACE` if callers should
receive these partial timeout results.
`command_timeout_seconds` overrides `ROCQ_LSP_COMMAND_TIMEOUT` for one compile:
`None` uses the server default of 5 seconds, `0` disables the sentence
watchdog, and positive values enable it for that call.
`long_running_seconds` controls `long_running_commands` reporting for one
compile: the default is 3 seconds, and `0` disables the report.
`restart=True` sends `coq/workspace_update` before compiling so `coq-lsp`
refreshes imports from recompiled dependencies while keeping the same server
process alive.

`rocq_lsp_run_command` takes a 1-based line and character position in `file`.
The command must be one complete Rocq command ending in `.`; punctuation is not
added automatically.

## Environment

- `ROCQ_LSP_BINARY` defaults to `coq-lsp`
- `ROCQ_LSP_USE_OPAM` defaults to `1`; set to `0` to run the binary directly
- `ROCQ_LSP_TIMEOUT` is the default soft partial-result deadline; it defaults
  to `60` and is capped at `3600`
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` defaults to `5`
- `ROCQ_LSP_COMMAND_TIMEOUT` defaults to `5`; set `0` to disable per-sentence
  command timeouts by default
- `ROCQ_LSP_MAX_ERRORS` defaults to `1000000`
- `ROCQ_LSP_WORKSPACE` optionally pins the workspace root
- `ROCQ_LSP_IDLE_TTL_SECONDS` defaults to `1800`

Idle sessions older than `ROCQ_LSP_IDLE_TTL_SECONDS` are cleaned up by the MCP
server's background reaper and opportunistically during compile calls. Sessions
currently compiling, or queued for a compile, are not stopped.

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
Scenario compile and command steps may include timing assertions for
cache-sensitive behavior; those timings are asserted by the harness but omitted
from promoted transcripts. Scenario command steps call `rocq_lsp_run_command`
through the same cached manager. Scenarios may also define a top-level
`timeout_policy` to override the harness defaults for timeout-specific coverage,
including `command_seconds` for per-sentence timeout scenarios. Individual
compile and command steps may pass `overall_timeout_seconds` to override the
soft partial-result deadline for that step. Scenario compile steps may set
`command_timeout_seconds` to exercise per-call compile timeout overrides,
`long_running_seconds` to opt in or out of long-running command reporting, or
`severity` and `message_limit` to exercise render-time diagnostic filtering.
The e2e suite covers diagnostic severity filtering and dense counts, command
output info levels for `Search`, `Check`, and `Compute`, command caching after
slow proofs, soft partial-result timeout recovery, hard-timeout session reset,
per-call compile command timeout overrides, per-sentence command timeout
behavior, soft deadlines resuming from cached progress while preserving
diagnostics, cached repeated compile errors, long-running command reporting
with cached recompiles ignored, and lemma-header errors that would otherwise
cascade into proof-mode diagnostics.
`tests/e2e/test_compile_restart.py` covers refreshing a client file after a
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
development test dependencies before running the Makefile target.
