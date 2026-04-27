# rocq-lsp-mcp-prototype

Prototype MCP server that compiles Rocq files through a persistent `coq-lsp`
session and returns structured diagnostics. The prototype talks to `coq-lsp`
directly over stdio JSON-RPC.

## Run

```bash
uv run rocq-lsp-mcp
```

The server exposes one MCP tool:

- `rocq_lsp_compile_file(file: str)`

## Environment

- `ROCQ_LSP_BINARY` defaults to `coq-lsp`
- `ROCQ_LSP_TIMEOUT` defaults to `60`
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` defaults to `5`
- `ROCQ_LSP_MAX_ERRORS` defaults to `1000000`
- `ROCQ_LSP_WORKSPACE` optionally pins the workspace root

## Tests

Tests that require a real `coq-lsp` are skipped unless enabled:

```bash
RUN_ROCQ_LSP_INTEGRATION=1 uv run pytest tests/test_integration.py
RUN_ROCQ_LSP_E2E=1 uv run pytest tests/test_e2e_ocaml_modes.py
```

Security controls are intentionally minimal for this prototype.
