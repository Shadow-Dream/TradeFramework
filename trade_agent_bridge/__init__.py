"""Narrow integration boundary between TradeEngine and Agent Web."""

from .tool_grants import TOOL_SCOPES, ToolGrantError, ToolGrantStore

__all__ = (
    "TOOL_SCOPES",
    "ToolGrantError",
    "ToolGrantStore",
)
