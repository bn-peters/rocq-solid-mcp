"""Prototype MCP server for Rocq interaction via rocq-lsp."""

from .manager import RocqLspManager
from .models import CompileResult

__all__ = ["CompileResult", "RocqLspManager"]
