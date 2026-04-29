# rocq-lsp-mcp-prototype

Prototype MCP server that compiles Rocq files through a persistent `coq-lsp`
session and returns structured diagnostics. Diagnostics preserve raw LSP data
and expose normalized fields such as proof-state context and, when `coq-lsp`
reports it, the enclosing `proof_name`. The prototype talks to `coq-lsp`
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

These targets run tests in parallel with `pytest-xdist` by default. Override the
worker count with a Make variable, for example `make test PYTEST_WORKERS=4`.

Integration and e2e tests skip themselves when `coq-lsp` is unavailable.
Use `make promote` to refresh promoted e2e transcripts after intentional output
changes; promotion runs serially because it rewrites golden files.

Use `make clean` to remove test artifacts.
