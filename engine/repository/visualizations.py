"""Authoritative database repository for saved Result Visualizations."""

from __future__ import annotations

from engine.contracts import strict_json
from engine.contracts import visualization as visualization_contracts
from engine.contracts.exact_fields import require_exact_fields
from engine.control import database as engine_database


_VISUALIZATION_ROW_FIELDS = frozenset({
    "visualization_id",
    "backtest_id",
    "name",
    "created_at",
    "spec_json",
})
_VISUALIZATION_SELECT = """
    SELECT visualization_id, backtest_id, name, created_at, spec_json
    FROM visualizations
"""


def decode_visualization_row(row):
    """Decode one exact physical row into the public durable record."""

    if not hasattr(row, "keys"):
        raise ValueError("Visualization database row must be an object.")
    material = {key: row[key] for key in row.keys()}
    require_exact_fields(
        material,
        allowed=_VISUALIZATION_ROW_FIELDS,
        required=_VISUALIZATION_ROW_FIELDS,
        label="Visualization database row",
    )
    for field in _VISUALIZATION_ROW_FIELDS:
        if not isinstance(material[field], str):
            raise ValueError(
                f"Visualization database field '{field}' must be a string."
            )
    try:
        spec = strict_json.loads(material["spec_json"])
    except ValueError as exc:
        raise ValueError("Visualization contains invalid stored JSON.") from exc
    if not isinstance(spec, dict):
        raise ValueError("Visualization stored spec must be an object.")
    return visualization_contracts.require_record({
        "visualizationId": material["visualization_id"],
        "backtestId": material["backtest_id"],
        "name": material["name"],
        "createdAt": material["created_at"],
        "spec": spec,
    })


def save_visualization(config, record):
    """Atomically update the Backtest current spec and upsert its saved record."""

    visualization_contracts.require_record(record)
    spec_json = strict_json.dumps(record["spec"], sort_keys=True)
    with engine_database.connect_database(config) as connection:
        try:
            cursor = connection.execute(
                "UPDATE backtests SET visualization_json = ? "
                "WHERE backtest_id = ?",
                (spec_json, record["backtestId"]),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Unknown backtest: {record['backtestId']}")
            connection.execute(
                """
                INSERT OR REPLACE INTO visualizations
                (visualization_id, backtest_id, name, created_at, spec_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record["visualizationId"],
                    record["backtestId"],
                    record["name"],
                    record["createdAt"],
                    spec_json,
                ),
            )
            row = connection.execute(
                _VISUALIZATION_SELECT + " WHERE visualization_id = ?",
                (record["visualizationId"],),
            ).fetchone()
            if row is None:
                raise RuntimeError("Visualization upsert did not create its record.")
            saved = decode_visualization_row(row)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return saved


def get_visualization(config, visualization_id):
    if not isinstance(visualization_id, str) or not visualization_id:
        raise ValueError("Visualization ID must be a non-empty string.")
    with engine_database.connect_database(config) as connection:
        row = connection.execute(
            _VISUALIZATION_SELECT + " WHERE visualization_id = ?",
            (visualization_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown visualization: {visualization_id}")
    return decode_visualization_row(row)


def list_visualizations(config, backtest_id=""):
    if not isinstance(backtest_id, str):
        raise ValueError("Visualization backtestId filter must be a string.")
    sql = _VISUALIZATION_SELECT
    parameters = ()
    if backtest_id:
        sql += " WHERE backtest_id = ?"
        parameters = (backtest_id,)
    sql += " ORDER BY created_at DESC"
    with engine_database.connect_database(config) as connection:
        rows = connection.execute(sql, parameters).fetchall()
    return [decode_visualization_row(row) for row in rows]


__all__ = (
    "decode_visualization_row",
    "get_visualization",
    "list_visualizations",
    "save_visualization",
)
