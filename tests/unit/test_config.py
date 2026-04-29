from __future__ import annotations

from rocq_lsp_mcp.config import load_config, rocq_lsp_command


def test_rocq_lsp_command_defaults_to_opam_exec() -> None:
    assert rocq_lsp_command("coq-lsp", use_opam=True) == (
        "opam",
        "exec",
        "--",
        "coq-lsp",
    )


def test_rocq_lsp_command_can_run_directly() -> None:
    assert rocq_lsp_command("coq-lsp", use_opam=False) == ("coq-lsp",)


def test_load_config_reads_opam_and_idle_ttl(monkeypatch) -> None:
    for name in (
        "ROCQ_LSP_BINARY",
        "ROCQ_LSP_TIMEOUT",
        "ROCQ_LSP_HARD_TIMEOUT_GRACE",
        "ROCQ_LSP_MAX_ERRORS",
        "ROCQ_LSP_WORKSPACE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ROCQ_LSP_USE_OPAM", "0")
    monkeypatch.setenv("ROCQ_LSP_IDLE_TTL_SECONDS", "12.5")

    config = load_config()

    assert config.use_opam is False
    assert config.idle_ttl_seconds == 12.5
