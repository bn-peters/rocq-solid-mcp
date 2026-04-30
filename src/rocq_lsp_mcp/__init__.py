"""Prototype MCP server for Rocq interaction via rocq-lsp."""

from .manager import RocqLspManager
from .models import CheckResult

__all__ = ["CheckResult", "RocqLspManager"]
