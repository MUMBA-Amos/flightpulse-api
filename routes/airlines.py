from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder

from database.databricks import run_query


router = APIRouter(
    prefix="/airlines",
    tags=["Airlines"]
)


@router.get("/")
def get_airlines():
    return jsonable_encoder(run_query("""
        SELECT *
        FROM workspace.default.gold_airline_performance
        ORDER BY total_flights DESC
    """))


@router.get("/{airline_iata}")
def get_airline(airline_iata: str):
    return jsonable_encoder(run_query("""
        SELECT *
        FROM workspace.default.gold_airline_performance
        WHERE airline_iata = ?
    """, (airline_iata.upper(),)))
