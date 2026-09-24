from fastapi import FastAPI
from dotenv import load_dotenv
from pathlib import Path

from routes.flights import router as flights_router
from routes.airlines import router as airlines_router
from routes.airports import router as airports_router
from routes.aircraft import router as aircraft_router
from routes.stats import router as stats_router
from routes.weather import router as weather_router
from routes.insights import router as insights_router
from database.databricks import start_refresher
import os


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


app = FastAPI(title="FlightPulse API")

# Keeps the saved Databricks results fresh (see database/databricks.py).
start_refresher()

app.include_router(flights_router)
app.include_router(airlines_router)
app.include_router(airports_router)
app.include_router(aircraft_router)
app.include_router(stats_router)
app.include_router(weather_router)
app.include_router(insights_router)

@app.get("/")
def root():
    return {"message": "FlightPulse API is running"}