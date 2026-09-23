from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder

from database.databricks import run_query


router = APIRouter(
    prefix="/flights",
    tags=["Flights"]
)


@router.get("/")
def get_flights():
    return jsonable_encoder(run_query("""
        SELECT *
        FROM workspace.default.gold_flight_summary
        LIMIT 50
    """))


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
    return jsonable_encoder(run_query("""
        SELECT *
        FROM workspace.default.gold_flight_summary
        WHERE flight_iata = ?
    """, (flight_iata.upper(),)))
