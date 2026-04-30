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
  severity: str = "info",
  overall_timeout_seconds: float = 60,
  command_timeout_seconds: float | None = None,
  long_running_seconds: float = 3, restart: bool = False)` and
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
  context, stores `$/coq/filePerfData` payloads by URI/version for
  long-running command reporting, stores `$/coq/executionInformation` by
  URI/version for command timeout watchdogs and executed-range filtering, and
  lets callers wait briefly for diagnostics, performance data, or execution
  changes for a requested document version.
- `src/rocq_lsp_mcp/pipeline.py` normalizes raw diagnostics into compact
  internal `Diagnostic` models. It attaches proof states by matching
  sentence ranges, merges supplemental `proof/goals` results for incomplete
  proof saves, and leaves response filtering and truncation to the render step.
- `src/rocq_lsp_mcp/rocq_client.py` is the manual stdio JSON-RPC client for
  `coq-lsp`. It handles `Content-Length` framing, `initialize`, server
  requests, `coq/getDocument`, `proof/goals`, `petanque/run_at_point`,
  `coq/workspace_update`, soft request deadlines, host cancellation cleanup,
  optional per-command watchdog cancellation, process cleanup, and dispatches
  notifications to `NotificationStore`.
- `src/rocq_lsp_mcp/pretty.py` converts rocq-lsp serialized pretty-print trees
  such as `Pp_glue`, `Pp_box`, and `Pp_print_break` into readable text, then
  normalizes proof-state payloads into compact agent-facing JSON. Goal
  hypotheses are rendered as strings like `H : True` or `n := 0 : nat`.
- `src/rocq_lsp_mcp/models.py` contains the internal Pydantic result models,
  `TimeoutContext`, and `RenderOptions`. Internal models keep normalized raw
  ranges, document metadata, diagnostics, proof states, compile timing,
  long-running command data, and timeout state.
  Their `render(options)` methods are the only place that decides the compact
  MCP output shape: file display, severity labels, location format, proof-state
  inclusion, duplicate proof-error filtering, minimum severity filtering,
  global cascade-error suppression, per-severity message limits, timeout
  formatting, compilation-time formatting, long-running command
  formatting, and empty-field pruning. Default rendering keeps only the first
  error for each `proof_name` and, after the first error without a
  `proof_name`, suppresses later errors that also lack a `proof_name`; warnings
  and infos are preserved. This broad cascade guard can hide later independent
  top-level errors that also lack `proof_name`. Rendering applies the requested
  minimum severity, caps returned diagnostics at 10 per severity bucket with a
  hard cap of 50, and includes compact `timeout_context` only for timeout
  results.
- `src/rocq_lsp_mcp/config.py` loads environment configuration, defines
  `TimeoutPolicy`, builds the `opam exec -- coq-lsp` command, and builds the
  `coq-lsp` initialization settings. `send_execinfo` is enabled so per-call
  command timeouts can be used without restarting sessions, and
  `send_perf_data` is enabled so compile calls can report long-running
  commands.

The prototype intentionally talks to `coq-lsp` directly. Do not reintroduce an
LSP client library unless the user explicitly asks for that direction.
The main source of documentation on `coq-lsp` is the upstream protocol guide:
https://github.com/rocq-community/rocq-lsp/blob/main/etc/doc/PROTOCOL.md

## Runtime Behavior

The compile MCP tool receives a Rocq file path, optional `message_limit`,
optional `severity`, optional `overall_timeout_seconds`, optional
`command_timeout_seconds`, optional `long_running_seconds`, and optional
`restart`; `message_limit` and `severity` are render-only and do not truncate
the internal `CompileResult`. `severity` may be `info`, `warning`, or `error`;
only diagnostics at or above that level are returned. `overall_timeout_seconds`
controls a soft `coq-lsp` partial-result deadline. If it fires before the MCP
host cancels the tool call, the server cancels the underlying `coq-lsp`
request and returns partial diagnostics, progress context, or partial command
output when available. It defaults to 60 seconds and is capped at 3600 seconds.
Host-level MCP timeouts should be greater than
`overall_timeout_seconds + ROCQ_LSP_HARD_TIMEOUT_GRACE` when callers want
partial timeout results back.
For `command_timeout_seconds`, `None` uses `ROCQ_LSP_COMMAND_TIMEOUT`, which
defaults to 5 seconds, `0` disables the command watchdog for that call, and
positive values enable it. `long_running_seconds` defaults to 3 seconds and
controls the threshold for returned `long_running_commands`; `0` disables the
extra report. `restart=True` sends `coq/workspace_update` with empty object
params before the file's `didOpen` or `didChange`, asking the existing
`coq-lsp` process to invalidate cached `Require` state after external `.vo`
files have changed. It does not restart the server process and does not rebuild
dependencies; callers must recompile edited dependencies first. The command MCP
tool receives a Rocq file path, one complete command ending in `.`, a 1-based
line/character position, and optional `overall_timeout_seconds`; this is the
same command tool used for `Search` calls. On each call, the manager resolves
the file, infers a workspace by
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
minimum severity filtering, suppresses later global errors after the first
error without `proof_name`, and applies per-severity diagnostic limits. Counts
are rendered on every compile result and include `errors`, `warnings`, and
`infos`, including zero-valued buckets. When diagnostics are omitted because of
`message_limit`, the response includes `omitted_counts` and the effective
`message_limit`.

Compile results also include `compilation_time_seconds`, rounded to
milliseconds. When enabled, long-running command reporting observes
`$/coq/filePerfData`, filters out entries below `long_running_seconds`, filters
out entries with `info.cache_hit: true`, intersects perf entries with
`$/coq/executionInformation` ranges from the same document version when those
execution ranges are available, and renders at most 10
`long_running_commands`. Each entry contains the source text and a 1-based
location in the same format used by diagnostics, without precise per-command
times so promoted e2e transcripts stay reproducible.

When the soft `coq/getDocument` deadline fires, the manager returns status
`timeout` with an internal `TimeoutContext`.
`TimeoutContext.from_manager_state(...)` builds the timeout state from source
content, the partial document, the latest `$/coq/fileProgress` notification,
timeout settings, and any timed-out command range. Rendering presents
`checked_until` from partial `completed.range.end` when available, otherwise
the start of the latest processing range, plus the operation name,
`timeout_kind`, timeout settings, `latest_processing_range`, and
`next_unchecked` as 1-based human-facing locations.

When command timeout is enabled, the Python client watches
`$/coq/executionInformation` during `coq/getDocument`. Each notification marks
the start of one Rocq sentence and resets the command deadline. If that sentence
exceeds the deadline, the client sends `$/cancelRequest` and the manager returns
`status: "timeout"` with `timeout_kind: "command"`, `command_timeout_seconds`,
the timed-out sentence range, and a source preview. This is per Rocq sentence,
not per internal Ltac substep. The `overall_timeout_seconds` soft deadline
remains the outer server-side partial-result deadline for compile and
command/Search calls. MCP host cancellation is a harder outer abort: the server
sends `$/cancelRequest` to `coq-lsp` and may reset the workspace session, but
the client may already have abandoned the result.

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

Timeout and cancellation handling has three layers:

- `overall_timeout_seconds` is the soft `coq/getDocument` or
  `petanque/run_at_point` partial-result deadline; it defaults to 60 seconds
  and is capped at 3600 seconds. `ROCQ_LSP_TIMEOUT` provides the same bounded
  default.
- `ROCQ_LSP_COMMAND_TIMEOUT` is an optional per-sentence watchdog during
  `coq/getDocument`; unset or non-positive values disable it.
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` is the extra wait after sending
  `$/cancelRequest`, including cleanup after host-level MCP cancellation.

If the hard timeout is hit, the manager kills and drops that workspace session
so the next call starts with a fresh `coq-lsp`. Hard-timeout responses still
include timeout context built from any latest progress notification observed
before the session reset.

## Configuration

The prototype reads these environment variables:

- `ROCQ_LSP_BINARY`, default `coq-lsp`
- `ROCQ_LSP_USE_OPAM`, default `1`; set to `0` to run the binary directly
- `ROCQ_LSP_TIMEOUT`, default soft partial-result deadline `60`, capped at
  `3600`
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

## GitLab CI

The GitLab pipeline lives entirely in `.gitlab-ci.yml`; do not move CI setup
into an auxiliary script unless the user explicitly asks for that. The pipeline
runs `make test` in a matrix over `rocq-core` 9.0.0, 9.1.0, and 9.2.0. Each job
uses `ocaml/opam:ubuntu-24.04-ocaml-5.2`, adds the `coq-released` and `bpeters`
opam repositories, installs the pinned `rocq-core` plus unconstrained
`rocq-stdlib` and `bens-rocq-lsp`, creates `.venv/`, installs `.[dev]`, and then
relies on the existing Makefile so tests run through
`opam exec -- .venv/bin/python`. The CI config rewrites the `bens-rocq-lsp`
package source URL from SSH to HTTPS for Git fetches, because GitLab runners
usually do not have SSH credentials even when the repository is public.

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
  and command steps may include `overall_timeout_seconds` to override the soft
  partial-result deadline, plus `min_seconds` or `max_seconds` timing
  assertions for cache-sensitive behavior; timings are asserted by the harness
  but omitted from promoted transcripts. Scenario
  compile steps may also include `command_timeout_seconds` to cover per-call
  compile timeout overrides, `long_running_seconds` to opt in or out of
  long-running command reporting, or `severity` and `message_limit` to cover
  render-time diagnostic filtering.
  `diagnostic_message_limit` covers default
  per-severity diagnostic truncation. `diagnostic_severity_filter` covers
  real warning diagnostics, proof-script errors, dense counts, and `severity`
  filtering. `command_info_outputs` covers promoted `Search`, `Check`, and
  `Compute` command outputs rendered at info level. `run_command_search` covers
  promoted command output for a narrow `Search` query.
  `run_command_cached_after_slow_proof`
  checks that `Search` after a cached slow proof is quick. `timeout_recovery`
  uses the local `sleep` tactic to cover a soft `coq/getDocument` timeout
  followed by a successful recompile in the same manager.
  `hard_timeout_resets_cache` sets the cancel grace to zero and checks that a
  hard timeout drops the old `coq-lsp` process by requiring a later recompile to
  rerun an unchanged slow proof. `compile_command_timeout_argument` covers the
  compile tool call's per-call `command_timeout_seconds` override.
  `command_timeout` uses the same local `sleep` tactic with a long global
  timeout and short command timeout to cover per-sentence cancellation.
  `request_timeout_resume_and_error_cache` covers repeated soft partial-result
  timeouts resuming from cached progress while preserving an earlier error
  diagnostic, including the final cached successful compile.
  `request_timeout_resume_then_success` covers the two-call case where a soft
  partial-result timeout reports an earlier error, then the next cached compile
  succeeds and reports the same error.
  `repeated_compile_error_cached` calls compile twice on the same file with a
  slow cached prefix and verifies both calls report the same error while the
  second call is quick.
  `long_running_proofs_cached` opts in to long-running command reporting and
  verifies that the first slow `sleep 5.` tactic is reported while the later
  cached recompile is not.
- `tests/e2e/test_compile_restart.py` covers the compile tool's `restart`
  argument by recompiling a dependency externally, showing a stale import
  without `restart`, then refreshing it through `coq/workspace_update`.
  `lemma_header_cascade_error` covers malformed lemma headers that should report
  only the originating lemma diagnostic instead of downstream proof-mode
  cascade errors.

When changing output shape, update exact-output tests rather than weakening
assertions. The MCP output should be useful to agents and humans; raw Rocq
serialization details such as `Pp_glue` should not leak into normalized
proof-state fields.

Keep unrelated running `coq-lsp` processes alone unless the user explicitly asks
to inspect or stop them.
