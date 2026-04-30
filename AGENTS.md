# rocq-lsp-mcp-prototype Guide

It is the duty of the agent to keep AGENTS.md and README.md up to date.

This package is a Python MCP prototype for compiling Rocq files and running
single Rocq commands through persistent project-scoped `coq-lsp` sessions.
Compilation returns compact structured diagnostics and, when available,
proof-state context for the sentence that produced an error. Command execution
returns compact feedback output for the requested command.

## Package Layout

- `src/rocq_lsp_mcp/server.py` defines the FastMCP server, the lifespan-managed
  `RocqLspManager`, and the MCP tools
  `rocq_lsp_compile_file(file: str, message_limit: int = 10,
  overall_timeout_seconds: float = 60,
  command_timeout_seconds: float | None = None)` and
  `rocq_lsp_run_command(file: str, command: str, line: int, character: int = 1,
  overall_timeout_seconds: float = 60)`.
  Tool handlers render internal result models before returning MCP output.
- `src/rocq_lsp_mcp/manager.py` is the orchestration layer. It resolves files,
  infers workspaces from `_RocqProject` or `_CoqProject`, keeps cached
  sessions per workspace, evicts idle sessions, reads files, routes compile and
  command requests, and converts outcomes into `CompileResult` or
  `CommandResult`.
- `src/rocq_lsp_mcp/session.py` owns the lifecycle for one workspace-backed
  `coq-lsp` session. It starts and stops the client, keeps the session lock,
  tracks document versions, and owns the session `NotificationStore`.
- `src/rocq_lsp_mcp/notifications.py` stores LSP notification state. It handles
  `textDocument/publishDiagnostics`, tracks latest diagnostic versions by URI,
  stores latest `$/coq/fileProgress` payloads by URI/version for timeout
  context, stores `$/coq/executionInformation` by URI/version for command
  timeout watchdogs, and lets callers wait briefly for diagnostics or execution
  changes for a requested document version.
- `src/rocq_lsp_mcp/pipeline.py` normalizes raw diagnostics into compact
  internal `Diagnostic` models. It attaches proof states by matching
  sentence ranges, merges supplemental `proof/goals` results for incomplete
  proof saves, and leaves response filtering and truncation to the render step.
- `src/rocq_lsp_mcp/rocq_client.py` is the manual stdio JSON-RPC client for
  `coq-lsp`. It handles `Content-Length` framing, `initialize`, server
  requests, `coq/getDocument`, `proof/goals`, `petanque/run_at_point`, request
  timeouts, cancellation, optional per-command watchdog cancellation, process
  cleanup, and dispatches notifications to `NotificationStore`.
- `src/rocq_lsp_mcp/pretty.py` converts rocq-lsp serialized pretty-print trees
  such as `Pp_glue`, `Pp_box`, and `Pp_print_break` into readable text, then
  normalizes proof-state payloads into compact agent-facing JSON. Goal
  hypotheses are rendered as strings like `H : True` or `n := 0 : nat`.
- `src/rocq_lsp_mcp/models.py` contains the internal Pydantic result models,
  `TimeoutContext`, and `RenderOptions`. Internal models keep normalized raw
  ranges, document metadata, diagnostics, proof states, and timeout state.
  Their `render(options)` methods are the only place that decides the compact
  MCP output shape: file display, severity labels, location format, proof-state
  inclusion, duplicate proof-error filtering, per-severity message limits,
  timeout formatting, and empty-field pruning. Default rendering keeps only the
  first error for each `proof_name`, preserves warnings, infos, and global
  errors, caps returned diagnostics at 10 per severity bucket with a hard cap
  of 50, and includes compact `timeout_context` only for timeout results.
- `src/rocq_lsp_mcp/config.py` loads environment configuration, defines
  `TimeoutPolicy`, builds the `opam exec -- coq-lsp` command, and builds the
  `coq-lsp` initialization settings. `send_execinfo` is enabled so per-call
  command timeouts can be used without restarting sessions.

The prototype intentionally talks to `coq-lsp` directly. Do not reintroduce an
LSP client library unless the user explicitly asks for that direction.
The main source of documentation on `coq-lsp` is the upstream protocol guide:
https://github.com/rocq-community/rocq-lsp/blob/main/etc/doc/PROTOCOL.md

## Runtime Behavior

The compile MCP tool receives a Rocq file path, optional `message_limit`,
optional `overall_timeout_seconds`, and optional `command_timeout_seconds`;
`message_limit` is render-only and does not truncate the internal
`CompileResult`. `overall_timeout_seconds` controls the outer `coq-lsp` request
wait, defaults to 60 seconds, and is capped at 3600 seconds. For
`command_timeout_seconds`, `None` uses `ROCQ_LSP_COMMAND_TIMEOUT`, which
defaults to 5 seconds, `0` disables the command watchdog for that call, and
positive values enable it. The command MCP tool receives a Rocq file path, one
complete command ending in `.`, a 1-based line/character position, and optional
`overall_timeout_seconds`; this is the same command tool used for `Search`
calls. On each call, the manager resolves the file, infers a workspace by
walking upward to the nearest `_RocqProject` or
`_CoqProject`, then delegates to the cached `RocqLspSession` for that
workspace. If no project file is found, the file's parent directory is used.
The session sends either `textDocument/didOpen` or `textDocument/didChange`
through its client. The document version increments on every call.

Each inferred workspace gets a separate long-lived `coq-lsp` process. The
process starts with `cwd` set to that workspace and, by default, with
`opam exec -- coq-lsp` so local opam switches select the appropriate Rocq and
`coq-lsp` versions. Calls for different workspaces may use different
`coq-lsp` processes concurrently. Calls inside the same workspace share one
session and serialize on that session's lock.

Compilation is driven by `coq/getDocument` with `goals="Str"`. This asks
`coq-lsp` for the checked document and proof-state data around spans. The
`NotificationStore` collects diagnostics from
`textDocument/publishDiagnostics`. `pipeline.py` normalizes those diagnostics
and attaches proof states without applying agent-facing filtering. The
`CompileResult.render(RenderOptions(...))` step converts raw LSP ranges to
1-based locations, severity numbers to `error`, `warning`, or `info`, removes
empty fields, filters duplicate proof errors by default, and applies
per-severity diagnostic limits. Counts are rendered only when warnings or
omitted diagnostics are present, and zero-valued count fields are omitted. When
diagnostics are omitted because of `message_limit`, the response includes
`omitted_counts` and the effective `message_limit`.

When `coq/getDocument` times out, the manager returns status `timeout` with an
internal `TimeoutContext`. `TimeoutContext.from_manager_state(...)` builds the
timeout state from source content, the partial document, the latest
`$/coq/fileProgress` notification, timeout settings, and any timed-out command
range. Rendering presents `checked_until` from partial `completed.range.end`
when available, otherwise the start of the latest processing range, plus the
operation name, `timeout_kind`, timeout settings, `latest_processing_range`,
and `next_unchecked` as 1-based human-facing locations.

When command timeout is enabled, the Python client watches
`$/coq/executionInformation` during `coq/getDocument`. Each notification marks
the start of one Rocq sentence and resets the command deadline. If that sentence
exceeds the deadline, the client sends `$/cancelRequest` and the manager returns
`status: "timeout"` with `timeout_kind: "command"`, `command_timeout_seconds`,
the timed-out sentence range, and a source preview. This is per Rocq sentence,
not per internal Ltac substep. The `overall_timeout_seconds` request timeout
remains the outer limit for compile and command/Search calls.

For `Attempt to save an incomplete proof` diagnostics, the `Qed.` sentence can
carry only the save error while the open goal is attached to the previous proof
span. The manager issues a best-effort `proof/goals` request with `mode="Prev"`
at the diagnostic position and `pipeline.py` merges the returned compact goals
and bullet state into the existing proof-state payload.

Command execution is driven by `petanque/run_at_point`, because
`proof/goals` with the `command` parameter does not surface feedback from
commands such as `Search` or `Print` in current `coq-lsp`. The command tool
should be called right after imports when possible, or between
lemmas/definitions when local declarations, scopes, or modules are required.
The command is ephemeral and does not edit the source file.

Caching is delegated to the long-lived `coq-lsp` process. The Python side keeps
the session, notification, and version state alive so repeated calls for changed
files can use rocq-lsp's incremental checking behavior. Idle sessions are
cleaned up by the MCP server's background reaper and opportunistically during
compile calls after `ROCQ_LSP_IDLE_TTL_SECONDS`; sessions that are compiling or
queued for a compile are not stopped.

Timeout handling has three layers:

- `overall_timeout_seconds` is the normal `coq/getDocument` or
  `petanque/run_at_point` wait for MCP calls; it defaults to 60 seconds and is
  capped at 3600 seconds. `ROCQ_LSP_TIMEOUT` provides the same bounded default
  for direct manager usage.
- `ROCQ_LSP_COMMAND_TIMEOUT` is an optional per-sentence watchdog during
  `coq/getDocument`; unset or non-positive values disable it.
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` is the extra wait after sending
  `$/cancelRequest`.

If the hard timeout is hit, the manager kills and drops that workspace session
so the next call starts with a fresh `coq-lsp`. Hard-timeout responses still
include timeout context built from any latest progress notification observed
before the session reset.

## Configuration

The prototype reads these environment variables:

- `ROCQ_LSP_BINARY`, default `coq-lsp`
- `ROCQ_LSP_USE_OPAM`, default `1`; set to `0` to run the binary directly
- `ROCQ_LSP_TIMEOUT`, default `60`, capped at `3600`
- `ROCQ_LSP_HARD_TIMEOUT_GRACE`, default `5`
- `ROCQ_LSP_COMMAND_TIMEOUT`, default `5`; set `0` to disable per-sentence
  command timeout by default
- `ROCQ_LSP_MAX_ERRORS`, default `1000000`
- `ROCQ_LSP_WORKSPACE`, optional workspace override
- `ROCQ_LSP_IDLE_TTL_SECONDS`, default `1800`

The prototype is intentionally not security-hardened. Do not add path sandboxing
or command restrictions unless the task specifically asks for it.

## Development Commands

Run commands from this directory.

```bash
make test
make unit
make integration
make e2e
make promote
make clean
```

`make test` runs the whole pytest suite under `opam exec` and in parallel via
`pytest-xdist`. Select narrower suites by directory via `make unit`,
`make integration`, or `make e2e`; do not use environment variables to select
test classes. Override parallelism with a Make variable such as
`make test PYTEST_WORKERS=4`. Tests that need a real `coq-lsp` skip themselves
if `opam` or `coq-lsp` is unavailable. E2e scenarios with compiled dependencies
also skip themselves if `rocq` is unavailable. `make promote` refreshes promoted
e2e transcripts and runs serially because it rewrites golden files. `make clean`
removes pytest caches, Python bytecode caches, and egg-info directories while
preserving `.venv/`.

The local virtual environment is expected at `.venv/`. Use
`opam exec -- .venv/bin/python -m pytest ...` for focused test runs.

## Test Suite

- `tests/unit/test_manager.py` uses fakes to cover manager behavior, versioning,
  workspace inference, per-workspace session separation, command execution,
  idle cleanup, language IDs, timeout context construction, and hard-timeout
  reset logic.
- `tests/unit/test_config.py` covers command construction and environment
  configuration.
- `tests/unit/test_notifications.py` covers diagnostic notification storage,
  file-progress storage for timeout context, execution-info storage for command
  timeout, clearing, waiting, fallback behavior, and ignored notifications.
- `tests/unit/test_pipeline.py` covers proof-state attachment and agent-facing
  diagnostic filtering.
- `tests/unit/test_pretty.py` covers readable rendering of rocq-lsp Pp structures
  and proof-state normalization.
- `tests/integration/test_integration.py` talks to a real `coq-lsp`. It checks
  clean files, multiple errors, command feedback and command failures, and
  file-change behavior where a failing proof `Goal 0 = 0. Proof. fail. Qed.`
  reports proof state, then recompiles cleanly after changing `fail` to
  `reflexivity`.
- `tests/e2e/test_scenarios.py` runs promoted scenario fixtures that copy whole
  Rocq projects, compile files or run commands through one cached manager, edit
  files, and check concise agent-facing transcripts. Scenario files may define a
  top-level `timeout_policy` for timeout-specific coverage. Scenario compile
  and command steps may include `overall_timeout_seconds`, plus `min_seconds` or
  `max_seconds` timing assertions for cache-sensitive behavior; timings are
  asserted by the harness but omitted from promoted transcripts. Scenario
  compile steps may also include `command_timeout_seconds` to cover per-call
  compile timeout overrides.
  `diagnostic_message_limit` covers default
  per-severity diagnostic truncation. `run_command_search` covers promoted
  command output for a narrow `Search` query. `run_command_cached_after_slow_proof`
  checks that `Search` after a cached slow proof is quick. `timeout_recovery`
  uses the local `sleep` tactic to cover a soft `coq/getDocument` timeout
  followed by a successful recompile in the same manager.
  `hard_timeout_resets_cache` sets the cancel grace to zero and checks that a
  hard timeout drops the old `coq-lsp` process by requiring a later recompile to
  rerun an unchanged slow proof. `compile_command_timeout_argument` covers the
  compile tool call's per-call `command_timeout_seconds` override.
  `command_timeout` uses the same local `sleep` tactic with a long global
  timeout and short command timeout to cover per-sentence cancellation.
  `request_timeout_resume_and_error_cache` covers repeated soft request
  timeouts resuming from cached progress while preserving an earlier error
  diagnostic, including the final cached successful compile.
  `request_timeout_resume_then_success` covers the two-call case where a soft
  request timeout reports an earlier error, then the next cached compile
  succeeds and reports the same error.
  `repeated_compile_error_cached` calls compile twice on the same file with a
  slow cached prefix and verifies both calls report the same error while the
  second call is quick.

When changing output shape, update exact-output tests rather than weakening
assertions. The MCP output should be useful to agents and humans; raw Rocq
serialization details such as `Pp_glue` should not leak into normalized
proof-state fields.

Keep unrelated running `coq-lsp` processes alone unless the user explicitly asks
to inspect or stop them.
