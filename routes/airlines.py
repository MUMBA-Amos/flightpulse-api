from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder

from database.databricks import run_query


router = APIRouter(
    prefix="/airlines",
    tags=["Airlines"]
)


@router.get("/")
def get_airlines():
    return jsonable_encoder(_all_airlines())


@router.get("/{airline_iata}")
def get_airline(airline_iata: str):
    # Filtered from the saved list, so arbitrary codes can't send new queries to Databricks.
    airline_iata = airline_iata.strip().upper()
    return jsonable_encoder([a for a in _all_airlines() if a["airline_iata"] == airline_iata])


def _all_airlines():
    return run_query("""
        SELECT *
        FROM workspace.default.gold_airline_performance
        ORDER BY total_flights DESC
    """)
