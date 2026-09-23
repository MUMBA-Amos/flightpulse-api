from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder

from database.databricks import run_query


router = APIRouter(
    prefix="/stats",
    tags=["Stats"]
)


@router.get("/")
def get_stats():
    rows = run_query("""
        SELECT
            (SELECT COUNT(*) FROM workspace.default.gold_flight_summary) AS total_flights,
            (SELECT COUNT(*) FROM workspace.default.gold_airline_performance) AS total_airlines,
            (SELECT COUNT(*) FROM workspace.default.gold_airport_activity) AS total_airports,
            (SELECT COUNT(*) FROM workspace.default.silver_aircraft) AS total_aircraft,
            (SELECT COUNT(*) FROM workspace.default.silver_aircraft WHERE on_ground = false) AS airborne_aircraft
    """)

    return jsonable_encoder(rows[0])
