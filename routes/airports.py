from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder

from database.databricks import run_query


router = APIRouter(
    prefix="/airports",
    tags=["Airports"]
)


@router.get("/")
def get_airports():
    return jsonable_encoder(run_query("""
        SELECT *
        FROM workspace.default.gold_airport_activity
        ORDER BY total_activity DESC
    """))


@router.get("/{airport_iata}")
def get_airport(airport_iata: str):
    return jsonable_encoder(run_query("""
        SELECT *
        FROM workspace.default.gold_airport_activity
        WHERE airport_iata = ?
    """, (airport_iata.upper(),)))
