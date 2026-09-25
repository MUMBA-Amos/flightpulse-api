from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder

from database.databricks import run_query


router = APIRouter(
    prefix="/flights",
    tags=["Flights"]
)


@router.get("/")
def get_flights():
    return jsonable_encoder(all_flights())


def all_flights():
    """Every flight in the latest data. One saved query, shared by the lookups below."""
    return run_query("""
        SELECT *
        FROM workspace.default.gold_flight_summary
        ORDER BY scheduled_departure DESC
    """)


@router.get("/delayed")
def get_delayed_flights():
    return jsonable_encoder(run_query("""
        SELECT *
        FROM workspace.default.gold_flight_summary
        WHERE departure_delay > 0
           OR arrival_delay > 0
        ORDER BY departure_delay DESC
    """))


@router.get("/{flight_iata}")
def get_flight(flight_iata: str):
    # Filtered from the saved list rather than queried, so looking up made-up
    # flight numbers can't send new queries to Databricks.
    flight_iata = flight_iata.strip().upper()
    return jsonable_encoder([f for f in all_flights() if f["flight_iata"] == flight_iata])
