# rocq-lsp-mcp-prototype

Prototype MCP server that compiles Rocq files through persistent project-scoped
`coq-lsp` sessions and returns compact structured diagnostics. Diagnostics
expose a human-facing `level`, `location`, message, optional `proof_name`, and
compact proof-state context when available. Results include the compiled file
relative to the workspace. Raw LSP plumbing such as document URIs, positions,
duplicated ranges, versions, empty lists, and null fields is omitted from the MCP
response. Agent-facing results keep only the first error for each `proof_name`,
while preserving warnings, infos, and errors without a proof name. Returned
diagnostics are limited per severity bucket: the default is 10 errors, 10
warnings, and 10 info/hint messages, and callers may request up to the hard cap
of 50 per bucket. The prototype talks to `coq-lsp` directly over stdio JSON-RPC.
Timeout results include a compact `timeout_context` that reports the request
operation, timeout settings, the latest checked position, the latest processing
range, and a small preview of the next unchecked source line when available.

## Architecture

- `server.py` registers the single MCP tool and manages one `RocqLspManager`
  for the server lifespan.
- `manager.py` resolves files and workspaces, keeps the session registry,
  evicts idle sessions, and orchestrates compile results.
- `session.py` owns each workspace session lifecycle, document versions, and
  notification store.
- `rocq_client.py` is transport-only JSON-RPC framing, requests, notifications,
  cancellation, `coq/getDocument`, `proof/goals`, and process cleanup.
- `notifications.py` stores diagnostics and latest file-progress notifications
  published by `coq-lsp`.
- `pipeline.py` converts raw diagnostics plus proof-state data into compact
  agent-facing `Diagnostic` models.

For `Attempt to save an incomplete proof` diagnostics, the `Qed.` span may only
contain the save error. The manager asks `proof/goals` with `mode: "Prev"` at
that diagnostic position and merges the returned compact goals and bullet state
into the diagnostic proof state.

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

## Run

```bash
uv run rocq-lsp-mcp
```

The server exposes one MCP tool:

- `rocq_lsp_compile_file(file: str, message_limit: int = 10)`

`message_limit` caps returned diagnostics per severity bucket. Values above 50
are capped to 50. When diagnostics are omitted, the response includes
`omitted_counts` and `message_limit` alongside total `counts`.

## Environment

- `ROCQ_LSP_BINARY` defaults to `coq-lsp`
- `ROCQ_LSP_USE_OPAM` defaults to `1`; set to `0` to run the binary directly
- `ROCQ_LSP_TIMEOUT` defaults to `60`
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` defaults to `5`
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
the harness defaults for timeout-specific coverage. The e2e suite covers both
soft timeout recovery and hard-timeout session reset behavior.
Use `make promote` to refresh promoted e2e transcripts after intentional output
changes; promotion runs serially because it rewrites golden files.

Use `opam exec -- .venv/bin/python -m pytest ...` for focused test runs outside
of the Makefile. Use `make clean` to remove test artifacts.
