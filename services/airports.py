import threading

import httpx


# Airport details from the Aviation Weather Center (free, no key): position,
# elevation, runways and radio frequencies. Airports rarely change, so each one
# is fetched once and kept for the life of the process.
AIRPORT_URL = "https://aviationweather.gov/api/data/airport"
BATCH_SIZE = 50

_airports = {}
_lock = threading.Lock()


def coordinates_for(*icao_codes):
    """{icao: (latitude, longitude)} for the given airports; unknown airports are left out."""
    return {
        code: (airport["latitude"], airport["longitude"])
        for code, airport in details_for(*icao_codes).items()
        if airport["latitude"] is not None and airport["longitude"] is not None
    }


def details_for(*icao_codes):
    """{icao: airport details} for the given airports; unknown airports are left out."""
    wanted = list(dict.fromkeys(code for code in icao_codes if code))
    with _lock:
        missing = [code for code in wanted if code not in _airports]

    for start in range(0, len(missing), BATCH_SIZE):
        batch = missing[start:start + BATCH_SIZE]
        try:
            response = httpx.get(AIRPORT_URL, params={"ids": ",".join(batch), "format": "json"}, timeout=10)
            airports = response.json() if response.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            airports = []
        with _lock:
            for airport in airports:
                if airport.get("icaoId"):
                    _airports[airport["icaoId"]] = _details(airport)

    with _lock:
        return {code: _airports[code] for code in wanted if code in _airports}


def _details(airport):
    return {
        "icao": airport["icaoId"],
        "iata": airport.get("iataId"),
        "name": airport.get("name"),
        "latitude": airport.get("lat"),
        "longitude": airport.get("lon"),
        # Metres in the source; feet is what pilots and simulators use.
        "elevation_ft": round(airport["elev"] * 3.28084) if isinstance(airport.get("elev"), (int, float)) else None,
        "runways": [
            {"id": runway["id"], "dimension": runway.get("dimension"), "surface": runway.get("surface")}
            for runway in airport.get("runways") or []
            if runway.get("id")
        ],
        "frequencies": _frequencies(airport.get("freqs")),
    }


def _frequencies(freqs):
    """"ATIS,128.05;TWR,118.8" -> [{"name": "ATIS", "mhz": "128.05"}, ...]"""
    result = []
    for part in (freqs or "").split(";"):
        name, _, mhz = part.partition(",")
        if name.strip() and mhz.strip():
            result.append({"name": name.strip(), "mhz": mhz.strip()})
    return result
