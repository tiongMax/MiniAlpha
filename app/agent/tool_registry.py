"""Validated registry and intent groups for MiniAlpha's financial tools."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from langchain_core.tools import BaseTool


@dataclass(frozen=True, slots=True)
class ToolGroup:
    """One application-owned research capability and its tool names."""

    name: str
    tool_names: tuple[str, ...]


TOOL_GROUPS: tuple[ToolGroup, ...] = (
    ToolGroup("company_overview", ("get_company_overview",)),
    ToolGroup("price_history", ("get_price_history",)),
    ToolGroup("financial_statements", ("get_financial_statements",)),
    ToolGroup("fundamental_ratios", ("get_fundamental_ratios",)),
    ToolGroup("analyst_estimates", ("get_analyst_estimates",)),
    ToolGroup("sec_filings", ("get_sec_filings",)),
    ToolGroup("ownership", ("get_ownership",)),
    ToolGroup("insider_activity", ("get_insider_activity",)),
    ToolGroup("company_news", ("get_company_news",)),
    ToolGroup("company_comparison", ("compare_companies",)),
    ToolGroup("return_statistics", ("calculate_return_statistics",)),
    ToolGroup("volatility", ("calculate_volatility",)),
    ToolGroup("drawdown", ("analyze_drawdowns",)),
    ToolGroup("correlation", ("calculate_correlations",)),
    ToolGroup("technical_indicators", ("calculate_technical_indicators",)),
    ToolGroup("moving_average_backtest", ("backtest_moving_average",)),
)

TOOL_NAMES_BY_GROUP: dict[str, tuple[str, ...]] = {
    group.name: group.tool_names for group in TOOL_GROUPS
}


class ToolRegistry:
    """Resolve a request-scoped tool subset without recreating tool objects."""

    def __init__(self, tools: Sequence[BaseTool]) -> None:
        by_name: dict[str, BaseTool] = {}
        for tool in tools:
            if not tool.name:
                raise ValueError("Every registered tool requires a name.")
            if tool.name in by_name:
                raise ValueError(f"Duplicate tool name: {tool.name}")
            by_name[tool.name] = tool
        self._tools = tuple(tools)
        self._by_name = by_name

    @property
    def all(self) -> tuple[BaseTool, ...]:
        """Return tools in stable production registration order."""
        return self._tools

    @property
    def names(self) -> tuple[str, ...]:
        """Return registered names in stable production order."""
        return tuple(tool.name for tool in self._tools)

    def resolve(self, names: Iterable[str]) -> tuple[BaseTool, ...]:
        """Resolve unique names while retaining production registration order."""
        selected = set(names)
        unknown = selected.difference(self._by_name)
        if unknown:
            raise ValueError(f"Unknown tool names: {', '.join(sorted(unknown))}")
        return tuple(tool for tool in self._tools if tool.name in selected)

    def names_for_groups(self, groups: Iterable[str]) -> tuple[str, ...]:
        """Resolve application-owned intent groups to registered tool names."""
        selected: set[str] = set()
        for group in groups:
            try:
                selected.update(TOOL_NAMES_BY_GROUP[group])
            except KeyError as error:
                raise ValueError(f"Unknown tool group: {group}") from error
        return tuple(name for name in self.names if name in selected)
