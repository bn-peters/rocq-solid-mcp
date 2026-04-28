# rocq-lsp-mcp-prototype Guide

This package is a Python MCP prototype for compiling Rocq files through a
persistent `coq-lsp` session. It returns structured diagnostics and, when
available, proof-state context for the sentence that produced an error.

## Package Layout

- `src/rocq_lsp_mcp/server.py` defines the FastMCP server, the lifespan-managed
  `RocqLspManager`, and the single MCP tool
  `rocq_lsp_compile_file(file: str)`.
- `src/rocq_lsp_mcp/manager.py` is the orchestration layer. It resolves files,
  infers workspaces from `_RocqProject` or `_CoqProject`, keeps cached
  `coq-lsp` sessions per workspace, tracks document versions, sends open/change
  notifications, calls `coq/getDocument`, collects diagnostics, and attaches
  proof states by matching sentence ranges.
- `src/rocq_lsp_mcp/rocq_client.py` is the manual stdio JSON-RPC client for
  `coq-lsp`. It handles `Content-Length` framing, `initialize`, server
  requests, diagnostics notifications, request timeouts, cancellation, and
  process cleanup.
- `src/rocq_lsp_mcp/pretty.py` converts rocq-lsp serialized pretty-print trees
  such as `Pp_glue`, `Pp_box`, and `Pp_print_break` into readable text, then
  normalizes proof-state payloads into agent-facing JSON.
- `src/rocq_lsp_mcp/models.py` contains the Pydantic response models.
  `CompileResult` separates diagnostics into errors, warnings, and infos.
  `Diagnostic` keeps the raw LSP diagnostic while exposing normalized fields and
  optional `proof_state`.
- `src/rocq_lsp_mcp/config.py` loads environment configuration and builds the
  `coq-lsp` initialization settings.

The prototype intentionally talks to `coq-lsp` directly. Do not reintroduce an
LSP client library unless the user explicitly asks for that direction.

## Runtime Behavior

The MCP tool receives only a Rocq file path. On each call, the manager reads the
file from disk and sends either `textDocument/didOpen` or
`textDocument/didChange` to the cached `coq-lsp` session for that workspace.
The document version increments on every call.

Compilation is driven by `coq/getDocument` with `goals="Str"`. This asks
`coq-lsp` for the checked document and proof-state data around spans. The
diagnostics from `textDocument/publishDiagnostics` are returned in the MCP
response.

Caching is delegated to the long-lived `coq-lsp` process. The Python side keeps
the session and version state alive so repeated calls for changed files can use
rocq-lsp's incremental checking behavior.

Timeout handling has two layers:

- `ROCQ_LSP_TIMEOUT` is the normal `coq/getDocument` wait.
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` is the extra wait after sending
  `$/cancelRequest`.

If the hard timeout is hit, the manager kills and drops that workspace session
so the next call starts with a fresh `coq-lsp`.

## Configuration

The prototype reads these environment variables:

- `ROCQ_LSP_BINARY`, default `coq-lsp`
- `ROCQ_LSP_TIMEOUT`, default `60`
- `ROCQ_LSP_HARD_TIMEOUT_GRACE`, default `5`
- `ROCQ_LSP_MAX_ERRORS`, default `1000000`
- `ROCQ_LSP_WORKSPACE`, optional workspace override

The prototype is intentionally not security-hardened. Do not add path sandboxing
or command restrictions unless the task specifically asks for it.

## Development Commands

Run commands from this directory.

```bash
make test
make clean
```

`make test` runs the whole pytest suite with integration and end-to-end gates
enabled. Tests that need a real `coq-lsp` skip themselves if it is unavailable.
`make clean` removes pytest caches, Python bytecode caches, and egg-info
directories while preserving `.venv/`.

The local virtual environment is expected at `.venv/`. Use
`.venv/bin/python -m pytest ...` for focused test runs.

## Test Suite

- `tests/test_manager.py` uses fakes to cover manager behavior, versioning,
  workspace inference, language IDs, and hard-timeout reset logic.
- `tests/test_pretty.py` covers readable rendering of rocq-lsp Pp structures
  and proof-state normalization.
- `tests/test_integration.py` talks to a real `coq-lsp`. It checks clean files,
  multiple errors, and file-change behavior where a failing proof
  `Goal 0 = 0. Proof. fail. Qed.` reports proof state, then recompiles cleanly
  after changing `fail` to `reflexivity`.
- `tests/test_e2e_multifile_project.py` creates a temporary multi-file Rocq
  project, compiles it, appends late errors, and verifies the cached LSP
  session reports the new errors.

When changing output shape, update exact-output tests rather than weakening
assertions. The MCP output should be useful to agents and humans; raw Rocq
serialization details such as `Pp_glue` should not leak into normalized
proof-state fields.

Keep unrelated running `coq-lsp` processes alone unless the user explicitly asks
to inspect or stop them.
