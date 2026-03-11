"""Chase Travel data via Chrome extension capture.

Chase uses Kasada anti-bot which blocks all browser automation.
The workaround: a Chrome extension captures API responses while the user
browses chase.com/travel normally, then `vplan chase import` reads the file.

Data flow:
  1. User installs chase_extension/ in Chrome
  2. User browses chase.com/travel, logs in, searches flights/hotels
  3. Extension saves captures to chase_capture.json (downloaded via popup)
  4. User moves file to ~/.vplan/chase_capture.json
  5. `vplan chase import` reads and normalizes the data
"""

from __future__ import annotations

import json
import sys
from typing import Any

from vplan_cli.config import CHASE_CAPTURE_PATH, load_chase_capture

API_URL_SIGNALS = [
    "/api/", "/search", "/flight", "/hotel", "/offer",
    "/availability", "/pricing", "cxloyalty",
]


def _is_travel_api(url: str) -> bool:
    url_lower = url.lower()
    return any(sig in url_lower for sig in API_URL_SIGNALS)


def import_chase_captures(filepath: str = "") -> dict[str, Any]:
    """Read chase_capture.json and normalize flight/hotel results."""
    if filepath:
        try:
            with open(filepath) as f:
                raw_captures = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return {"error": f"Failed to read {filepath}: {e}", "flights": [], "hotels": []}
    else:
        raw_captures = load_chase_capture()

    if not raw_captures:
        return {
            "error": "No captures found. Browse chase.com/travel with the extension, then download chase_capture.json to ~/.vplan/",
            "flights": [],
            "hotels": [],
        }

    flights: list[dict[str, Any]] = []
    hotels: list[dict[str, Any]] = []
    api_count = 0

    for capture in raw_captures:
        url = capture.get("url", "")
        data = capture.get("data")
        if not data or not _is_travel_api(url):
            continue
        api_count += 1
        _extract_from_body(url, data, flights, hotels)

    return {
        "total_captures": len(raw_captures),
        "api_responses": api_count,
        "flights": flights,
        "hotels": hotels,
    }


def _extract_from_body(url: str, body: Any, flights: list[dict[str, Any]], hotels: list[dict[str, Any]]) -> None:
    if not isinstance(body, (dict, list)):
        return

    items: list[dict[str, Any]] = []
    if isinstance(body, list):
        items = [i for i in body if isinstance(i, dict)]
    else:
        for key in ["results", "data", "offers", "flights", "hotels",
                     "searchResults", "flightResults", "hotelResults",
                     "itineraries", "listings", "items", "response"]:
            val = body.get(key)
            if isinstance(val, list):
                items = [i for i in val if isinstance(i, dict)]
                if items:
                    break
        if not items and isinstance(body.get("data"), dict):
            inner = body["data"]
            for key in ["results", "offers", "flights", "hotels", "items"]:
                val = inner.get(key)
                if isinstance(val, list):
                    items = [i for i in val if isinstance(i, dict)]
                    if items:
                        break
        if not items:
            items = [body]

    for item in items:
        normalized = _normalize(item, url)
        if normalized:
            if normalized["type"] == "flight":
                flights.append(normalized)
            else:
                hotels.append(normalized)


def _normalize(item: dict[str, Any], url: str) -> dict[str, Any] | None:
    flight_signals = {"airline", "carrier", "flightNumber", "flight_number",
                      "departureTime", "departure", "arrivalTime", "arrival",
                      "departsAt", "arrivesAt", "origin", "destination",
                      "segments", "legs", "slices"}
    hotel_signals = {"hotel", "hotelName", "hotel_name", "property",
                     "propertyName", "checkIn", "check_in", "roomRate",
                     "room_rate", "nightly", "nightlyRate"}

    keys = set(item.keys())
    if keys & flight_signals:
        return _normalize_flight(item, url)
    elif keys & hotel_signals:
        return _normalize_hotel(item, url)
    return None


def _normalize_flight(raw: dict[str, Any], url: str) -> dict[str, Any]:
    points_price = (
        raw.get("pointsPrice") or raw.get("rewardCost") or raw.get("pointsCost") or
        raw.get("points") or raw.get("rewardPoints") or raw.get("pointsRequired") or
        raw.get("miles") or raw.get("loyaltyPoints") or 0
    )
    cash_price = (
        raw.get("totalPrice") or raw.get("price") or raw.get("cashPrice") or
        raw.get("total") or raw.get("fareTotal") or 0
    )
    if isinstance(cash_price, (int, float)) and cash_price > 10000:
        cash_price = cash_price / 100

    origin = raw.get("origin") or raw.get("departureAirport") or raw.get("originCode") or raw.get("from") or ""
    dest = raw.get("destination") or raw.get("arrivalAirport") or raw.get("destinationCode") or raw.get("to") or ""
    carrier = raw.get("carrier") or raw.get("airline") or raw.get("carriers") or raw.get("marketingCarrier") or ""
    departure = raw.get("departureTime") or raw.get("departsAt") or raw.get("departure") or ""
    arrival = raw.get("arrivalTime") or raw.get("arrivesAt") or raw.get("arrival") or ""

    for field in (origin, dest, carrier):
        if isinstance(field, dict):
            field = field.get("code", field.get("iata", str(field)))

    stops = raw.get("stops", raw.get("numStops", raw.get("connections", 0)))
    if isinstance(stops, list):
        stops = len(stops)

    duration = raw.get("duration", raw.get("totalDuration", raw.get("flightDuration", "")))
    if isinstance(duration, (int, float)) and duration > 0:
        h, m = divmod(int(duration), 60)
        duration = f"{h}h {m}m"

    return {
        "type": "flight",
        "source": "chase_travel",
        "origin": str(origin),
        "destination": str(dest),
        "carrier": str(carrier),
        "departure": str(departure),
        "arrival": str(arrival),
        "stops": int(stops) if isinstance(stops, (int, float)) else 0,
        "duration": str(duration),
        "cash_price_usd": round(float(cash_price), 2) if cash_price else 0,
        "points_price": int(points_price) if points_price else 0,
        "ur_points": int(points_price) if points_price else 0,
        "raw_url": url[:200],
    }


def _normalize_hotel(raw: dict[str, Any], url: str) -> dict[str, Any]:
    name = (
        raw.get("hotelName") or raw.get("hotel_name") or raw.get("propertyName") or
        raw.get("name") or
        (raw.get("hotel", {}).get("name", "") if isinstance(raw.get("hotel"), dict) else raw.get("hotel", ""))
    )
    nightly = raw.get("nightlyRate") or raw.get("nightly") or raw.get("averageNightlyRate") or raw.get("rate") or 0
    total = raw.get("totalPrice") or raw.get("total") or raw.get("price") or 0
    points = raw.get("points") or raw.get("rewardPoints") or raw.get("pointsRequired") or raw.get("pointsCost") or 0
    rating = raw.get("rating") or raw.get("starRating") or raw.get("stars") or 0

    if isinstance(nightly, (int, float)) and nightly > 10000:
        nightly = nightly / 100
    if isinstance(total, (int, float)) and total > 100000:
        total = total / 100

    return {
        "type": "hotel",
        "source": "chase_travel",
        "name": str(name),
        "rating": float(rating) if rating else 0,
        "nightly_usd": round(float(nightly), 2) if nightly else 0,
        "total_usd": round(float(total), 2) if total else 0,
        "points_price": int(points) if points else 0,
        "ur_points": int(points) if points else 0,
        "raw_url": url[:200],
    }
