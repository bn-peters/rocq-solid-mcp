"""RocqSolid: an MCP server for Rocq proof development via rocq-lsp."""

from .manager import RocqLspManager
from .models import CheckResult

__all__ = ["CheckResult", "RocqLspManager"]
