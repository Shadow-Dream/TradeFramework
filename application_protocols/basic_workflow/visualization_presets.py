"""Basic Workflow visualization suggestions over explicitly selected DataKeys."""

from __future__ import annotations

import re


_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


def _pane(pane_id, title, role, visualizers):
    return {
        "id": pane_id,
        "title": title,
        "role": role,
        "view": {
            "start": None,
            "end": None,
            "logScale": False,
            "controlsCollapsed": False,
        },
        "visualizers": visualizers,
        "temporaryModules": [],
    }


def build_visualization_preset(
    dataset_id,
    time_zone,
    result_data_keys,
    *,
    period,
    instrument_id,
):
    """Build a conservative preset without guessing a period or instrument."""

    if type(dataset_id) is not str or not dataset_id:
        raise ValueError("Visualization preset dataset_id must be a non-empty string.")
    if type(time_zone) is not str or not time_zone:
        raise ValueError("Visualization preset time_zone must be a non-empty string.")
    if type(result_data_keys) is not dict:
        raise ValueError("Visualization preset Result DataKeys must be an object.")
    for value, label in ((period, "period"), (instrument_id, "instrument_id")):
        if type(value) is not str or not _SEGMENT.fullmatch(value):
            raise ValueError(f"Visualization preset {label} is invalid.")

    close_key = f"price.{period}.{instrument_id}.close"
    panes = []
    market = []
    if close_key in result_data_keys:
        market.append(
            {
                "id": "market-close",
                "callback": "series.line",
                "params": {
                    "dataKey": close_key,
                    "color": "#475569",
                    "lineWidth": 1,
                },
            }
        )
    if market:
        panes.append(_pane("market", "Market", "financial", market))

    portfolio = []
    for identifier, data_key, color in (
        ("account-equity", "portfolio.account.equity", "#2563eb"),
        (
            "account-position",
            f"portfolio.account.positions.{instrument_id}",
            "#0f766e",
        ),
        ("approved-position", f"intent.approved.{instrument_id}", "#7c3aed"),
    ):
        if data_key in result_data_keys:
            portfolio.append(
                {
                    "id": identifier,
                    "callback": "series.line",
                    "params": {"dataKey": data_key, "color": color, "lineWidth": 2},
                }
            )
    if portfolio:
        panes.append(_pane("portfolio", "Portfolio", "indicator", portfolio))

    return {
        "schemaVersion": 3,
        "datasetId": dataset_id,
        "timeZone": time_zone,
        "panes": panes,
    }


__all__ = ("build_visualization_preset",)
