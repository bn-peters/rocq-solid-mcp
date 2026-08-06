# Ben's Rocq MCP

*I'm accepting better name proposals.*

*Join us on the [Iris Mattermost]*:
1. Go to the [GitLab log-in page](https://gitlab.mpi-sws.org/users/sign_in) and select the "GitHub" button, then log in with your Github account.
2. Log in to Mattermost; this should use your GitLab login.
3. Join the [Ben's Rocq MCP](https://mattermost.mpi-sws.org/iris/channels/bens-rocq-mcp) channel.

If you notice that your agent misbehaves with this MCP, please let me know! It
would be particularly helpful if you could share your transcripts, which you
can find at ~/.codex/sessions/.

The MCP exposes two tools:
1. `check`, which generates diagnostics for a given file. It can return errors
   from multiple proofs at once. Proof states are cached.
   For dune-based Rocq projects, `check` and `run_at` attempt to
   rebuild dependencies with dune before checking the file.
2. `run_at`, which runs one-off commands at a given position in a file. This is
   intended to be used for queries like `Search` and `Check`, and for one-off
   debugging commands.

## Installation

This package depends on a patched version of `coq-lsp`. It is compatible with
Rocq 9.0, 9.1, and 9.2. To install it:
```bash
opam repo add bpeters https://gitlab.mpi-sws.org/bpeters/opam.git
opam install bens-rocq-lsp
```
You might be asked to uninstall `coq-lsp`. Don't worry, `bens-rocq-lsp` is
(probably) compatible with your other tools. It must be installed in all opam
switches you plan to use Ben's Rocq MCP with.

Then execute in a clone of this repository:
```
uv tool install -e .
```
This installs the package to `~/.local/bin/`. You can also install the package
with other tools.

## Usage

To teach Codex about Ben's Rocq MCP:
```bash
codex mcp add bens-rocq-mcp bens-rocq-mcp
```

The MCP looks for `_CoqProject` and `_RocqProject` files to set up file
dependencies correctly. To generate them in a `dune`-based project, add
`(generate_project_file)` to your `coq.theory` stanzas.

The MCP figures out the right switch itself if you have linked with `opam
switch link . [switch]`.

The MCP currently always needs a `_CoqProject` or
`_RocqProject` file with -suitable `-R` and `-Q` entries, even if using
`dune`. You may find the `(generate_project_file)` option for `dune` to
generate `_RocqProject` files useful.

You should consider disabling all other Rocq MCPs. If you have slow-to-compile
files, you should increase the tool timeout; This MCP has its own timeout
mechanism. Add this to `.codex/config.toml` in your repository:
```
[mcp_servers.rocq-mcp]
enabled = false

[mcp_servers.bens-rocq-mcp]
tool_timeout_sec = 300
env = { ROCQ_LSP_TIMEOUT = "290" }
```
Set `ROCQ_LSP_TIMEOUT` slightly lower than `tool_timeout_sec` so the MCP can
return partial timeout results before Codex cancels the entire tool call. The
`check` tool is considerably slower than standard compilation, but I expect
that caching makes up for this overall.

## Tools

**You should read the full tool prompts at
[src/rocq_lsp_mcp/server.py](src/rocq_lsp_mcp/server.py).**

I hope that these two tools are sufficient for most, if not all Rocq tasks. If
you find yourself needing something else, please let me know! If possible,
include an agent transcript where a task could've been completed more
effectively with additional tools.

## Environment Variables

All optional; defaults in parentheses.

- `ROCQ_LSP_BINARY` (`coq-lsp`): the `coq-lsp` executable
- `ROCQ_LSP_USE_OPAM` (`1`): run via `opam exec --`; `0` runs the binary
  directly
- `ROCQ_LSP_WORKSPACE` (unset): override the inferred workspace root
- `ROCQ_LSP_TIMEOUT` (`55`, max `3600`): deadline per call; set slightly below
  the MCP tool timeout
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` (`5`): extra wait after cancellation before the
  session is killed
- `ROCQ_LSP_COMMAND_TIMEOUT` (`5`): default per-sentence timeout; `0` disables
- `ROCQ_LSP_TIMEOUT_AFTER_ERROR` (`10`): overall deadline for the rest of a
  check after its first error; `0` disables
- `ROCQ_LSP_MESSAGE_LIMIT` (`10`, max `50`): max errors returned per check
- `ROCQ_LSP_MAX_ERRORS` (`1000000`): max errors `coq-lsp` collects
- `ROCQ_LSP_IDLE_TTL_SECONDS` (`1800`): idle time before cached sessions are
  stopped
- `ROCQ_LSP_DUNE_BUILD` (`1`): build dune dependencies before checks; `0`
  disables
- `ROCQ_LSP_DUNE_TIMEOUT` (`55`, max `3600`): limit per dune detection or build
  command




**The rest of this file is a long AI-generated repository summary. I didn't
feel like deleting it, but I'm not convinced it's useful :)**

# bens-rocq-mcp


Prototype MCP server that checks Rocq files and runs one-off Rocq commands
through persistent file-scoped `coq-lsp` sessions. Check diagnostics report
errors only and expose a human-facing `level`, `location`, message, optional
`sentence`, optional `proof_name`, and compact proof-state context when
available. The `sentence` field is sliced from `coq-lsp`'s sentence range when
available, so tactic errors include the tactic sentence such as
`idtac "3"; fail.`. Recompile the file with Rocq, for example
`rocq c path/to/file.v`, when warnings are needed.
`run_at` results expose compact feedback output for the provided commands
string, including warning feedback when `coq-lsp` returns it. Check results may
include `long_running_commands` entries with source text and location for
commands that actually ran during that check and exceeded the 80% alert
threshold derived from the configured request timeout. Results include the file
relative to the workspace. Raw LSP
plumbing such as document URIs, positions, duplicated ranges, versions, empty
lists, and null fields is omitted from the MCP response. Agent-facing check
results keep only the first error for each `proof_name` and, after the first
error without a
`proof_name`, suppress later errors that also lack a `proof_name`. Warnings and
infos are retained internally but omitted from the check tool response. This
broad cascade guard can hide later independent top-level errors that also lack
`proof_name`. Returned error diagnostics are limited by `ROCQ_LSP_MESSAGE_LIMIT`,
which defaults to 10 and is capped at 50. Check results include `counts` for
nonzero error diagnostics only; clean results omit `counts`. Timeout results include
a compact `timeout_context` that reports the request operation, timeout
kind and settings, the latest checked position, the latest processing range,
and a small preview of the next unchecked source line when available. The soft
coq-lsp partial-result deadline is configured with `ROCQ_LSP_TIMEOUT`; the
default is 55 seconds and the maximum is 3600 seconds. Set it slightly lower
than the host-level MCP tool timeout so the server can return partial
diagnostics before the host cancels the whole tool call, leaving room for
`ROCQ_LSP_HARD_TIMEOUT_GRACE`. Optional per-command timeouts use
coq-lsp sentence execution notifications, so the timeout unit is one Rocq
sentence such as `sleep 5.` or `repeat ... .`, not an internal Ltac substep. The
prototype talks to `coq-lsp` directly over stdio JSON-RPC.

Check calls can pass `clear_cache=True` to restart the requested file's
`coq-lsp` process before checking, so changed project files or externally
compiled dependencies are picked up. For dune-based Rocq projects, the manager
also attempts a dune dependency build before checking or running commands; when
that step reports real build actions (dune's `Entering directory`/`Leaving
directory` navigation lines are ignored), it restarts an existing `coq-lsp`
process as if `clear_cache=True` had been set.

## Architecture

- `server.py` registers the MCP tools, manages one `RocqLspManager`
  for the server lifespan, and renders internal results for MCP output.
- `manager.py` resolves files and workspaces, keeps the file-keyed session
  registry, evicts idle sessions, and orchestrates internal check and `run_at`
  results.
- `session.py` owns each file-scoped session lifecycle, document versions, and
  notification store. Each session still starts `coq-lsp` with an inferred
  workspace root.
- `rocq_client.py` is transport-only JSON-RPC framing, requests,
  cancellation, `coq/getDocument`, `proof/goals`, `petanque/run_at_point`, and
  process cleanup.
- `notifications.py` stores diagnostics, latest file-progress notifications,
  file performance data, and sentence execution notifications published by
  `coq-lsp`.
- `pipeline.py` converts raw diagnostics plus source sentence and proof-state
  data into internal `Diagnostic` models without deciding what the MCP response
  should include.
- `models.py` holds internal result models, `TimeoutContext`, and the
  `RenderOptions`-driven render step that applies agent-facing filtering,
  error-only check rendering, message limits, location formatting, timeout
  formatting, long-running command formatting, and empty-field pruning.

For `Attempt to save an incomplete proof` diagnostics, the `Qed.` span may only
contain the save error. The manager asks `proof/goals` with `mode: "Prev"` at
that diagnostic position and merges the returned compact goals and bullet state
into the diagnostic proof state. Timed-out checks skip these supplemental
requests so the exit stays bounded.

`run_at` execution uses `petanque/run_at_point`, because `proof/goals` with the
`command` parameter does not return feedback from commands such as `Search` or
`Print` in current `coq-lsp`. Call `run_at` with one string containing one or
more complete commands, for one-off tasks such as `Check`, `Search`, `Require`,
`Locate`, or testing notations in a local context. Prefer calling it right after
imports when possible, or between lemmas/definitions when local declarations,
scopes, or modules are needed. The commands are ephemeral and do not edit the
source file.

The manager infers a workspace by walking upward from the requested file and
using the first directory with `_RocqProject` or `_CoqProject`. If no project
file is found, the file's parent directory is used. Each requested file gets
its own cached `coq-lsp` process, while the process still starts as
`opam exec -- coq-lsp` with `cwd` set to the inferred workspace so local opam
switches select the matching Rocq and `coq-lsp` versions.

Before a check or `run_at` call, the manager tries to detect dune-based Rocq
projects. If
`dune describe workspace` succeeds and `dune rocq top --no-build --toplevel
echo file.v` or `dune coq top --no-build --toplevel echo file.v` identifies the
file, the manager runs `dune rocq top file.v --toplevel echo --display=short` or
the corresponding `dune coq top` command to build dependencies (the `echo`
toplevel loads the file's dependencies without recompiling the file itself).
When this dune
step reports real build actions on stderr, an existing cached
`coq-lsp` process for that file is killed and replaced before checking so
rebuilt dependencies are loaded. Dune prints `Entering directory`/`Leaving
directory` lines to stderr whenever it runs from a theory subdirectory; those
are ignored so an up-to-date build does not restart the session on every call.
If a check call has `clear_cache=True`, the
same restart happens even outside dune projects, so changed `_RocqProject` or
`_CoqProject` files are picked up.
Dune detection and dependency-build subprocesses are serialized within one MCP
server process, so parallel `rocq_check_file` calls from one client do not race
each other for dune's build directory lock. Separate MCP server processes or
manual dune invocations are still outside this in-process lock.

When the soft `coq/getDocument` deadline fires, the response status is
`timeout` and the rendered result includes `timeout_context`. `checked_until`
is taken from the partial `completed.range.end` when cancellation returns a
partial document; if not, it falls back to the start of the latest
`$/coq/fileProgress` processing range. The context also includes
`latest_processing_range` and `next_unchecked` as compact 1-based locations
without exposing raw LSP progress payloads.

When command timeout is enabled, the client watches `$/coq/executionInformation`
notifications during `coq/getDocument`. Each new sentence resets the command
deadline. If the current sentence exceeds the configured limit, the client sends
`$/cancelRequest` and returns `status: "timeout"` with
`timeout_context.timeout_kind: "command"`, the timed-out sentence range, and a
source preview. The `ROCQ_LSP_TIMEOUT` request deadline remains the outer
server-side partial-result deadline for parsing stalls, missing execution
notifications, non-interruptible work, and final-sentence/end-of-file work. The
last sentence in a file is not subject to the per-command watchdog because
`$/coq/executionInformation` marks sentence starts, not sentence completion, so
otherwise end-of-file finalization could be misreported as a timeout in the last
sentence. MCP host cancellation is a harder outer abort: the server sends
`$/cancelRequest` to `coq-lsp` and may reset the file session, but the client may
already have abandoned the result.

`ROCQ_LSP_TIMEOUT_AFTER_ERROR` configures an overall deadline for the rest of
a check once the first error diagnostic has been reported: the whole call is
stopped at most that many seconds after the first error, and the diagnostics
collected so far are returned. It bounds how long a check with a known error
can keep running while still reporting later errors found within the window.
The deadline is absolute — unlike the per-sentence watchdog it never resets,
and it also covers the final sentence and end-of-file finalization, which the
per-sentence watchdog exempts. The normal `ROCQ_LSP_COMMAND_TIMEOUT` watchdog
keeps applying per sentence before and after the error. Cancellations report
`status: "timeout"` with `timeout_context.timeout_kind: "after_error"` and
`timeout_after_error_seconds`. The variable defaults to `10` seconds; `0` or
negative values disable it, and a set-but-empty value falls back to the
default like the unset case. `timeout_after_error_seconds` overrides the
variable for one check with the same convention as `command_timeout_seconds`:
`None` uses the server default, `0` disables it, and positive values enable
it for that call. The one-shot `bens-rocq-mcp check` subcommand disables it
per call the same way it disables the other MCP-oriented timeouts.

`coq-lsp` performance data is enabled with `send_perf_data`. After successful
`coq/getDocument` calls, the manager waits briefly for `$/coq/filePerfData` and
uses sentence execution notifications from the same document version to keep
only commands that ran during the current check step. Entries with
`info.cache_hit: true` are ignored, entries below 80% of
the configured request timeout are ignored, and the rendered result is capped
at 10 long-running commands.

## Run

```bash
uv run bens-rocq-mcp
```

Running `bens-rocq-mcp` with no subcommand starts the MCP server. Running
`bens-rocq-mcp check path/to/file.v` performs one check and prints formatted
JSON.

The server exposes two MCP tools:

- `rocq_check_file(file: str,
  command_timeout_seconds: float | None = None,
  timeout_after_error_seconds: float | None = None,
  clear_cache: bool = False)`
- `rocq_run_at(file: str, commands: str, line: int, character: int = 1)`

`rocq_check_file` returns error diagnostics only. Warning and info
diagnostics are intentionally omitted from this tool response; recompile the
file with Rocq, for example `rocq c path/to/file.v`, when warnings are needed.
`ROCQ_LSP_MESSAGE_LIMIT` caps returned errors, defaults to 10, and is capped to
50. When errors are omitted, the response includes `omitted_counts` and
`message_limit` alongside total `counts`. The limit is applied only while
rendering the MCP response; internal check results retain all normalized
diagnostics.
`ROCQ_LSP_TIMEOUT` controls the soft `coq-lsp` partial-result deadline for
check and `run_at` calls. If it fires before the MCP host's own tool-call
timeout or cancellation, the server cancels the underlying `coq-lsp` request
and returns partial diagnostics, progress context, or partial `run_at` output
when available. It defaults to 55 seconds and may be at most 3600 seconds. Set
it slightly lower than the MCP host tool timeout, leaving room for
`ROCQ_LSP_HARD_TIMEOUT_GRACE`, if callers should receive these partial timeout
results.
`command_timeout_seconds` overrides `ROCQ_LSP_COMMAND_TIMEOUT` for one check:
`None` uses the server default of 5 seconds, `0` disables the sentence
watchdog, and positive values enable it for that call.
`long_running_commands` reports commands that actually ran during the check
and took at least 80% of the configured request timeout.
`clear_cache=True` restarts the file's `coq-lsp` process before checking so
`coq-lsp` refreshes imports from recompiled dependencies and rereads project
configuration.
When `ROCQ_LSP_DUNE_BUILD=1`, `rocq_check_file` also attempts the dune
dependency build described above. If dune is missing, no dune workspace is
detected, or dune does not know the file as a Rocq/Coq toplevel, checking falls
back to normal `coq-lsp` behavior. If dune recognizes the file but the build
fails, the check returns `status: "failed"` with dune's output.

`rocq_run_at` takes a 1-based line and character position in `file`.
`commands` is one string containing one or more complete Rocq commands ending in
`.`; punctuation is not added automatically. It can run `Require` as well as
one-off queries such as `Check`, `Search`, `Locate`, or notation experiments.
The tool returns one rendered result for the full commands string.

## Environment

- `ROCQ_LSP_BINARY` defaults to `coq-lsp`
- `ROCQ_LSP_USE_OPAM` defaults to `1`; set to `0` to run the binary directly
- `ROCQ_LSP_TIMEOUT` is the default soft partial-result deadline; it defaults
  to `55`, is capped at `3600`, and should be set slightly lower than the
  overall MCP tool timeout
- `ROCQ_LSP_HARD_TIMEOUT_GRACE` defaults to `5`
- `ROCQ_LSP_COMMAND_TIMEOUT` defaults to `5`; set `0` to disable per-sentence
  command timeouts by default
- `ROCQ_LSP_TIMEOUT_AFTER_ERROR` defaults to `10`; positive values bound the
  rest of a check to that many seconds after its first error diagnostic, `0`
  or negative values disable it, and an empty value falls back to the
  default
- `ROCQ_LSP_MESSAGE_LIMIT` defaults to `10`; values above `50` are capped
- `ROCQ_LSP_MAX_ERRORS` defaults to `1000000`
- `ROCQ_LSP_WORKSPACE` optionally pins the workspace root
- `ROCQ_LSP_IDLE_TTL_SECONDS` defaults to `1800`
- `ROCQ_LSP_DUNE_BUILD` defaults to `1`; set to `0` to disable automatic dune
  dependency builds before `check`
- `ROCQ_LSP_DUNE_TIMEOUT` defaults to `55`, is capped at `3600`, and limits each
  dune command used for dependency-build detection or execution

Idle sessions older than `ROCQ_LSP_IDLE_TTL_SECONDS` are cleaned up by the MCP
server's background reaper and opportunistically during check calls. Sessions
currently checking, or queued for a check, are not stopped.

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
The Makefile exports `OPAMSWITCH` from `opam switch show` unless it is already
set, so `coq-lsp` processes launched by tests from temporary workspaces use the
same opam switch as the top-level test command. Override it with a Make variable
when needed, for example `make test OPAMSWITCH=my-switch`.

Integration and e2e tests skip themselves when `opam` or `coq-lsp` is
unavailable. E2e scenarios with compiled dependencies also skip when `rocq` is
unavailable.
Scenario check and `run_at` steps may include timing assertions for
cache-sensitive behavior; those timings are asserted by the harness but omitted
from promoted transcripts. Scenario `run_at` steps call `rocq_run_at`
through the same cached manager. Scenarios may also define a top-level
`timeout_policy` to override the harness defaults for timeout-specific coverage,
including `command_seconds` for per-sentence timeout scenarios and
`request_seconds_after_error` for the overall after-error deadline.
Individual check steps may pass `command_timeout_seconds` or the manager-level
`long_running_seconds` threshold to exercise per-call check timeout overrides
and long-running command reporting.
The e2e suite covers error-only check rendering and dense error limits,
`run_at` output info levels for `Search`, `Check`, and `Compute`, `run_at`
caching after slow proofs, soft partial-result timeout recovery, hard-timeout
session reset, per-call check command timeout overrides, per-sentence command
timeout behavior, the overall after-error deadline (a post-error chain of
individually fast sentences stopped at the deadline, disabled at `0` with a
per-call override re-enabling it, completion of files whose errors are
followed by only fast sentences, and cancellation of the otherwise-exempt
final sentence), soft deadlines resuming from cached progress while preserving
diagnostics, cached repeated check errors, long-running command reporting with
cached rechecks ignored, and lemma-header errors that would otherwise cascade
into proof-mode diagnostics.
`tests/e2e/test_check_restart.py` covers refreshing a client file after a
dependency `.vo` is rebuilt.
Use `make promote` to refresh promoted e2e transcripts after intentional output
changes; promotion runs serially because it rewrites golden files.

Use `opam exec -- .venv/bin/python -m pytest ...` for focused test runs outside
of the Makefile. Use `make clean` to remove test artifacts.

## GitLab CI

The GitLab pipeline is defined entirely in `.gitlab-ci.yml`. It runs
`make test` in a matrix over `rocq-core` 9.0.0, 9.1.0, and 9.2.0. Each job uses
the `ocaml/opam:ubuntu-24.04-ocaml-5.2` image, adds the `coq-released` and
`bpeters` opam repositories, installs native apt prerequisites including
`build-essential`, `libgmp-dev`, `m4`, and `pkg-config`, installs the pinned
`rocq-core` version plus unconstrained `rocq-stdlib` and `bens-rocq-lsp`,
creates the repository-local `.venv/`, and installs the Python package with
development test dependencies before running the Makefile target. GitLab caches
pip downloads under `.cache/pip/` per branch and stores opam roots under
`.cache/opam/rocq-${ROCQ_CORE_VERSION}/`, with separate cache keys for each
Rocq version in the matrix. The cached opam root is initialized as a bare root
and uses a `default` switch pinned to `ocaml-base-compiler.5.2.0`; cache reuse
still depends on the GitLab runner having the local cache available.
