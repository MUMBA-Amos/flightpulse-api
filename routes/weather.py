
from fastapi import APIRouter, HTTPException
import httpx


router = APIRouter(
    prefix="/weather",
    tags=["Weather"]
)


@router.get("/{airport_icao}")
def get_weather(airport_icao: str):
    airport_icao = airport_icao.upper()

    url = "https://aviationweather.gov/api/data/metar"

    params = {
        "ids": airport_icao,
        "format": "json"
    }

    try:
        response = httpx.get(
            url,
            params=params,
            timeout=10
        )

        if response.status_code == 204:
            return {
                "airport_icao": airport_icao,
                "weather": None,
                "message": "No recent METAR available for this airport"
            }

        response.raise_for_status()

        return {
            "airport_icao": airport_icao,
            "weather": response.json()
        }

    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Unable to connect to Aviation Weather Center"
        )

    except httpx.HTTPStatusError:
        raise HTTPException(
            status_code=response.status_code,
            detail="Aviation Weather Center returned an error"
        )

