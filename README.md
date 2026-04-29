# rocq-lsp-mcp-prototype

Prototype MCP server that compiles Rocq files through a persistent `coq-lsp`
session and returns compact structured diagnostics. Diagnostics expose a
human-facing `level`, `location`, message, optional `proof_name`, and compact
proof-state context when available. Results include the compiled file relative
to the workspace. Raw LSP plumbing such as document URIs, positions, duplicated
ranges, versions, empty lists, and null fields is omitted from the MCP response.
Agent-facing results keep only the first error for each `proof_name`, while
preserving warnings, infos, and errors without a proof name. The prototype talks
to `coq-lsp` directly over stdio JSON-RPC.

## Architecture

- `server.py` registers the single MCP tool and manages one `RocqLspManager`
  for the server lifespan.
- `manager.py` resolves files and workspaces, keeps the session registry, and
  orchestrates compile results.
- `session.py` owns each workspace session lifecycle, document versions, and
  notification store.
- `rocq_client.py` is transport-only JSON-RPC framing, requests, notifications,
  cancellation, `coq/getDocument`, `proof/goals`, and process cleanup.
- `notifications.py` stores diagnostics published by `coq-lsp`.
- `pipeline.py` converts raw diagnostics plus proof-state data into compact
  agent-facing `Diagnostic` models.

For `Attempt to save an incomplete proof` diagnostics, the `Qed.` span may only
contain the save error. The manager asks `proof/goals` with `mode: "Prev"` at
that diagnostic position and merges the returned compact goals and bullet state
into the diagnostic proof state.

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

Integration and e2e tests skip themselves when `coq-lsp` is unavailable. E2e
scenarios with compiled dependencies also skip when `rocq` is unavailable.
Scenario compile steps may include timing assertions for cache-sensitive
behavior; those timings are asserted by the harness but omitted from promoted
transcripts.
Use `make promote` to refresh promoted e2e transcripts after intentional output
changes; promotion runs serially because it rewrites golden files.

Use `make clean` to remove test artifacts.
