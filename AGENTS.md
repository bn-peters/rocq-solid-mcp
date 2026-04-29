# rocq-lsp-mcp-prototype Guide

It is the duty of the agent to keep AGENTS.md and README.md up to date.

This package is a Python MCP prototype for compiling Rocq files through
persistent project-scoped `coq-lsp` sessions. It returns compact structured
diagnostics and, when available, proof-state context for the sentence that
produced an error.

## Package Layout

- `src/rocq_lsp_mcp/server.py` defines the FastMCP server, the lifespan-managed
  `RocqLspManager`, and the single MCP tool
  `rocq_lsp_compile_file(file: str, message_limit: int = 10)`.
- `src/rocq_lsp_mcp/manager.py` is the orchestration layer. It resolves files,
  infers workspaces from `_RocqProject` or `_CoqProject`, keeps cached
  sessions per workspace, evicts idle sessions, reads files, routes compile
  requests, and converts outcomes into `CompileResult`.
- `src/rocq_lsp_mcp/session.py` owns the lifecycle for one workspace-backed
  `coq-lsp` session. It starts and stops the client, keeps the session lock,
  tracks document versions, and owns the session `NotificationStore`.
- `src/rocq_lsp_mcp/notifications.py` stores LSP notification state. It handles
  `textDocument/publishDiagnostics`, tracks latest diagnostic versions by URI,
  stores latest `$/coq/fileProgress` payloads by URI/version for timeout
  context, and lets callers wait briefly for diagnostics for a requested
  document version.
- `src/rocq_lsp_mcp/pipeline.py` normalizes raw diagnostics into compact
  agent-facing `Diagnostic` models. It attaches proof states by matching
  sentence ranges, merges supplemental `proof/goals` results for incomplete
  proof saves, and keeps only the first error for each `proof_name`, while
  preserving warnings, infos, and errors without a proof name.
- `src/rocq_lsp_mcp/rocq_client.py` is the manual stdio JSON-RPC client for
  `coq-lsp`. It handles `Content-Length` framing, `initialize`, server
  requests, `coq/getDocument`, `proof/goals`, request timeouts, cancellation,
  process cleanup, and dispatches notifications to `NotificationStore`.
- `src/rocq_lsp_mcp/pretty.py` converts rocq-lsp serialized pretty-print trees
  such as `Pp_glue`, `Pp_box`, and `Pp_print_break` into readable text, then
  normalizes proof-state payloads into compact agent-facing JSON. Goal
  hypotheses are rendered as strings like `H : True` or `n := 0 : nat`.
- `src/rocq_lsp_mcp/models.py` contains the Pydantic response models.
  `CompileResult` separates diagnostics into errors, warnings, and infos.
  `Diagnostic` keeps raw LSP details internally while serializing only compact
  fields such as `level`, `location`, `message`, optional `proof_name`, and
  optional `proof_state`. `CompileResult` caps returned diagnostics per severity
  bucket, with a default of 10 and a hard cap of 50 requested messages per
  bucket. Timeout compile results may include compact `timeout_context` with
  the checked position and next unchecked source preview. Serialized compile
  results include the compiled file relative to the workspace and omit
  version/workspace internals.
- `src/rocq_lsp_mcp/config.py` loads environment configuration, defines
  `TimeoutPolicy`, builds the `opam exec -- coq-lsp` command, and builds the
  `coq-lsp` initialization settings.

The prototype intentionally talks to `coq-lsp` directly. Do not reintroduce an
LSP client library unless the user explicitly asks for that direction.

## Runtime Behavior

The MCP tool receives a Rocq file path and optional `message_limit`. On each
call, the manager resolves the file, infers a workspace by walking upward to the
nearest `_RocqProject` or `_CoqProject`, then delegates to the cached
`RocqLspSession` for that workspace. If no project file is found, the file's
parent directory is used. The session sends either `textDocument/didOpen` or
`textDocument/didChange` through its client. The document version increments on
every call.

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
for the MCP response, replacing raw LSP ranges with a single 1-based location
string, replacing numeric LSP severities with `error`, `warning`, or `info`,
and omitting null or empty fields. If multiple errors belong to the same
`proof_name`, only the first is forwarded to the agent. Counts are serialized
only when warnings or omitted diagnostics are present, and zero-valued count
fields are omitted. When diagnostics are omitted because of `message_limit`, the
response includes `omitted_counts` and the effective `message_limit`.

When `coq/getDocument` times out, the manager returns status `timeout` with a
compact `timeout_context`. `checked_until` comes from partial
`completed.range.end` when cancellation returns a partial document; otherwise it
falls back to the start of the latest `$/coq/fileProgress` processing range.
The context also includes the operation name, timeout settings,
`latest_processing_range`, and `next_unchecked` as 1-based human-facing
locations. Raw file-progress payloads are kept internal.

For `Attempt to save an incomplete proof` diagnostics, the `Qed.` sentence can
carry only the save error while the open goal is attached to the previous proof
span. The manager issues a best-effort `proof/goals` request with `mode="Prev"`
at the diagnostic position and `pipeline.py` merges the returned compact goals
and bullet state into the existing proof-state payload.

Caching is delegated to the long-lived `coq-lsp` process. The Python side keeps
the session, notification, and version state alive so repeated calls for changed
files can use rocq-lsp's incremental checking behavior. Idle sessions are
cleaned up by the MCP server's background reaper and opportunistically during
compile calls after `ROCQ_LSP_IDLE_TTL_SECONDS`; sessions that are compiling or
queued for a compile are not stopped.

Timeout handling has two layers:

- `ROCQ_LSP_TIMEOUT` is the normal `coq/getDocument` wait.
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
- `ROCQ_LSP_TIMEOUT`, default `60`
- `ROCQ_LSP_HARD_TIMEOUT_GRACE`, default `5`
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
  workspace inference, per-workspace session separation, idle cleanup, language
  IDs, timeout context construction, and hard-timeout reset logic.
- `tests/unit/test_config.py` covers command construction and environment
  configuration.
- `tests/unit/test_notifications.py` covers diagnostic notification storage,
  file-progress storage for timeout context, clearing, waiting, fallback
  behavior, and ignored notifications.
- `tests/unit/test_pipeline.py` covers proof-state attachment and agent-facing
  diagnostic filtering.
- `tests/unit/test_pretty.py` covers readable rendering of rocq-lsp Pp structures
  and proof-state normalization.
- `tests/integration/test_integration.py` talks to a real `coq-lsp`. It checks clean files,
  multiple errors, and file-change behavior where a failing proof
  `Goal 0 = 0. Proof. fail. Qed.` reports proof state, then recompiles cleanly
  after changing `fail` to `reflexivity`.
- `tests/e2e/test_scenarios.py` runs promoted scenario fixtures that copy whole
  Rocq projects, compile files through one cached manager, edit files, and check
  concise agent-facing transcripts. Scenario files may define a top-level
  `timeout_policy` for timeout-specific coverage. Scenario compile steps may
  include `min_seconds` or `max_seconds` timing assertions for cache-sensitive
  behavior; timings are asserted by the harness but omitted from promoted
  transcripts. `diagnostic_message_limit` covers default per-severity diagnostic
  truncation. `timeout_recovery` uses the local `sleep` tactic to cover a soft
  `coq/getDocument` timeout followed by a successful recompile in the same
  manager. `hard_timeout_resets_cache` sets the cancel grace to zero and checks
  that a hard timeout drops the old `coq-lsp` process by requiring a later
  recompile to rerun an unchanged slow proof.

When changing output shape, update exact-output tests rather than weakening
assertions. The MCP output should be useful to agents and humans; raw Rocq
serialization details such as `Pp_glue` should not leak into normalized
proof-state fields.

Keep unrelated running `coq-lsp` processes alone unless the user explicitly asks
to inspect or stop them.
