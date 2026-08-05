from __future__ import annotations

from rocq_lsp_mcp.config import (
    DEFAULT_COMMAND_TIMEOUT_AFTER_ERROR_SECONDS,
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    DEFAULT_MESSAGE_LIMIT,
    MAX_MESSAGE_LIMIT,
    MAX_OVERALL_TIMEOUT_SECONDS,
    load_config,
    rocq_lsp_command,
    rocq_lsp_settings,
)


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
        "ROCQ_LSP_COMMAND_TIMEOUT",
        "ROCQ_LSP_COMMAND_TIMEOUT_AFTER_ERROR",
        "ROCQ_LSP_MESSAGE_LIMIT",
        "ROCQ_LSP_MAX_ERRORS",
        "ROCQ_LSP_WORKSPACE",
        "ROCQ_LSP_DUNE_BUILD",
        "ROCQ_LSP_DUNE_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ROCQ_LSP_USE_OPAM", "0")
    monkeypatch.setenv("ROCQ_LSP_IDLE_TTL_SECONDS", "12.5")

    config = load_config()

    assert config.use_opam is False
    assert config.idle_ttl_seconds == 12.5
    assert config.timeout_policy.command_seconds == DEFAULT_COMMAND_TIMEOUT_SECONDS
    assert (
        config.timeout_policy.command_seconds_after_error
        == DEFAULT_COMMAND_TIMEOUT_AFTER_ERROR_SECONDS
    )
    assert config.message_limit == DEFAULT_MESSAGE_LIMIT
    assert config.dune_build is True


def test_load_config_reads_command_timeout(monkeypatch) -> None:
    monkeypatch.setenv("ROCQ_LSP_COMMAND_TIMEOUT", "2.5")

    config = load_config()

    assert config.timeout_policy.command_seconds == 2.5


def test_load_config_caps_overall_timeout(monkeypatch) -> None:
    monkeypatch.setenv("ROCQ_LSP_TIMEOUT", "9999")

    config = load_config()

    assert config.timeout_policy.request_seconds == MAX_OVERALL_TIMEOUT_SECONDS


def test_load_config_reads_and_caps_message_limit(monkeypatch) -> None:
    monkeypatch.setenv("ROCQ_LSP_MESSAGE_LIMIT", "999")

    config = load_config()

    assert config.message_limit == MAX_MESSAGE_LIMIT


def test_load_config_disables_non_positive_command_timeout(monkeypatch) -> None:
    monkeypatch.setenv("ROCQ_LSP_COMMAND_TIMEOUT", "0")

    config = load_config()

    assert config.timeout_policy.command_seconds is None


def test_load_config_defaults_command_timeout_after_error_to_ten_seconds(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ROCQ_LSP_COMMAND_TIMEOUT_AFTER_ERROR", raising=False)

    config = load_config()

    assert (
        config.timeout_policy.command_seconds_after_error
        == DEFAULT_COMMAND_TIMEOUT_AFTER_ERROR_SECONDS
    )


def test_load_config_reads_command_timeout_after_error(monkeypatch) -> None:
    monkeypatch.setenv("ROCQ_LSP_COMMAND_TIMEOUT_AFTER_ERROR", "2.5")

    config = load_config()

    assert config.timeout_policy.command_seconds_after_error == 2.5


def test_load_config_disables_zero_command_timeout_after_error(monkeypatch) -> None:
    monkeypatch.setenv("ROCQ_LSP_COMMAND_TIMEOUT_AFTER_ERROR", "0")

    config = load_config()

    assert config.timeout_policy.command_seconds_after_error is None


def test_load_config_disables_negative_command_timeout_after_error(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ROCQ_LSP_COMMAND_TIMEOUT_AFTER_ERROR", "-1")

    config = load_config()

    assert config.timeout_policy.command_seconds_after_error is None


def test_load_config_treats_empty_command_timeout_after_error_as_default(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ROCQ_LSP_COMMAND_TIMEOUT_AFTER_ERROR", "")

    config = load_config()

    assert (
        config.timeout_policy.command_seconds_after_error
        == DEFAULT_COMMAND_TIMEOUT_AFTER_ERROR_SECONDS
    )


def test_load_config_reads_dune_build_settings(monkeypatch) -> None:
    monkeypatch.setenv("ROCQ_LSP_DUNE_BUILD", "0")
    monkeypatch.setenv("ROCQ_LSP_DUNE_TIMEOUT", "2.5")

    config = load_config()

    assert config.dune_build is False
    assert config.dune_timeout_seconds == 2.5


def test_rocq_lsp_settings_enable_execution_and_performance_information() -> None:
    assert rocq_lsp_settings(100)["send_execinfo"] is True
    assert rocq_lsp_settings(100)["send_perf_data"] is True
