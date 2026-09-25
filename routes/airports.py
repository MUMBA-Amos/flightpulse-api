from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder

from database.databricks import run_query


router = APIRouter(
    prefix="/airports",
    tags=["Airports"]
)


@router.get("/")
def get_airports():
    return jsonable_encoder(_all_airports())


@router.get("/{airport_iata}")
def get_airport(airport_iata: str):
    # Filtered from the saved list, so arbitrary codes can't send new queries to Databricks.
    airport_iata = airport_iata.strip().upper()
    return jsonable_encoder([a for a in _all_airports() if a["airport_iata"] == airport_iata])


def _all_airports():
    return run_query("""
        SELECT *
        FROM workspace.default.gold_airport_activity
        ORDER BY total_activity DESC
    """)
