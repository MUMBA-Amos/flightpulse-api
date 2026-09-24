from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder

from database.databricks import run_query


router = APIRouter(
    prefix="/insights",
    tags=["Insights"]
)


# Airports whose departures are collected daily for delay insights (IATA codes).
# Built by the gold notebook from silver_flights_history; see gold_insights_*.
TRACKED_AIRPORTS = {
    "KUL": "Kuala Lumpur International",
    "PEN": "Penang International",
}

# name in the response -> (gold table, sort order)
TABLES = {
    "airlines": ("gold_insights_by_airline", "on_time_pct DESC, flights DESC"),
    "hours": ("gold_insights_by_hour", "local_hour"),
    "routes": ("gold_insights_by_route", "flights DESC"),
    "daily": ("gold_insights_daily", "flight_date"),
    "delay_bands": ("gold_insights_delay_bands", "delay_band"),
}


@router.get("/")
def get_tracked_airports():
    """The airports with delay insights, with each one's headline figures (null until data exists)."""
    summaries = {row["airport"]: row for row in _rows("gold_insights_summary", "airport")}
    return jsonable_encoder([
        {"airport": code, "name": name, "summary": summaries.get(code)}
        for code, name in TRACKED_AIRPORTS.items()
    ])


@router.get("/{airport}")
def get_airport_insights(airport: str):
    """Everything the Insights page shows for one airport, from the gold insight tables."""
    airport = airport.strip().upper()
    if airport not in TRACKED_AIRPORTS:
        raise HTTPException(status_code=404, detail=f"No delay insights for {airport}")

    summary = _rows("gold_insights_summary", "airport", airport)
    result = {
        "airport": airport,
        "name": TRACKED_AIRPORTS[airport],
        "summary": summary[0] if summary else None,
    }
    for name, (table, order) in TABLES.items():
        result[name] = _rows(table, order, airport)

    return jsonable_encoder(result)


def _rows(table, order, airport=None):
    """Rows of a gold insight table; empty if the pipeline hasn't created it yet."""
    where = "WHERE airport = ?" if airport else ""
    params = (airport,) if airport else None
    try:
        return run_query(f"""
            SELECT *
            FROM workspace.default.{table}
            {where}
            ORDER BY {order}
        """, params)
    except Exception as error:
        if "TABLE_OR_VIEW_NOT_FOUND" in str(error):
            return []
        raise
