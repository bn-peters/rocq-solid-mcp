# RocqSolid

[![PyPI](https://img.shields.io/pypi/v/rocq-solid-mcp)](https://pypi.org/project/rocq-solid-mcp/)
[![CI](https://github.com/bn-peters/rocq-solid-mcp/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/bn-peters/rocq-solid-mcp/actions/workflows/ci.yml)

An MCP server for Rocq proof development.

The MCP is deliberately small:
1. `check` generates diagnostics for a given file. It tries to return errors
   from proofs throughout the file. Proof states are cached for efficiency.
2. `run_at` runs a command at a given position in a file. This is useful for
   queries like `Search` or `Check`, and for one-off debugging commands.

## Requirements

*RocqSolid appears to be more stable on OCaml 5 than on OCaml 4. Consider
upgrading if you're experiencing crashes.*

RocqSolid depends on a [patched version](https://github.com/bn-peters/coq-lsp)
of [`coq-lsp`](https://github.com/rocq-community/coq-lsp). It improves the
stability and error reporting of `coq-lsp`, and RocqSolid does not work
without. It is compatible with Rocq 9.0, 9.1, and 9.2. It needs to be installed
in all opam switches RocqSolid should work with:
```bash
opam repo add rocq-solid https://github.com/bn-peters/rocq-solid-mcp-opam.git
opam install rocq-solid-mcp-lsp
```
You might be asked to uninstall `coq-lsp`. Don't worry, `rocq-solid-mcp-lsp` is compatible with standard `coq-lsp`.

## Agent Setup

We recommend using [`uv`](https://docs.astral.sh/uv/), a Python packaging tool.
To teach your favorite agent about RocqSolid:
```bash
claude mcp add --scope user rocq-solid -- uvx rocq-solid-mcp
codex mcp add rocq-solid -- uvx rocq-solid-mcp
```

## Project Setup

The MCP looks for `_CoqProject` and `_RocqProject` files to set up file
dependencies correctly. To generate them in a `dune`-based project, add
`(generate_project_file)` to your `rocq.theory` stanzas:
```
; in theories/dune
(rocq.theory
  (name my-project)
  (generate_project_file)
  (theories Stdlib))
```
You need to link the right opam switch to your development so that RocqSolid
knows which one to use:
```
opam switch link [switch] .
```

## Timeouts

If you have files that compile very slowly, RocqSolid may time out. You can
raise this limit with `ROCQ_LSP_TIMEOUT`.

For Claude, re-register RocqSolid with a higher limit:
```bash
claude mcp remove --scope user rocq-solid
claude mcp add --scope user rocq-solid -e ROCQ_LSP_TIMEOUT=290 -- uvx rocq-solid-mcp
```

For Codex, add the last two options to the existing entry in
`~/.codex/config.toml`:
```toml
[mcp_servers.rocq-solid]
command = "uvx"
args = ["rocq-solid-mcp"]
# add these two options:
tool_timeout_sec = 300
env = { ROCQ_LSP_TIMEOUT = "290" } # slightly lower than tool_timeout_sec
```

## Upgrading

To upgrade RocqSolid:
```
uvx rocq-solid-mcp@latest --help
```
To upgrade `coq-lsp`, run in your switch:
```
opam update && opam upgrade rocq-solid-mcp-lsp
```

## Troubleshooting

RocqSolid needs Python 3.11 or newer. If your default `python3` is older, `uvx`
reports that no solution was found. Use `uvx --python 3.12 rocq-solid-mcp`
instead.

You can check a single file from the command line, which prints the diagnostics
as JSON:
```bash
uvx rocq-solid-mcp check path/to/file.v
```

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


## Support

Join us on the Iris Mattermost!
1. Go to the [GitLab log-in page](https://gitlab.mpi-sws.org/users/sign_in) and select the "GitHub" button, then log in with your GitHub account.
2. Log in to Mattermost; this should use your GitLab login.
3. Join the [RocqSolid MCP](https://mattermost.mpi-sws.org/iris/channels/rocq-solid-mcp) channel.

If you notice that your agent misbehaves with this MCP, please let us know! It
would be particularly helpful if you could share your transcripts.

## Development

In a clone of this repository:
```bash
uv venv .venv && uv pip install -e ".[dev]"
make test
```
`uv tool install -e .` installs a development version of `rocq-solid-mcp` to
`~/.local/bin/`.

To release a new version, bump `version` in `pyproject.toml` and push a tag
`vX.Y.Z`. The `release` GitHub workflow builds the package and publishes it to
PyPI.

To update `rocq-solid-mcp-lsp`, push new version tags to the repository, update the `opam` repository, and push the `opam` repository both got GitHub and GitLab.
