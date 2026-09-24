from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder

from database.databricks import run_query
from services.airports import coordinates_for


router = APIRouter(
    prefix="/insights",
    tags=["Insights"]
)


# Airports whose departures are collected daily for delay insights, by IATA code.
# Built by the gold notebook from silver_flights_history; see gold_insights_*.
TRACKED_AIRPORTS = {
    "KUL": {"name": "Kuala Lumpur International", "icao": "WMKK"},
    "PEN": {"name": "Penang International", "icao": "WMKP"},
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
        {"airport": code, "name": info["name"], "summary": summaries.get(code)}
        for code, info in TRACKED_AIRPORTS.items()
    ])


@router.get("/{airport}")
def get_airport_insights(airport: str):
    """Everything the Insights page shows for one airport, from the gold insight tables."""
    airport = airport.strip().upper()
    if airport not in TRACKED_AIRPORTS:
        raise HTTPException(status_code=404, detail=f"No delay insights for {airport}")

    info = TRACKED_AIRPORTS[airport]
    summary = _rows("gold_insights_summary", "airport", airport)
    result = {
        "airport": airport,
        "name": info["name"],
        "summary": summary[0] if summary else None,
    }
    for name, (table, order) in TABLES.items():
        result[name] = _rows(table, order, airport)

    _add_coordinates(result, info["icao"])
    return jsonable_encoder(result)


def _add_coordinates(result, airport_icao):
    """Latitude/longitude for the airport and each route's destination, for the route map."""
    # The route table has IATA codes; the coordinate lookup needs ICAO.
    try:
        rows = run_query("""
            SELECT arrival_iata, first(arrival_icao, true) AS arrival_icao
            FROM workspace.default.silver_flights_history
            WHERE departure_iata = ? AND arrival_iata IS NOT NULL
            GROUP BY arrival_iata
        """, (result["airport"],))
    except Exception as error:
        if "TABLE_OR_VIEW_NOT_FOUND" not in str(error):
            raise
        rows = []
    icao_by_iata = {row["arrival_iata"]: row["arrival_icao"] for row in rows}
    coordinates = coordinates_for(airport_icao, *icao_by_iata.values())

    latitude, longitude = coordinates.get(airport_icao, (None, None))
    result["latitude"], result["longitude"] = latitude, longitude
    for route in result["routes"]:
        route["latitude"], route["longitude"] = coordinates.get(icao_by_iata.get(route["arrival_iata"]), (None, None))


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
