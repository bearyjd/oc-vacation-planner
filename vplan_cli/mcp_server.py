"""MCP server exposing vplan vacation planning tools and resources."""

from __future__ import annotations

import json

import requests

from mcp.server.fastmcp import FastMCP

from vplan_cli.config import (
    DEFAULT_UA,
    FAMILY,
    POINTS,
    SWEET_SPOTS,
    load_watchlist,
    save_watchlist,
    list_trips,
    load_trip,
    save_trip,
    delete_trip,
    update_config,
)
from vplan_cli.data_sources import (
    HYATT_CATEGORIES,
    calculate_redemption,
    family_suitability,
    fetch_weather,
    generate_itinerary,
    lookup_awards,
    lookup_visa,
    scrape_wikivoyage,
    search_hotels_liteapi,
)
from vplan_cli.routes import NONSTOP_ROUTES, get_all_nonstop_codes

mcp = FastMCP(
    "vplan",
    instructions=(
        "Vacation planning tools for the Beary family (2 adults + 3 kids). "
        "Use these tools to search award flights, research destinations, "
        "compare options, check visa requirements, calculate points redemption, "
        "and manage a travel watchlist. The family flies from IAD/DCA and has "
        "552k Chase UR, 778k United miles (Premier 1K), and 731k Delta SkyMiles."
    ),
)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA})
    return s


# ---------------------------------------------------------------------------
# Resources — read-only data the LLM can reference
# ---------------------------------------------------------------------------


@mcp.resource("vplan://config/family")
def resource_family() -> str:
    return json.dumps(FAMILY, indent=2)


@mcp.resource("vplan://config/points")
def resource_points() -> str:
    return json.dumps(POINTS, indent=2)


@mcp.resource("vplan://config/sweet-spots")
def resource_sweet_spots() -> str:
    return json.dumps(SWEET_SPOTS, indent=2)


@mcp.resource("vplan://routes/{airport}")
def resource_routes(airport: str) -> str:
    airport = airport.upper()
    if airport not in NONSTOP_ROUTES:
        return json.dumps({"error": f"No routes for {airport}", "available": list(NONSTOP_ROUTES.keys())})
    return json.dumps({airport: NONSTOP_ROUTES[airport]}, indent=2)


@mcp.resource("vplan://watchlist")
def resource_watchlist() -> str:
    return json.dumps(load_watchlist(), indent=2)


@mcp.resource("vplan://trips")
def resource_trips() -> str:
    return json.dumps(list_trips(), indent=2)


@mcp.resource("vplan://trips/{slug}")
def resource_trip_detail(slug: str) -> str:
    data = load_trip(slug)
    if data is None:
        return json.dumps({"error": f"Trip '{slug}' not found"})
    return json.dumps(data, indent=2)


@mcp.resource("vplan://hyatt-chart")
def resource_hyatt_chart() -> str:
    return json.dumps(HYATT_CATEGORIES, indent=2)


# ---------------------------------------------------------------------------
# Tools — actions the LLM can perform
# ---------------------------------------------------------------------------


@mcp.tool()
def research_destination(destination: str, month: str = "", nights: int = 7) -> dict:
    """Research a destination: overview from Wikivoyage, weather, family suitability tips."""
    s = _session()
    result = {
        "destination": destination,
        "nights": nights,
        "month": month,
        "family": f"{FAMILY['adults']} adults + {len(FAMILY['kids'])} kids",
    }

    sections = scrape_wikivoyage(destination, s)
    if sections:
        result["overview"] = sections.get("Overview", sections.get("Understand", ""))[:1000]
        for key in ["See", "Do", "Eat", "Get in", "Stay safe"]:
            if key in sections:
                result[key.lower().replace(" ", "_")] = sections[key][:500]
    else:
        result["overview"] = f"Could not fetch guide for {destination}."

    if month:
        result["weather"] = fetch_weather(destination, month, s)

    result["family_tips"] = family_suitability(destination, sections)
    return result


@mcp.tool()
def check_visa(country: str) -> dict:
    """Check visa and entry requirements for a country (US passport holders)."""
    return lookup_visa(country)


@mcp.tool()
def search_awards(origin: str = "IAD", destination: str = "", month: str = "") -> dict:
    """Search static award chart for flights. Returns miles costs for United, Delta, and Chase UR transfer options."""
    return lookup_awards(origin, destination, month)


@mcp.tool()
def calculate_points(hotel_rate_usd: float = 0, flights_usd: float = 0) -> list:
    """Calculate optimal points/miles redemption strategies for a trip given cash prices."""
    return calculate_redemption(hotel_rate_usd, flights_usd)


@mcp.tool()
def get_weather(destination: str, month: str) -> str:
    """Get historical weather data for a destination in a specific month (avg high/low, rainfall)."""
    return fetch_weather(destination, month, _session())


@mcp.tool()
def create_itinerary(destination: str, nights: int = 7, kid_ages: str = "8,10,14") -> dict:
    """Generate a day-by-day family itinerary for a destination."""
    ages = [int(a.strip()) for a in kid_ages.split(",") if a.strip()]
    return generate_itinerary(destination, nights, ages)


@mcp.tool()
def search_hotels(city: str, country_code: str = "", checkin: str = "", checkout: str = "") -> dict:
    """Search hotels with Hyatt award chart and optional live pricing via LiteAPI."""
    result = {"city": city, "hyatt_award_chart": [], "live_hotels": [], "search_links": []}

    for cat, info in HYATT_CATEGORIES.items():
        result["hyatt_award_chart"].append({
            "category": cat,
            "points_per_night": info["points"],
        })

    if checkin and checkout:
        live = search_hotels_liteapi(city, country_code, checkin, checkout)
        result["live_hotels"] = live
        result["search_links"] = [
            f"https://www.hyatt.com/search/{city}?checkinDate={checkin}&checkoutDate={checkout}",
            f"https://www.google.com/travel/hotels/{city}?dates={checkin}+to+{checkout}",
        ]

    return result


@mcp.tool()
def search_flights_live(origin: str = "IAD", destination: str = "", cabin: str = "economy", limit: int = 50) -> list:
    """Search live award flight availability via seats.aero. Takes 15-30 seconds. Returns flights with miles cost, taxes, stops, dates, flight numbers."""
    from vplan_cli.scraper_seats import SeatsAeroScraper

    with SeatsAeroScraper(headless=True) as scraper:
        return scraper.search_flights(origin, destination, cabin, limit)


@mcp.tool()
def search_round_trip_live(origin: str = "IAD", destination: str = "", cabin: str = "economy", limit: int = 50) -> dict:
    """Search both outbound and return award flights via seats.aero. Takes 30-60 seconds."""
    from vplan_cli.scraper_seats import search_round_trip

    return search_round_trip(origin, destination, cabin, limit)


@mcp.tool()
def get_nonstop_destinations(airport: str = "IAD") -> list:
    """Get all nonstop destination airport codes from a given airport."""
    return get_all_nonstop_codes(airport.upper())


@mcp.tool()
def compare_destinations(destinations: str, month: str = "", origin: str = "IAD") -> list:
    """Compare multiple destinations side-by-side. Pass comma-separated destination names (e.g. 'Cancun,Jamaica,Costa Rica')."""
    from vplan_cli.advisor import DESTINATION_CODES, _gather_destination_context

    dest_list = [d.strip() for d in destinations.split(",") if d.strip()]
    results = []
    for dest in dest_list:
        code = DESTINATION_CODES.get(dest.lower(), dest.upper() if len(dest) == 3 else "")
        ctx = _gather_destination_context(dest, code, origin, month or None, live=False)
        results.append(ctx)
    return results


@mcp.tool()
def ask_advisor(query: str, live: bool = False, model: str = "gpt-4o-mini") -> str:
    """Ask the AI travel advisor a natural language question. Returns a complete text response (not streamed)."""
    from vplan_cli.advisor import ask

    chunks = []
    for chunk in ask(query, live=live, model=model, verbose=False):
        chunks.append(chunk)
    return "".join(chunks)


@mcp.tool()
def multicity_search(stops: str, cabin: str = "economy") -> dict:
    """Search multi-city award routing. Pass comma-separated airport codes (e.g. 'IAD,LHR,BCN,IAD'). Includes United Excursionist Perk eligibility."""
    from vplan_cli.data_sources import REGION_MAP

    codes = [c.strip().upper() for c in stops.split(",") if c.strip()]
    if len(codes) < 3:
        return {"error": "Need at least 3 airport codes"}

    segments = [(codes[i], codes[i + 1]) for i in range(len(codes) - 1)]

    regions = set()
    for orig, dest in segments:
        r = REGION_MAP.get(dest, REGION_MAP.get(orig, ""))
        if r:
            regions.add(r)

    result = {
        "route": " → ".join(codes),
        "segments": [],
        "excursionist_eligible": len(regions) <= 2,
    }

    for orig, dest in segments:
        awards = lookup_awards(orig, dest, "")
        result["segments"].append({"origin": orig, "destination": dest, "awards": awards})

    return result


# ---------------------------------------------------------------------------
# Chase Travel tools (manual-assist — requires visible browser + login)
# ---------------------------------------------------------------------------


@mcp.tool()
def search_chase_flights(origin: str = "IAD", destination: str = "", profile_dir: str = "") -> dict:
    """Search flights on Chase Travel portal using UR points. REQUIRES manual Chase login in a visible browser window. Takes 2-5 minutes (user must log in and search). Returns flights with UR points pricing."""
    from vplan_cli.scraper_chase import ChaseTravel

    with ChaseTravel(profile_dir=profile_dir or None) as ct:
        results = ct.interactive_session("flights", origin=origin, destination=destination)
        flights = [r for r in results if r.get("type") == "flight"]
        return {"flights": flights, "origin": origin, "destination": destination, "count": len(flights)}


@mcp.tool()
def search_chase_hotels(city: str = "", checkin: str = "", checkout: str = "", profile_dir: str = "") -> dict:
    """Search hotels on Chase Travel portal using UR points. REQUIRES manual Chase login in a visible browser window. Takes 2-5 minutes (user must log in and search). Returns hotels with UR points pricing."""
    from vplan_cli.scraper_chase import ChaseTravel

    with ChaseTravel(profile_dir=profile_dir or None) as ct:
        results = ct.interactive_session("hotels", city=city, checkin=checkin, checkout=checkout)
        hotels = [r for r in results if r.get("type") == "hotel"]
        return {"hotels": hotels, "city": city, "checkin": checkin, "checkout": checkout, "count": len(hotels)}


# ---------------------------------------------------------------------------
# Watchlist management tools
# ---------------------------------------------------------------------------


@mcp.tool()
def watchlist_add(origin: str = "IAD", destination: str = "", cabin: str = "economy", max_miles: int = 50000, name: str = "") -> dict:
    """Add a route to the flight deal watchlist for monitoring."""
    if not name:
        name = f"{origin}-{destination}"
    items = load_watchlist()
    entry = {"name": name, "origin": origin, "dest": destination, "cabin": cabin, "max_miles": max_miles}
    items.append(entry)
    save_watchlist(items)
    return {"status": "added", "entry": entry, "total_items": len(items)}


@mcp.tool()
def watchlist_remove(index: int) -> dict:
    """Remove a watchlist entry by index (1-based)."""
    items = load_watchlist()
    if index < 1 or index > len(items):
        return {"error": f"Invalid index {index}. Watchlist has {len(items)} items."}
    removed = items.pop(index - 1)
    save_watchlist(items)
    return {"status": "removed", "removed": removed, "remaining": len(items)}


@mcp.tool()
def watchlist_run() -> dict:
    """Run all watchlist searches and return deals found. Takes ~30s per watchlist entry."""
    from datetime import datetime
    from vplan_cli.scraper_seats import SeatsAeroScraper

    items = load_watchlist()
    if not items:
        return {"deals": [], "message": "Watchlist is empty"}

    all_deals = []
    with SeatsAeroScraper(headless=True) as scraper:
        for item in items:
            flights = scraper.search_flights(item["origin"], item["dest"], item["cabin"], 50)
            max_mi = item["max_miles"]
            cheap = [
                f for f in flights
                if isinstance(f["mileage_cost"], (int, float)) and 0 < f["mileage_cost"] <= max_mi
            ]
            for f in cheap:
                f["watchlist_name"] = item["name"]
            all_deals.extend(cheap)

    all_deals.sort(key=lambda x: x["mileage_cost"])
    return {"timestamp": datetime.now().isoformat(), "deals": all_deals, "searches_run": len(items)}


# ---------------------------------------------------------------------------
# Trip management tools
# ---------------------------------------------------------------------------


@mcp.tool()
def save_trip_data(name: str, destination: str = "", month: str = "", notes: str = "") -> dict:
    """Save a trip to the trip library."""
    data = {"destination": destination, "month": month, "notes": notes}
    path = save_trip(name, data)
    return {"status": "saved", "name": name, "path": str(path)}


@mcp.tool()
def delete_trip_data(slug: str) -> dict:
    """Delete a saved trip by its slug name."""
    if delete_trip(slug):
        return {"status": "deleted", "slug": slug}
    return {"error": f"Trip '{slug}' not found"}


# ---------------------------------------------------------------------------
# Config tools
# ---------------------------------------------------------------------------


@mcp.tool()
def update_points_balance(program: str, balance: int) -> dict:
    """Update a points/miles balance. Program must be 'chase_ur', 'united', or 'delta'."""
    if program not in ("chase_ur", "united", "delta"):
        return {"error": f"Unknown program '{program}'. Use: chase_ur, united, delta"}
    points = dict(POINTS)
    points[program] = dict(points.get(program, {}))
    points[program]["balance"] = balance
    update_config("points", points)
    return {"status": "updated", "program": program, "new_balance": balance}


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
