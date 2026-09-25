import re
import threading
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder

from database.databricks import run_query
from services.airports import coordinates_for


router = APIRouter(
    prefix="/aircraft",
    tags=["Aircraft"]
)


# OpenSky positions carry no origin/destination, so routes are looked up by
# callsign: first in our own Aviationstack flights, then in adsbdb.com (free,
# no key). adsbdb knows a callsign's usual route, not necessarily today's.
ADSBDB_URL = "https://api.adsbdb.com/v0/callsign/{}"
ROUTE_CACHE_SECONDS = 24 * 60 * 60
UNKNOWN_CACHE_SECONDS = 60 * 60
CALLSIGN_PATTERN = re.compile(r"^[A-Z0-9]{2,8}$")

_route_cache = {}
_route_cache_lock = threading.Lock()


@router.get("/")
def get_aircraft(
    airborne: Optional[bool] = Query(None, description="true = only aircraft in the air, false = only on the ground"),
    limit: int = Query(500, ge=1, le=2000),
):
    """The most recently seen aircraft with a known position."""
    ground_filter = "" if airborne is None else f"AND on_ground = {'false' if airborne else 'true'}"
    return jsonable_encoder(run_query(f"""
        SELECT *
        FROM workspace.default.silver_aircraft
        WHERE latitude IS NOT NULL
          AND longitude IS NOT NULL
          {ground_filter}
        ORDER BY contact_time DESC
        LIMIT {limit}
    """))


@router.get("/route/{callsign}")
def get_aircraft_route(callsign: str):
    """Origin and destination for an aircraft's callsign, or nulls when unknown."""
    callsign = callsign.strip().upper()
    unknown = {"callsign": callsign, "source": None, "airline": None, "origin": None, "destination": None}

    if not CALLSIGN_PATTERN.match(callsign):
        return unknown

    now = time.monotonic()
    with _route_cache_lock:
        cached = _route_cache.get(callsign)
    if cached and now < cached[0]:
        return cached[1]

    route = _route_from_flights(callsign) or _route_from_adsbdb(callsign)

    if route is None:
        # Don't cache lookup failures, so a brief adsbdb outage isn't remembered.
        return unknown

    ttl = ROUTE_CACHE_SECONDS if route["source"] else UNKNOWN_CACHE_SECONDS
    with _route_cache_lock:
        if len(_route_cache) > 5000:
            _route_cache.clear()
        _route_cache[callsign] = (now + ttl, route)

    return route


def cached_route(callsign):
    """The route this worker has already looked up for a callsign, or None. Never calls Databricks or adsbdb."""
    with _route_cache_lock:
        cached = _route_cache.get(callsign)
    return cached[1] if cached and time.monotonic() < cached[0] else None


def _route_from_flights(callsign):
    rows = run_query("""
        SELECT airline_name,
               departure_airport, departure_iata, departure_icao,
               arrival_airport, arrival_iata, arrival_icao
        FROM workspace.default.gold_flight_summary
        WHERE flight_icao = ?
        LIMIT 1
    """, (callsign,))

    if not rows:
        return None

    f = rows[0]
    coordinates = coordinates_for(f["departure_icao"], f["arrival_icao"])
    return {
        "callsign": callsign,
        "source": "flights",
        "airline": f["airline_name"],
        "origin": _airport(f["departure_iata"], f["departure_icao"], f["departure_airport"], None,
                           *coordinates.get(f["departure_icao"], (None, None))),
        "destination": _airport(f["arrival_iata"], f["arrival_icao"], f["arrival_airport"], None,
                                *coordinates.get(f["arrival_icao"], (None, None))),
    }


def _route_from_adsbdb(callsign):
    """The route from adsbdb, an 'unknown' route if it has none, or None if the lookup failed."""
    try:
        response = httpx.get(ADSBDB_URL.format(callsign), timeout=5)
        data = response.json().get("response")
    except (httpx.HTTPError, ValueError):
        return None

    flightroute = data.get("flightroute") if isinstance(data, dict) else None
    if not flightroute:
        return {"callsign": callsign, "source": None, "airline": None, "origin": None, "destination": None}

    origin = flightroute.get("origin") or {}
    destination = flightroute.get("destination") or {}
    return {
        "callsign": callsign,
        "source": "adsbdb",
        "airline": (flightroute.get("airline") or {}).get("name"),
        "origin": _airport(origin.get("iata_code"), origin.get("icao_code"), origin.get("name"), origin.get("municipality"),
                           origin.get("latitude"), origin.get("longitude")),
        "destination": _airport(destination.get("iata_code"), destination.get("icao_code"), destination.get("name"),
                                destination.get("municipality"), destination.get("latitude"), destination.get("longitude")),
    }


def _airport(iata, icao, name, city, latitude=None, longitude=None):
    if not (iata or icao or name):
        return None
    return {
        "iata": iata,
        "icao": icao,
        "name": name.strip() if name else None,
        "city": city,
        "latitude": latitude,
        "longitude": longitude,
    }
