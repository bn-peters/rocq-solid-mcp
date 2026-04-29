# rocq-lsp-mcp-prototype

Prototype MCP server that compiles Rocq files and runs single Rocq commands
through persistent project-scoped `coq-lsp` sessions. Compile diagnostics expose
a human-facing `level`, `location`, message, optional `proof_name`, and compact
proof-state context when available. Command results expose compact feedback
output. Results include the file relative to the workspace. Raw LSP plumbing
such as document URIs, positions, duplicated ranges, versions, empty lists, and
null fields is omitted from the MCP response. Agent-facing compile results keep
only the first error for each `proof_name`, while preserving warnings, infos,
and errors without a proof name. Returned diagnostics are limited per severity
bucket: the default is 10 errors, 10 warnings, and 10 info/hint messages, and
callers may request up to the hard cap of 50 per bucket. Timeout results
include a compact `timeout_context` that reports the request operation, timeout
kind and settings, the latest checked position, the latest processing range,
and a small preview of the next unchecked source line when available. Optional
per-command timeouts use coq-lsp sentence execution notifications, so the
timeout unit is one Rocq sentence such as `sleep 5.` or `repeat ... .`, not an
internal Ltac substep. The prototype talks to `coq-lsp` directly over stdio
JSON-RPC.

## Architecture

- `server.py` registers the MCP tools and manages one `RocqLspManager`
  for the server lifespan.
- `manager.py` resolves files and workspaces, keeps the session registry,
  evicts idle sessions, and orchestrates compile and command results.
- `session.py` owns each workspace session lifecycle, document versions, and
  notification store.
- `rocq_client.py` is transport-only JSON-RPC framing, requests, notifications,
  cancellation, `coq/getDocument`, `proof/goals`, `petanque/run_at_point`, and
  process cleanup.
- `notifications.py` stores diagnostics, latest file-progress notifications,
  and sentence execution notifications published by `coq-lsp`.
- `pipeline.py` converts raw diagnostics plus proof-state data into compact
  agent-facing `Diagnostic` models.

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

When `coq/getDocument` times out, the response status is `timeout` and the
serialized result includes `timeout_context`. `checked_until` is taken from the
partial `completed.range.end` when cancellation returns a partial document; if
not, it falls back to the start of the latest `$/coq/fileProgress` processing
range. The context also includes `latest_processing_range` and `next_unchecked`
as compact 1-based locations without exposing raw LSP progress payloads.

When command timeout is enabled, the client watches `$/coq/executionInformation`
notifications during `coq/getDocument`. Each new sentence resets the command
deadline. If the current sentence exceeds the configured limit, the client sends
`$/cancelRequest` and returns `status: "timeout"` with
`timeout_context.timeout_kind: "command"`, the timed-out sentence range, and a
source preview. The existing global request timeout remains the outer safety
net for parsing stalls, missing execution notifications, or non-interruptible
work.

## Run

```bash
uv run rocq-lsp-mcp
```

The server exposes two MCP tools:

- `rocq_lsp_compile_file(file: str, message_limit: int = 10,
  command_timeout_seconds: float | None = None)`
- `rocq_lsp_run_command(file: str, command: str, line: int, character: int = 1)`

`message_limit` caps returned diagnostics per severity bucket. Values above 50
are capped to 50. When diagnostics are omitted, the response includes
`omitted_counts` and `message_limit` alongside total `counts`.
`command_timeout_seconds` overrides `ROCQ_LSP_COMMAND_TIMEOUT` for one compile:
`None` uses the server default, `0` disables the sentence watchdog, and positive
values enable it for that call.

`rocq_lsp_run_command` takes a 1-based line and character position in `file`.
The command must be one complete Rocq command ending in `.`; punctuation is not
added automatically.

## Environment

- `ROCQ_LSP_BINARY` defaults to `coq-lsp`
- `ROCQ_LSP_USE_OPAM` defaults to `1`; set to `0` to run the binary directly
- `ROCQ_LSP_TIMEOUT` defaults to `60`
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` defaults to `5`
- `ROCQ_LSP_COMMAND_TIMEOUT` is unset by default; set a positive number of
  seconds to enable per-sentence command timeouts
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
Scenario compile steps may include timing assertions for cache-sensitive
behavior; those timings are asserted by the harness but omitted from promoted
transcripts. Scenarios may also define a top-level `timeout_policy` to override
the harness defaults for timeout-specific coverage, including
`command_seconds` for per-sentence timeout scenarios. Scenario command steps
call `rocq_lsp_run_command` through the same cached manager. The e2e suite
covers command output, soft timeout recovery, hard-timeout session reset, and
per-sentence command timeout behavior.
Use `make promote` to refresh promoted e2e transcripts after intentional output
changes; promotion runs serially because it rewrites golden files.

Use `opam exec -- .venv/bin/python -m pytest ...` for focused test runs outside
of the Makefile. Use `make clean` to remove test artifacts.
