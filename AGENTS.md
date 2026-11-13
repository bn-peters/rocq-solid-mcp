# RocqSolid (rocq-solid-mcp) Guide

It is the duty of the agent to keep AGENTS.md and README.md up to date.

This package (`rocq-solid-mcp` on PyPI, MIT-licensed) is a Python MCP server for checking Rocq files and running
one-off Rocq commands through persistent file-scoped `coq-lsp` sessions.
Checking returns compact structured diagnostics and, when available, the source
sentence plus proof-state context for the sentence that produced an error.
`run_at` execution returns compact feedback output for the requested commands
string.

## Package Layout

- `src/rocq_solid_mcp/cli.py` defines the `rocq-solid-mcp` console entry point
  (also installed under the legacy name `bens-rocq-mcp`; keep both in
  `pyproject.toml`). With no arguments it starts the MCP server.
  `rocq-solid-mcp check
  path/to/file.v` runs a one-shot check through `RocqLspManager` and prints
  formatted JSON with no diagnostic message limit, no request timeout, no
  per-sentence command timeout, no after-error deadline, and no long-running
  command reporting.
- `src/rocq_solid_mcp/server.py` defines the FastMCP server, the lifespan-managed
  `RocqLspManager`, and the MCP tools
  `rocq_check_file(file: str,
  command_timeout_seconds: float | None = None,
  timeout_after_error_seconds: float | None = None,
  clear_cache: bool = False)` and
  `rocq_run_at(file: str, commands: str, line: int,
  character: int = 1)`.
  Tool handlers render internal result models before returning MCP output.
- `src/rocq_solid_mcp/manager.py` is the orchestration layer. It resolves files,
  infers workspaces from `_RocqProject` or `_CoqProject`, keeps cached
  sessions per resolved file path, evicts idle sessions, reads files, routes
  check and `run_at` requests, and converts outcomes into `CheckResult` or
  `RunAtResult`.
- `src/rocq_solid_mcp/session.py` owns the lifecycle for one file-scoped,
  workspace-backed `coq-lsp` session. It starts and stops the client, keeps
  the session lock, tracks document versions, and owns the session
  `NotificationStore`.
- `src/rocq_solid_mcp/notifications.py` stores LSP notification state. It handles
  `textDocument/publishDiagnostics`, tracks latest diagnostic versions by URI,
  stores latest `$/coq/fileProgress` payloads by URI/version for timeout
  context, stores `$/coq/filePerfData` payloads by URI/version for
  long-running command reporting, stores `$/coq/executionInformation` by
  URI/version for command timeout watchdogs and executed-range filtering, and
  lets callers wait briefly for diagnostics, performance data, or execution
  changes for a requested document version. It also exposes
  `has_error_diagnostic`/`wait_for_error_diagnostic` (exact document version
  only) for the after-error deadline. Starting a new document version clears
  all older notification payloads and waiter events for that URI.
- `src/rocq_solid_mcp/pipeline.py` normalizes raw diagnostics into compact
  internal `Diagnostic` models. It attaches the source sentence and proof
  states by matching sentence ranges, normalizing only spans referenced by a
  diagnostic, and merges supplemental `proof/goals` results
  for incomplete proof saves, and leaves response filtering and truncation to
  the render step.
- `src/rocq_solid_mcp/rocq_client.py` is the manual stdio JSON-RPC client for
  `coq-lsp`. It handles order-independent `Content-Length` framing, chunked
  stderr draining, reader-failure session invalidation, `initialize`, server
  requests, `coq/getDocument`, `proof/goals`, `petanque/run_at_point`,
  soft request deadlines, host cancellation cleanup, optional per-command
  watchdog cancellation with the after-error deadline, process cleanup, and
  dispatches notifications to `NotificationStore`. Mid-request process-exit
  failures include the child exit code or POSIX signal and the last 20 captured
  stderr lines when available.
- `src/rocq_solid_mcp/pretty.py` converts rocq-lsp serialized pretty-print trees
  such as `Pp_glue`, `Pp_box`, and `Pp_print_break` into readable text, then
  normalizes proof-state payloads into compact agent-facing JSON. Goal
  hypotheses are rendered as strings like `H : True` or `n := 0 : nat`.
- `src/rocq_solid_mcp/models.py` contains the internal Pydantic result models,
  `TimeoutContext`, and `RenderOptions`. Internal models keep normalized raw
  ranges, document metadata, diagnostics, source sentences, proof states,
  long-running command data, and timeout state.
  Their `render(options)` methods are the only place that decides the compact
  MCP output shape: file display, severity labels, location format, proof-state
  inclusion, duplicate proof-error filtering, error-only check rendering,
  global cascade-error suppression, message limits, timeout formatting,
  long-running command formatting, and empty-field pruning. Default rendering
  keeps only the first
  error for each `proof_name` and, after the first error without a
  `proof_name`, suppresses later errors that also lack a `proof_name`; warnings
  and infos are retained internally but omitted from default check output. This
  broad cascade guard can hide later independent top-level errors that also
  lack `proof_name`. Rendering caps returned error diagnostics at 10 by default
  with a hard cap of 50, and includes compact `timeout_context` only for
  timeout results.
- `src/rocq_solid_mcp/config.py` loads environment configuration, defines
  `TimeoutPolicy`, builds the `opam exec -- coq-lsp` command, and builds the
  `coq-lsp` initialization settings. `send_execinfo` is enabled so per-call
  command timeouts can be used without restarting sessions, and
  `send_perf_data` is enabled so check calls can report long-running
  commands.

The server intentionally talks to `coq-lsp` directly. Do not reintroduce an
LSP client library unless the user explicitly asks for that direction.
The main source of documentation on `coq-lsp` is the upstream protocol guide:
https://github.com/rocq-community/rocq-lsp/blob/main/etc/doc/PROTOCOL.md

## Runtime Behavior

The check MCP tool receives a Rocq file path, optional
`command_timeout_seconds`, optional `timeout_after_error_seconds`, and
optional `clear_cache`. It reports only error
diagnostics; warning and info diagnostics are intentionally omitted from the
tool response. Recommend recompiling the file with Rocq, for example
`rocq c path/to/file.v`, when callers need warnings. Returned errors are capped
by `ROCQ_LSP_MESSAGE_LIMIT`, which defaults to 10 and is capped at 50. The
limit is render-only and does not truncate the internal `CheckResult`.
`ROCQ_LSP_TIMEOUT` controls the soft `coq-lsp` partial-result deadline for
check and `run_at` calls. Set it slightly lower than the host-level MCP
timeout, leaving room for `ROCQ_LSP_HARD_TIMEOUT_GRACE`, when callers want
partial timeout results back.
For `command_timeout_seconds`, `None` uses `ROCQ_LSP_COMMAND_TIMEOUT`, which
defaults to 5 seconds, `0` disables the command watchdog for that call, and
positive values enable it. `timeout_after_error_seconds` overrides
`ROCQ_LSP_TIMEOUT_AFTER_ERROR` per call with the same convention. The server reports `long_running_commands` for
commands that actually ran during check and took at least 80% of
the configured request timeout. `clear_cache=True` kills and replaces the
cached `coq-lsp` process for the requested file before sending `didOpen`, so
the new process rereads `_RocqProject` or `_CoqProject` and reloads imports
from external `.vo` files. When `ROCQ_LSP_DUNE_BUILD=1`, both `rocq_check_file`
and `rocq_run_at` calls first try
to detect dune-based Rocq projects with `dune describe workspace` and
`dune rocq top --no-build --toplevel echo file.v`, falling back to
`dune coq top --no-build --toplevel echo file.v`. If dune recognizes the file,
the manager runs `dune rocq top file.v --toplevel echo --display=short` or the
corresponding `dune coq top` command to build dependencies without recompiling
the requested file itself. If that step reports actual build actions on stderr
for an already cached file session, the manager
restarts the session as if `clear_cache=True` had been set so rebuilt
dependencies are reloaded. Two kinds of stderr line are not treated as a
rebuild: Dune's `Entering directory`/`Leaving directory` navigation lines
(printed whenever it runs from a theory subdirectory), and coqdep
`.theory.d` regeneration (which dune re-runs when the edited target file itself
changes, without recompiling any dependency). Ignoring them keeps an up-to-date
check and an edit-then-recheck from restarting the session and discarding the
incremental cache. Missing `dune`, non-dune projects, files
not recognized by dune, and successful dune top commands with no build actions
are skipped; a recognized dune file whose dependency build fails returns
`status: "failed"` with dune output. All dune detection and dependency-build
commands are serialized inside one MCP server process so parallel
`rocq_check_file` calls from one client do not race for dune's build directory
lock; separate MCP server processes and manual dune invocations are not covered
by this in-process lock. The `check` CLI subcommand has no flags
and deliberately ignores the
MCP-oriented timeout and message-limit defaults: it disables the request
timeout, per-sentence command timeout, after-error deadline, diagnostic
truncation, and timeout-derived long-running command reporting for a single
formatted JSON result. The `run_at` MCP tool receives a Rocq file path, one string containing
one or more complete commands ending in `.`, and a 1-based line/character
position; it is useful for one-off tasks such as `Check`, `Search`, `Require`,
`Locate`, and notation experiments. On each call, the manager resolves the
file, infers a workspace by walking upward to the nearest `_RocqProject` or
`_CoqProject`, builds dune dependencies the same way `rocq_check_file` does so
`Require`s resolve against freshly compiled `.vo` files, then delegates to the
cached `RocqLspSession` for that resolved
file path. If no project file is found, the file's parent directory is used.
The session sends either `textDocument/didOpen` or `textDocument/didChange`
through its client. The document version increments on every call.
If the configured launcher cannot be started, such as missing `opam` when
`ROCQ_LSP_USE_OPAM=1` or missing direct `coq-lsp` when
`ROCQ_LSP_USE_OPAM=0`, both MCP tools return `status: "failed"` with an
informative message instead of raising a raw subprocess error. The message
includes the command that was tried and points callers at `ROCQ_LSP_BINARY` and
`ROCQ_LSP_USE_OPAM`.

Each requested file gets a separate long-lived `coq-lsp` process. The process
starts with `cwd` set to the inferred workspace and, by default, with
`opam exec -- coq-lsp` so local opam switches select the appropriate Rocq and
`coq-lsp` versions. Calls for different files may use different `coq-lsp`
processes concurrently, even when the files are in the same workspace. Calls
for the same file share one session and serialize on that session's lock.

Checking is driven by `coq/getDocument` with `goals="Str"`. This asks
`coq-lsp` for the checked document and proof-state data around spans. The
`NotificationStore` collects diagnostics from
`textDocument/publishDiagnostics`. `pipeline.py` normalizes those diagnostics
and attaches source sentences plus proof states without applying agent-facing
filtering. The
`CheckResult.render(RenderOptions(...))` step converts raw LSP ranges to
1-based locations, includes the normalized source sentence when available,
converts severity numbers to `error`, `warning`, or `info`, removes empty
fields, filters duplicate proof errors by default, omits non-error diagnostics
from default check output, suppresses later global errors after the first error
without `proof_name`, and applies diagnostic limits. Counts include only
nonzero diagnostic buckets after filtering and are omitted when no diagnostics
remain after filtering. When diagnostics are omitted because of `message_limit`,
the response includes `omitted_counts` and the effective `message_limit`.

When enabled, long-running command reporting observes `$/coq/filePerfData`,
filters out entries below the alert threshold, filters out entries with
`info.cache_hit: true`, intersects perf entries with
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
not per internal Ltac substep. The last sentence in a file is governed by the
outer request timeout rather than the per-command watchdog because
`$/coq/executionInformation` reports sentence starts, not sentence completion;
otherwise end-of-file finalization can be incorrectly attributed to the last
sentence. The `ROCQ_LSP_TIMEOUT` request deadline remains the outer server-side
partial-result deadline for check and `run_at` calls. MCP host cancellation is a
harder outer abort: the server sends `$/cancelRequest` to `coq-lsp` and may reset
the file session, but the client may already have abandoned the result.

`ROCQ_LSP_TIMEOUT_AFTER_ERROR` adds an overall deadline for the rest of a
check once the watchdog observes the first error diagnostic for the current
document version: the whole call is cancelled at most that many seconds after
the first error, and the diagnostics collected so far are returned. It bounds
how long a check with a known error keeps running while still reporting later
errors found within the window. The deadline is absolute — unlike the
per-sentence watchdog it never resets, and it also covers the final sentence
and end-of-file finalization, which the per-sentence watchdog exempts (after
an error, a bounded exit matters more than attributing that time precisely).
The normal `ROCQ_LSP_COMMAND_TIMEOUT` watchdog keeps applying per sentence
before and after the error. Such cancellations return `status: "timeout"`
with `timeout_kind: "after_error"` and `timeout_after_error_seconds` in the
timeout context; `timed_out_range` points at the sentence that was executing
when the deadline expired, when one was. The variable defaults to 10 seconds;
`0` or negative values disable the feature, and a set-but-empty value falls
back to the default like the unset case. The check tool's
`timeout_after_error_seconds` argument overrides the variable for one call
with the same convention as `command_timeout_seconds` (`None` uses the
default, `0` disables, positive enables); the feature applies only to check
calls, not `run_at`.

For `Attempt to save an incomplete proof` diagnostics, the `Qed.` sentence can
carry only the save error while the open goal is attached to the previous proof
span. The manager issues a best-effort `proof/goals` request with `mode="Prev"`
at the diagnostic position and `pipeline.py` merges the returned compact goals
and bullet state into the existing proof-state payload. Timed-out checks skip
these supplemental requests: each may wait up to the request deadline, which
would stretch the bounded exit the timeout just promised.

`run_at` execution is driven by `petanque/run_at_point`, because
`proof/goals` with the `command` parameter does not surface feedback from
commands such as `Search` or `Print` in current `coq-lsp`. The tool should be
called with one string containing one or more complete commands right after
imports when possible, or between lemmas/definitions when local declarations,
scopes, or modules are required. The commands are ephemeral and do not edit the
source file.

Caching is delegated to the long-lived `coq-lsp` process. The Python side keeps
the session, notification, and version state alive so repeated calls for changed
files can use rocq-lsp's incremental checking behavior. Idle sessions are
cleaned up by the MCP server's background reaper and opportunistically during
check calls after `ROCQ_LSP_IDLE_TTL_SECONDS`; sessions that are checking or
queued for a check are not stopped.

Timeout and cancellation handling has three layers:

- `ROCQ_LSP_TIMEOUT` is the soft `coq/getDocument` or
  `petanque/run_at_point` partial-result deadline; it defaults to 55 seconds,
  is capped at 3600 seconds, and should be set slightly lower than the overall
  MCP tool timeout.
- `ROCQ_LSP_COMMAND_TIMEOUT` is an optional per-sentence watchdog during
  `coq/getDocument`; unset or non-positive values disable it.
  `ROCQ_LSP_TIMEOUT_AFTER_ERROR` (default 10 seconds) additionally bounds the
  rest of a check to that many seconds once the first error diagnostic is
  observed; non-positive values disable it.
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` is the extra wait after sending
  `$/cancelRequest`, including cleanup after host-level MCP cancellation.

If the hard timeout is hit, the manager kills and drops that file session
so the next call starts with a fresh `coq-lsp`. Hard-timeout responses still
include timeout context built from any latest progress notification observed
before the session reset. If the `coq-lsp` process dies mid-request (a broken
pipe or closed stdout), the manager likewise resets the session and returns
`status: "failed"` with `restarted` set, its exit code or POSIX signal, and the
last 20 stderr lines when available, so the next call starts fresh instead of
reusing the dead process.

## Configuration

The server reads these environment variables:

- `ROCQ_LSP_BINARY`, default `coq-lsp`
- `ROCQ_LSP_USE_OPAM`, default `1`; set to `0` to run the binary directly
- `ROCQ_LSP_TIMEOUT`, default soft partial-result deadline `55`, capped at
  `3600`, and should be set slightly lower than the overall MCP tool timeout
- `ROCQ_LSP_HARD_TIMEOUT_GRACE`, default `5`
- `ROCQ_LSP_COMMAND_TIMEOUT`, default `5`; set `0` to disable per-sentence
  command timeout by default
- `ROCQ_LSP_TIMEOUT_AFTER_ERROR`, default `10`; positive values bound the
  rest of a check to that many seconds after its first error diagnostic, `0`
  or negative values disable it, and an empty value falls back to the
  default
- `ROCQ_LSP_MESSAGE_LIMIT`, default `10`; values above `50` are capped
- `ROCQ_LSP_MAX_ERRORS`, default `1000000`
- `ROCQ_LSP_WORKSPACE`, optional workspace override
- `ROCQ_LSP_IDLE_TTL_SECONDS`, default `1800`
- `ROCQ_LSP_DUNE_BUILD`, default `1`; set to `0` to disable automatic dune
  dependency builds before check calls
- `ROCQ_LSP_DUNE_TIMEOUT`, default `55`, capped at `3600`; limits each dune
  detection or dependency-build command

The server is intentionally not security-hardened. Do not add path sandboxing
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
`make test PYTEST_WORKERS=4`. The Makefile exports `OPAMSWITCH` from
`opam switch show` unless it is already set, so nested `opam exec -- coq-lsp`
calls from temporary test workspaces use the same switch as the top-level test
command; override it with a Make variable such as
`make test OPAMSWITCH=my-switch` when needed. Tests that need a real `coq-lsp`
skip themselves if `opam` or `coq-lsp` is unavailable. E2e scenarios with
compiled dependencies also skip themselves if `rocq` is unavailable.
`make promote` refreshes promoted e2e transcripts and runs serially because it
rewrites golden files. `make clean` removes pytest caches, Python bytecode
caches, and egg-info directories while preserving `.venv/`.

The local virtual environment is expected at `.venv/`. Use
`opam exec -- .venv/bin/python -m pytest ...` for focused test runs.

The Rocq toolchain (`rocq`, `dune`, `coq-lsp`) lives in a local opam switch
(for example `my-rocq-lsp`) that is not selected automatically outside its own
directory. Running `opam exec -- dune`, `opam exec -- rocq`, or
`opam exec -- coq-lsp` from an unrelated directory such as a temporary test
workspace or the system temp dir resolves the wrong switch or none, so `rocq`
and `coqc` appear to be missing. Pin the switch with `OPAMSWITCH` (the Makefile
exports it from `opam switch show`) whenever invoking these tools from outside
the project directory. This switch provides `rocq` but not the legacy `coqc`,
so dune-based Rocq fixtures must use the new Rocq build language: `(lang dune
3.21)` or newer with `(using rocq 0.11)` and `(rocq.theory ...)` stanzas, which
invoke `rocq` and generate `_RocqProject`. The deprecated `(using coq ...)` /
`(coq.theory ...)` build language shells out to `coqc` and therefore fails in
this switch.

## CI and Releases

CI lives in `.github/workflows/ci.yml`; do not move CI setup into an auxiliary
script unless the user explicitly asks for that. The `unit` job runs
`tests/unit` on Python 3.11 through 3.14 without any Rocq toolchain. The
`test` job runs `make test` in a matrix over `rocq-core` 9.0.0, 9.1.1, and
9.2.0: it sets up OCaml 5.2 with `ocaml/setup-ocaml` (which also caches the
opam root), adds the `coq-released` and `rocq-solid`
(https://github.com/bn-peters/rocq-solid-mcp-opam) opam repositories, installs
the pinned `rocq-core` plus unconstrained `rocq-stdlib` and
`rocq-solid-mcp-lsp`, creates `.venv/` with `uv`, installs `.[dev]`, and then
relies on the existing Makefile so tests run through
`opam exec -- .venv/bin/python`.

`.github/workflows/release.yml` publishes to PyPI via trusted publishing when a
tag `vX.Y.Z` is pushed; it fails if the tag does not match `version` in
`pyproject.toml`. The patched rocq-lsp lives on the `rocq-solid-9.x` branches of
https://github.com/bn-peters/rocq-lsp and is packaged as `rocq-solid-mcp-lsp` in
the opam repository above.

## Test Suite

- `tests/unit/test_manager.py` uses fakes to cover manager behavior, versioning,
  workspace inference, per-file session separation, `run_at` execution,
  idle cleanup, language IDs, timeout context construction, and hard-timeout
  reset logic. It also covers the dune dependency-build integration: the
  `applied` heuristic ignoring `Entering directory`/`Leaving directory` stderr
  noise and coqdep `.theory.d` regeneration, and `run_at` triggering the dune
  build (including its failure and cache-restart paths) the same way `check` does.
  It also covers threading the after-error deadline to the client
  (non-positive policy values and per-call overrides disabling it), the
  `after_error` timeout context, skipping supplemental goals requests on
  timed-out checks, and resetting the session when the `coq-lsp` process dies
  mid-request.
- `tests/unit/test_config.py` covers command construction and environment
  configuration, including non-finite timeout values being rejected and the
  warning for the renamed `ROCQ_LSP_COMMAND_TIMEOUT_AFTER_ERROR` variable.
- `tests/unit/test_notifications.py` covers diagnostic notification storage,
  file-progress storage for timeout context, execution-info storage for command
  timeout, error-diagnostic observation for the after-error deadline,
  clearing, waiting, fallback behavior, and ignored notifications.
- `tests/unit/test_pipeline.py` covers proof-state attachment and agent-facing
  diagnostic filtering.
- `tests/unit/test_pretty.py` covers readable rendering of rocq-lsp Pp structures
  and proof-state normalization.
- `tests/integration/test_integration.py` talks to a real `coq-lsp`. It checks
  clean files, multiple errors, `run_at` feedback and failures, and
  file-change behavior where a failing proof `Goal 0 = 0. Proof. fail. Qed.`
  reports proof state, then rechecks cleanly after changing `fail` to
  `reflexivity`.
- `tests/e2e/test_scenarios.py` runs promoted scenario fixtures. Each fixture is
  a full Rocq project the harness copies and **builds before driving coq-lsp**.
  The backend is detected from the fixture and overridable with a scenario
  `build` key (`dune` | `make` | `none`): a `dune-project` selects dune (the
  default) and a bare `_CoqProject` selects CoqMakefile. The dune build runs
  `dune build <dir>/_RocqProject` for each theory, which writes the
  `_RocqProject` coq-lsp reads and compiles **no** `.vo` — so it never compiles
  an intentionally broken or slow step-target; the manager compiles each step's
  dependencies on demand during the run. The make build runs
  `rocq makefile -f _CoqProject -o Makefile` then `make`. Fixtures then check
  files or run `run_at` through one cached manager, create or edit files, and
  check concise agent-facing transcripts. A step's `action` is one of `create`
  (write a brand-new file that must not already exist), `append`, `replace`,
  `check`, or `run_at`. Under dune, `_infer_workspace` resolves to the theory
  subdirectory, so the transcript `file` field is a basename. Scenario files may
  define a top-level `timeout_policy` for timeout-specific coverage, including
  `request_seconds_after_error` for the overall after-error deadline.
  Scenario check and
  `run_at` steps may include `min_seconds` or `max_seconds` timing assertions
  for cache-sensitive behavior; timings are asserted by the harness but omitted
  from promoted transcripts. Scenario check steps may also include
  `command_timeout_seconds` or `timeout_after_error_seconds` to cover
  per-call check timeout overrides, and manager-level `long_running_seconds`
  to exercise long-running command reporting.
  `diagnostic_message_limit` covers the default error diagnostic limit.
  `diagnostic_errors_only` covers error-only check rendering when the file
  also produces a warning. `run_at_info_outputs` covers promoted `Search`,
  `Check`, and `Compute` outputs rendered at info level. `run_at_search` covers
  promoted output for a narrow `Search` query. `run_at_cached_after_slow_proof`
  checks that `Search` after a cached slow proof is quick. `timeout_recovery`
  uses the local `sleep` tactic to cover a soft `coq/getDocument` timeout
  followed by a successful recheck in the same manager.
  `hard_timeout_resets_cache` sets the cancel grace to zero and checks that a
  hard timeout drops the old `coq-lsp` process by requiring a later recheck to
  rerun an unchanged slow proof. `check_command_timeout_argument` covers the
  check tool call's per-call `command_timeout_seconds` override.
  `command_timeout` uses the same local `sleep` tactic with a long global
  timeout and short command timeout to cover per-sentence cancellation.
  `timeout_after_error` covers the overall after-error deadline: after an
  early error, a second fast error is still reported within the window, a
  chain of sentences that each pass the normal per-sentence watchdog is
  stopped at most the deadline after the first error
  (`timeout_kind: "after_error"`), and once the errors are fixed the deadline
  never arms even though a remaining sentence exceeds it.
  `timeout_after_error_zero` covers the disable semantics: a zero after-error
  deadline leaves the post-error slow proof running until the request
  deadline (`timeout_kind: "request"`), and a per-call
  `timeout_after_error_seconds` override on the next check re-enables the
  bounded exit (`timeout_kind: "after_error"`).
  `timeout_after_error_completes` checks that errors followed by only fast
  sentences complete without any after-error cancellation.
  `timeout_after_error_last_sentence` checks that after an error the deadline
  also cancels the document's final sentence, which the per-sentence watchdog
  exempts.
  `request_timeout_resume_and_error_cache` covers repeated soft partial-result
  timeouts resuming from cached progress while preserving an earlier error
  diagnostic, including the final cached successful check.
  `request_timeout_resume_then_success` covers the two-call case where a soft
  partial-result timeout reports an earlier error, then the next cached check
  succeeds and reports the same error.
  `repeated_check_error_cached` calls check twice on the same file with a
  slow cached prefix and verifies both calls report the same error while the
  second call is quick.
  `long_running_proofs_cached` opts in to long-running command reporting and
  verifies that the first slow `sleep 5.` tactic is reported while the later
  cached recheck is not.
- `tests/e2e/test_check_restart.py` covers the check tool's `clear_cache`
  argument by recompiling a dependency externally, showing a stale import
  without `clear_cache`, then refreshing it by restarting the file's
  `coq-lsp` process.
  `lemma_header_cascade_error` covers malformed lemma headers that should report
  only the originating lemma diagnostic instead of downstream proof-mode
  cascade errors.
  `dune_recheck_no_restart` is a dune project whose slow proof is checked twice
  unchanged; the promoted transcript shows no `restarted` on the second check and
  the `max_seconds` window enforces the cache hit, covering the dune
  dependency-build integration end to end (no spurious restart on recheck).
  `dune_run_at_builds_dep` is a dune project whose dependency `.vo` is left
  uncompiled by the setup build; `run_at` must build it so the client's `Require`
  resolves.
  `dune_check_created_dependency` creates two brand-new files at run time in a
  dune theory — `a.v` with a definition and `b.v` that `Require`s `a` — and then
  checks `b.v`. The setup build compiles no `.vo`, so the check succeeds only if
  the manager's dune dependency build compiles the freshly created `a.v`; a
  committed placeholder keeps the theory non-empty at setup so its generated
  `_RocqProject` is representative (an empty theory is emitted with `-noinit`).
  `dune_check_created_subdir_dependency` is the same idea one directory deeper: a
  dune theory with `(include_subdirs qualified)` ships only `a.v` at setup, then
  creates a nested `b/c.v` and `b/d.v` (with `d.v` requiring `newfiles.b.c`) at
  run time and checks `b/d.v`. The subdirectory does not exist when the setup
  build generates `_RocqProject`, so the check succeeds only if the manager's
  dune build discovers the new subdirectory and compiles `b/c.v`; the workspace
  resolves to `src/`, so the transcript `file` is the nested `b/d.v`.
  `make_check_sanity` and `make_run_at_sanity` exercise the CoqMakefile
  backend (`rocq makefile` + `make`): a make-built dependency loads for `check`,
  and `run_at` works on a `_CoqProject` project.

When changing output shape, update exact-output tests rather than weakening
assertions. The MCP output should be useful to agents and humans; raw Rocq
serialization details such as `Pp_glue` should not leak into normalized
proof-state fields.

Keep unrelated running `coq-lsp` processes alone unless the user explicitly asks
to inspect or stop them.
