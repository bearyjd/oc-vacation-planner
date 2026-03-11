import argparse
import json
import os
import sys
import textwrap
import time

import requests

from vplan_cli.config import (
    DEFAULT_UA,
    FAMILY,
    KARAKEEP_API_KEY,
    KARAKEEP_URL,
    LITEAPI_KEY,
    POINTS,
    SWEET_SPOTS,
    delete_trip,
    ensure_dir,
    get_credentials,
    list_trips,
    load_trip,
    load_watchlist,
    save_credentials,
    save_trip,
    save_watchlist,
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


def _log(msg: str):
    print(msg, file=sys.stderr)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA})
    return s


RESEARCH_SOURCES = [
    "Wikivoyage",
    "Open-Meteo (weather)",
]

TRANSFER_PARTNERS = {
    "chase_ur_to_hyatt": {"name": "Chase UR -> Hyatt", "ratio": 1.0, "type": "hotel", "cpp_target": 2.0},
    "chase_ur_to_united": {"name": "Chase UR -> United", "ratio": 1.0, "type": "airline", "cpp_target": 1.5},
    "chase_ur_to_southwest": {"name": "Chase UR -> Southwest", "ratio": 1.0, "type": "airline", "cpp_target": 1.4},
    "chase_ur_portal": {"name": "Chase Travel Portal (1.5x)", "ratio": 1.5, "type": "portal", "cpp_target": 1.5},
    "united_direct": {"name": "United MileagePlus", "ratio": 1.0, "type": "airline", "cpp_target": 1.3},
    "delta_direct": {"name": "Delta SkyMiles", "ratio": 1.0, "type": "airline", "cpp_target": 1.2},
    "delta_to_flying_blue": {"name": "Delta -> Flying Blue (indirect)", "ratio": 1.0, "type": "airline", "cpp_target": 1.5},
}


def cmd_research(args):
    destination = args.destination
    nights = args.nights
    month = args.month

    _log(f"Researching {destination} ({nights} nights, {month})...")
    s = _session()

    result = {
        "destination": destination,
        "nights": nights,
        "month": month,
        "family": f"{FAMILY['adults']} adults + {len(FAMILY['kids'])} kids ({', '.join(FAMILY['kids'])})",
    }

    sections = scrape_wikivoyage(destination, s)
    if sections:
        result["overview"] = sections.get("Overview", sections.get("Understand", ""))[:1000]

        for key in ["See", "Do", "Eat", "Drink", "Sleep", "Stay safe", "Get in", "Get around"]:
            if key in sections:
                result[key.lower().replace(" ", "_")] = sections[key][:800]
    else:
        result["overview"] = f"Could not fetch guide for {destination}. Try Wikivoyage or TripAdvisor directly."

    result["weather"] = fetch_weather(destination, month, s)
    result["family_tips"] = family_suitability(destination, sections)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"  {destination} — {nights} nights in {month}")
        print(f"  {result['family']}")
        print(f"{'=' * 60}")

        if result.get("overview"):
            print(f"\nOverview:\n{textwrap.fill(result['overview'], 80)}")

        if result.get("weather"):
            print(f"\nWeather ({month}):\n  {result['weather']}")

        for key in ["see", "do", "eat", "get_in", "stay_safe"]:
            if result.get(key):
                label = key.replace("_", " ").title()
                print(f"\n{label}:\n{textwrap.fill(result[key][:500], 80)}")

        if result.get("family_tips"):
            print("\nFamily suitability:")
            for tip in result["family_tips"]:
                print(f"  - {tip}")
        print()


def cmd_points(args):
    hotel_rate = args.hotel_rate or 0
    flights_usd = args.flights_usd or 0

    _log(f"Calculating optimal redemption: hotel ${hotel_rate}/night, flights ${flights_usd} total...")

    result = {
        "hotel_rate_usd": hotel_rate,
        "flights_usd": flights_usd,
        "total_cash": hotel_rate + flights_usd,
        "balances": {
            "chase_ur": f"{POINTS['chase_ur']['balance']:,} ({POINTS['chase_ur']['balance'] * 1.5:,.0f} @ 1.5x portal)",
            "united": f"{POINTS['united']['balance']:,} miles (Premier 1K, {POINTS['united']['plus_points']} PlusPoints)",
            "delta": f"{POINTS['delta']['balance']:,} SkyMiles",
        },
        "options": calculate_redemption(hotel_rate, flights_usd),
        "sweet_spots": SWEET_SPOTS,
        "reminder": "Chase UR 1.5x expires Oct 2027 — prioritize UR redemptions",
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"  Points Optimization")
        print(f"  Hotel: ${hotel_rate:,.0f}/night | Flights: ${flights_usd:,.0f}")
        print(f"  Total cash price: ${hotel_rate + flights_usd:,.0f}")
        print(f"{'=' * 60}")

        print("\nCurrent balances:")
        for k, v in result["balances"].items():
            print(f"  {k}: {v}")

        print(f"\n{'─' * 60}")
        for i, opt in enumerate(result["options"], 1):
            print(f"\n  Option {i}: {opt['strategy']}")
            print(f"    Points: {opt['points_used']}")
            if opt.get("cash_spent"):
                print(f"    Cash:   {opt['cash_spent']}")
            print(f"    Saved:  {opt['cash_saved']}")
            print(f"    CPP:    {opt['cpp']}cpp")
            if opt.get("priority"):
                print(f"    >>> {opt['priority']}")
            if opt.get("notes"):
                print(f"    Note:   {opt['notes']}")

        print(f"\n{'─' * 60}")
        print("  Sweet spots to check:")
        for sp in SWEET_SPOTS:
            print(f"    {sp['from']} -> {sp['to']} ({sp['ratio']}) — {sp['note']}")
        print(f"\n  ** Chase UR 1.5x expires Oct 2027 — use first **\n")


def cmd_awards(args):
    origin = args.origin
    dest = args.dest
    month = args.month
    live = getattr(args, "live", False)

    _log(f"Searching award availability: {origin} -> {dest} in {month}...")
    result = lookup_awards(origin, dest, month)

    live_flights: list = []
    if live:
        _log("Fetching live availability from seats.aero (15-30 seconds)...")
        from vplan_cli.scraper_seats import SeatsAeroScraper
        with SeatsAeroScraper(headless=True) as scraper:
            live_flights = scraper.search_flights(origin, dest, "economy", 30)
        result["live_flights"] = live_flights

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"  Award Search: {result['route']}")
        print(f"  Region: {result['region']} | Month: {result['month']}")
        print(f"  Family of 5 (round trip)")
        print(f"{'=' * 60}")

        for prog in result["programs"]:
            print(f"\n  {prog['program']}")
            print(f"    Balance: {prog['balance']}")
            for k, v in prog.items():
                if k in ("program", "balance", "notes"):
                    continue
                label = k.replace("_", " ").title()
                print(f"    {label}: {v}")
            if prog.get("notes"):
                print(f"    Note: {prog['notes']}")

        if live_flights:
            print(f"\n{'─' * 60}")
            print(f"  Live Availability (seats.aero, {len(live_flights)} options):")
            seen = set()
            for f in live_flights:
                key = (f["carriers"], f["mileage_cost"], f["stops"])
                if key in seen:
                    continue
                seen.add(key)
                miles = f["mileage_cost"]
                miles_str = f"{miles:,}" if isinstance(miles, int) else str(miles)
                stops = f["stops"]
                stop_str = "nonstop" if stops == 0 else f"{stops}stop"
                print(f"    {f['carriers']} | {miles_str}mi | ${f['taxes_usd']:.0f}tax | {stop_str} | {f['duration']} | {f['source']}")
        elif live:
            print(f"\n{'─' * 60}")
            print("  No live availability found on seats.aero")

        print(f"\n{'─' * 60}")
        print("  Search links:")
        for link in result["search_links"]:
            print(f"    {link}")
        print()


def cmd_visa(args):
    country = args.destination

    _log(f"Checking visa requirements for {country} (US passport)...")
    result = lookup_visa(country)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"  Visa Requirements: {result['country']}")
        print(f"  Passport: US")
        print(f"{'=' * 60}")

        visa_req = result.get("visa_required", "Unknown")
        if visa_req is False:
            print("\n  Visa required: NO")
        elif visa_req is True:
            print("\n  Visa required: YES")
        else:
            print(f"\n  Visa required: {visa_req}")

        if result.get("max_stay_days"):
            print(f"  Max stay: {result['max_stay_days']} days")
        if result.get("documents"):
            print(f"  Documents: {result['documents']}")
        if result.get("notes"):
            print(f"\n  {result['notes']}")
        if result.get("links"):
            print("\n  Check:")
            for link in result["links"]:
                print(f"    {link}")
        if result.get("source"):
            print(f"\n  Source: {result['source']}")
        print()


def cmd_save(args):
    title = args.title
    url = args.url
    notes = args.notes
    tags = args.tags.split(",") if args.tags else ["travel", "trip-idea"]

    _log(f"Saving to Karakeep: {title}...")

    api_base = f"{KARAKEEP_URL}/api/v1"
    headers = {
        "Authorization": f"Bearer {KARAKEEP_API_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "type": "link",
        "url": url,
        "title": title,
        "note": notes or "",
    }

    try:
        r = requests.post(f"{api_base}/bookmarks", json=body, headers=headers, timeout=15)
        r.raise_for_status()
        bookmark = r.json()
        bookmark_id = bookmark.get("id")
        _log(f"Bookmark created: {bookmark_id}")

        if bookmark_id and tags:
            tag_body = {"tags": [{"tagName": t.strip()} for t in tags]}
            r2 = requests.post(
                f"{api_base}/bookmarks/{bookmark_id}/tags",
                json=tag_body,
                headers=headers,
                timeout=15,
            )
            r2.raise_for_status()
            _log(f"Tags added: {', '.join(tags)}")

        result = {
            "status": "saved",
            "bookmark_id": bookmark_id,
            "title": title,
            "url": url,
            "tags": tags,
            "karakeep_url": KARAKEEP_URL,
        }

    except requests.RequestException as e:
        result = {
            "status": "error",
            "error": str(e),
            "title": title,
            "url": url,
        }
        _log(f"Error saving to Karakeep: {e}")

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["status"] == "saved":
            print(f"\nSaved: {title}")
            print(f"  URL: {url}")
            print(f"  Tags: {', '.join(tags)}")
            print(f"  Karakeep ID: {result['bookmark_id']}")
        else:
            print(f"\nFailed to save: {result.get('error', 'unknown error')}")
        print()


def cmd_itinerary(args):
    destination = args.destination
    nights = args.nights
    ages = [int(a.strip()) for a in args.ages.split(",")]

    _log(f"Generating itinerary: {destination}, {nights} nights, kids ages {ages}...")
    result = generate_itinerary(destination, nights, ages)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"  {destination} — {nights}-Night Itinerary")
        print(f"  {result['travelers']}")
        print(f"{'=' * 60}")

        for day in result["days"]:
            print(f"\n--- Day {day['day']}: {day['theme']} ---")
            for activity in day["activities"]:
                print(f"  - {activity}")
            if day.get("kid_tips"):
                print("  Kid tips:")
                for tip in day["kid_tips"]:
                    print(f"    * {tip}")
            if day.get("teen_tips"):
                print("  Teen tips:")
                for tip in day["teen_tips"]:
                    print(f"    * {tip}")

        if result.get("packing_tips"):
            print(f"\n{'─' * 60}")
            print("Packing reminders:")
            for tip in result["packing_tips"]:
                print(f"  - {tip}")
        print()


def _print_flight_list(flights: list, label: str, detailed: bool = False):
    print(f"\n  {label} ({len(flights)} options):")
    seen = set()
    for flight in flights:
        key = (flight["carriers"], flight["mileage_cost"], flight["stops"], flight["duration"], flight.get("date", ""))
        if key in seen:
            continue
        seen.add(key)
        miles = flight["mileage_cost"]
        miles_str = f"{miles:,}" if isinstance(miles, int) else str(miles)
        seats = flight["remaining_seats"]
        seats_str = f"{seats} seats" if seats else ""
        stops = flight["stops"]
        stop_str = "nonstop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"
        tax_cur = flight.get("taxes_currency", "USD")
        tax_sym = "$" if tax_cur == "USD" else f"{tax_cur} "
        date_str = f" | {flight['date']}" if flight.get("date") else ""
        print(f"\n    {flight['carriers']} | {miles_str} miles | {tax_sym}{flight['taxes_usd']:.0f} tax | {stop_str} | {flight['duration']}{date_str}")

        detail_parts = []
        if seats_str:
            detail_parts.append(f"{seats_str} remaining")
        detail_parts.append(f"via {flight['source']}")
        if detailed:
            if flight.get("flight_numbers"):
                detail_parts.append(f"flights: {flight['flight_numbers']}")
            times = ""
            if flight.get("depart_time"):
                times = f"dep {flight['depart_time']}"
            if flight.get("arrive_time"):
                times += f" → arr {flight['arrive_time']}"
            if times:
                detail_parts.append(times.strip())
        print(f"      {' | '.join(detail_parts)}")


def cmd_search(args):
    origin = args.origin
    dest = args.dest
    cabin = args.cabin
    limit = args.limit
    round_trip = getattr(args, "round_trip", False)
    detailed = getattr(args, "detailed", False)

    _log(f"Searching award flights: {origin} -> {dest} ({cabin}){'(round trip)' if round_trip else ''}...")
    _log("This uses seats.aero and requires a browser — may take 15-30 seconds...")

    if round_trip:
        from vplan_cli.scraper_seats import search_round_trip
        rt = search_round_trip(origin, dest, cabin, limit)
        outbound = rt["outbound"]
        ret = rt["return"]

        if args.json:
            print(json.dumps({"outbound": outbound, "return": ret, "origin": origin, "destination": dest}, indent=2))
        else:
            print(f"\n{'=' * 70}")
            print(f"  Round Trip: {origin} <-> {dest} ({cabin})")
            print(f"{'=' * 70}")
            if outbound:
                _print_flight_list(outbound, f"Outbound: {origin} -> {dest}", detailed=detailed)
            else:
                print(f"\n  No outbound flights found.")
            if ret:
                _print_flight_list(ret, f"Return: {dest} -> {origin}", detailed=detailed)
            else:
                print(f"\n  No return flights found.")
            print(f"\n{'─' * 70}")
            print(f"  Data from seats.aero (free tier, last 60 days)")
            print()
    else:
        from vplan_cli.scraper_seats import SeatsAeroScraper
        with SeatsAeroScraper(headless=True) as scraper:
            results = scraper.search_flights(origin, dest, cabin, limit)

        if not results:
            _log("No award flights found.")
            if args.json:
                print(json.dumps({"flights": [], "origin": origin, "destination": dest}))
            return

        if args.json:
            print(json.dumps({"flights": results, "origin": origin, "destination": dest}, indent=2))
        else:
            print(f"\n{'=' * 70}")
            print(f"  Award Flights: {origin} -> {dest} ({cabin})")
            print(f"  {len(results)} options found via seats.aero")
            print(f"{'=' * 70}")
            _print_flight_list(results, f"{origin} -> {dest}", detailed=detailed)
            print(f"\n{'─' * 70}")
            print(f"  Data from seats.aero (free tier, last 60 days)")
            print(f"  For real-time booking, check airline sites directly.")
            print()


def cmd_login(args):
    import getpass

    service = args.service
    username = args.username or input(f"{service} username/email: ")
    password = getpass.getpass(f"{service} password: ")

    save_credentials(service, username, password)
    _log(f"Credentials saved for {service} in ~/.vplan/credentials.json")
    print(f"Saved {service} login for {username}")


def cmd_trips(args):
    action = args.trips_action

    if action == "list":
        trips = list_trips()
        if args.json:
            print(json.dumps(trips, indent=2))
            return
        if not trips:
            print("No saved trips. Use 'vplan trips save' or 'vplan plan' to create one.")
            return
        print(f"\n{'=' * 50}")
        print(f"  Saved Trips ({len(trips)})")
        print(f"{'=' * 50}")
        for t in trips:
            dest = t.get("destination", "")
            month = t.get("month", "")
            detail = f" — {dest}" if dest else ""
            detail += f" ({month})" if month else ""
            print(f"  {t['slug']}{detail}")
        print()

    elif action == "show":
        slug = args.name
        data = load_trip(slug)
        if not data:
            print(f"Trip '{slug}' not found. Run 'vplan trips list' to see saved trips.")
            return
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            name = data.get("_name", slug)
            print(f"\n{'=' * 60}")
            print(f"  Trip: {name}")
            print(f"{'=' * 60}")
            for k, v in data.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict):
                    print(f"\n  {k}:")
                    for k2, v2 in v.items():
                        display = str(v2)[:200]
                        print(f"    {k2}: {display}")
                elif isinstance(v, list):
                    print(f"\n  {k}: ({len(v)} items)")
                    for item in v[:5]:
                        print(f"    - {str(item)[:150]}")
                    if len(v) > 5:
                        print(f"    ... and {len(v) - 5} more")
                else:
                    print(f"  {k}: {str(v)[:200]}")
            print()

    elif action == "delete":
        slug = args.name
        if delete_trip(slug):
            print(f"Deleted trip '{slug}'")
        else:
            print(f"Trip '{slug}' not found.")

    elif action == "save":
        name = args.name
        dest = args.destination or ""
        month = args.month or ""
        notes = args.notes or ""
        data = {"destination": dest, "month": month, "notes": notes}
        path = save_trip(name, data)
        print(f"Saved trip '{name}' to {path}")


def cmd_hotels(args):
    destination = args.destination
    checkin = args.checkin
    checkout = args.checkout
    nights = args.nights
    country_code = getattr(args, "country", "")

    result = {
        "destination": destination,
        "checkin": checkin,
        "checkout": checkout,
        "nights": nights,
        "hyatt_award_chart": [],
        "live_hotels": [],
        "search_links": [],
    }

    for cat, info in HYATT_CATEGORIES.items():
        pts = info["points"]
        family_total = pts * nights
        can_afford = family_total <= POINTS["chase_ur"]["balance"]
        result["hyatt_award_chart"].append({
            "category": cat,
            "points_per_night": pts,
            "total_points": family_total,
            "nights": nights,
            "can_afford_with_ur": can_afford,
        })

    if LITEAPI_KEY:
        _log(f"Searching live hotel rates via LiteAPI...")
        live = search_hotels_liteapi(destination, country_code, checkin, checkout)
        result["live_hotels"] = live
    else:
        _log("Set LITEAPI_KEY env var for live hotel pricing (free at dashboard.liteapi.travel)")

    result["search_links"] = [
        f"https://www.hyatt.com/search/{destination}?checkinDate={checkin}&checkoutDate={checkout}&rooms=1&adults=2&kids=3",
        f"https://www.google.com/travel/hotels/{destination}?q={destination}+hotels&dates={checkin}+to+{checkout}&adults=2",
        f"https://www.kayak.com/hotels/{destination}/{checkin}/{checkout}/2adults/3children?currency=USD&sort=price_a",
        f"https://www.marriott.com/search/default.mi?fromDate={checkin}&toDate={checkout}&destinationAddress={destination}",
    ]

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'=' * 60}")
        print(f"  Hotels: {destination}")
        print(f"  {checkin} to {checkout} ({nights} nights)")
        print(f"  Chase UR balance: {POINTS['chase_ur']['balance']:,}")
        print(f"{'=' * 60}")

        if result["live_hotels"]:
            print(f"\n  Live Hotel Rates ({len(result['live_hotels'])} found):")
            for h in result["live_hotels"][:10]:
                name = h["name"][:40]
                stars = f"{'★' * int(float(h['rating']))}" if h["rating"] else ""
                price = f"${h['price_usd']:,.0f}" if h["price_usd"] else "N/A"
                ppn = f"${h['price_usd'] / nights:,.0f}/nt" if h["price_usd"] else ""
                print(f"    {name:<40} {stars:<6} {price:>8} total  {ppn}")

            cheapest = next((h for h in result["live_hotels"] if h["price_usd"]), None)
            if cheapest and cheapest["price_usd"]:
                ur_equivalent = int(cheapest["price_usd"] / 0.015)
                print(f"\n    Cheapest cash rate: ${cheapest['price_usd']:,.0f}")
                print(f"    UR portal equivalent: {ur_equivalent:,} UR (at 1.5 cpp)")
                print(f"    Your UR balance: {POINTS['chase_ur']['balance']:,}")

        print(f"\n  Hyatt Award Chart (Chase UR -> Hyatt 1:1):")
        for entry in result["hyatt_award_chart"]:
            cat = entry["category"]
            ppn = entry["points_per_night"]
            total = entry["total_points"]
            afford = "✓" if entry["can_afford_with_ur"] else "✗"
            print(f"    Cat {cat}: {ppn:,}/night × {nights} = {total:,} UR  [{afford}]")

        print(f"\n{'─' * 60}")
        print("  Search links:")
        for link in result["search_links"]:
            print(f"    {link}")
        print()


def cmd_plan(args):
    destination = args.destination
    dest_code = args.dest_code
    month = args.month
    nights = args.nights

    trip_data = {
        "destination": destination,
        "dest_code": dest_code,
        "month": month,
        "nights": nights,
        "family": f"{FAMILY['adults']} adults + {len(FAMILY['kids'])} kids",
    }

    print(f"\n{'=' * 70}")
    print(f"  TRIP PLAN: {destination}")
    print(f"  {nights} nights in {month} | {trip_data['family']}")
    print(f"{'=' * 70}")

    _log(f"\n[1/5] Researching {destination}...")
    s = _session()

    wiki = scrape_wikivoyage(destination, s)
    weather = fetch_weather(destination, month, s)
    trip_data["overview"] = wiki.get("Overview", "")[:500]
    trip_data["weather"] = weather

    print(f"\n  Overview: {trip_data['overview'][:200]}...")
    print(f"  Weather: {weather}")

    _log(f"\n[2/5] Checking entry requirements...")
    visa = lookup_visa(destination)
    trip_data["visa"] = visa.get("summary", "Check entry requirements")

    print(f"  Visa: {trip_data['visa'][:150]}")

    _log(f"\n[3/5] Checking award availability for {dest_code}...")
    awards = lookup_awards("IAD", dest_code, month)
    trip_data["award_programs"] = awards.get("programs", [])

    for prog in awards.get("programs", [])[:3]:
        print(f"  {prog['program']}: {prog.get('balance', 'N/A')}")

    _log(f"\n[4/5] Calculating points strategy...")
    trip_data["points_balances"] = {
        "chase_ur": f"{POINTS['chase_ur']['balance']:,}",
        "united": f"{POINTS['united']['balance']:,}",
        "delta": f"{POINTS['delta']['balance']:,}",
    }

    _log(f"\n[5/5] Generating itinerary...")
    ages = [int(a) for a in "8,10,14".split(",")]
    itinerary = generate_itinerary(destination, nights, ages)
    trip_data["itinerary_days"] = len(itinerary.get("days", []))

    print(f"  Itinerary: {trip_data['itinerary_days']} days planned")

    path = save_trip(destination, trip_data)
    print(f"\n{'─' * 70}")
    print(f"  Trip saved to {path}")
    print(f"  View with: vplan trips show {destination.lower().replace(' ', '-')}")
    print()


def cmd_multicity(args):
    stops = args.stops
    cabin = args.cabin

    if len(stops) < 3:
        print("Need at least 3 airport codes (e.g., IAD LHR BCN IAD)", file=sys.stderr)
        sys.exit(1)

    live = getattr(args, "live", False)
    detailed = getattr(args, "detailed", False)

    segments = []
    for i in range(len(stops) - 1):
        segments.append((stops[i].upper(), stops[i + 1].upper()))

    from vplan_cli.data_sources import lookup_awards, REGION_MAP

    excursionist_eligible = False
    if len(segments) >= 2:
        regions = set()
        for orig, dest in segments:
            r = REGION_MAP.get(dest, REGION_MAP.get(orig, ""))
            if r:
                regions.add(r)
        if len(regions) <= 2:
            excursionist_eligible = True

    result: dict = {"segments": [], "excursionist_eligible": excursionist_eligible}

    if not args.json:
        route_str = " → ".join(stops)
        print(f"\n{'=' * 70}")
        print(f"  Multi-City: {route_str} ({cabin})")
        if excursionist_eligible:
            print(f"  ★ Potential United Excursionist Perk — one segment could be FREE")
        print(f"{'=' * 70}")

    total_miles_united = 0
    total_miles_delta = 0

    for orig, dest in segments:
        awards = lookup_awards(orig, dest, "")
        seg_data: dict = {"origin": orig, "destination": dest, "awards": awards}

        if not args.json:
            print(f"\n{'─' * 70}")
            print(f"  Segment: {orig} → {dest}")

            for p in awards.get("programs", []):
                prog_name = p["program"]
                bal = p.get("balance", "N/A")
                saver = p.get("saver_rt", p.get("low_rt", "N/A"))
                print(f"    {prog_name}: {bal} | one-way ~{saver}")

                if "United" in prog_name and "saver_rt" in p:
                    cost_str = p["saver_rt"].replace(",", "").split(" ")[0]
                    try:
                        total_miles_united += int(cost_str) // 2
                    except ValueError:
                        pass
                if "Delta" in prog_name and "low_rt" in p:
                    cost_str = p["low_rt"].replace(",", "").split(" ")[0]
                    try:
                        total_miles_delta += int(cost_str) // 2
                    except ValueError:
                        pass

        if live:
            _log(f"  Searching live: {orig} -> {dest}...")
            from vplan_cli.scraper_seats import SeatsAeroScraper
            with SeatsAeroScraper(headless=True) as scraper:
                flights = scraper.search_flights(orig, dest, cabin, 30)
            if not args.json and flights:
                _print_flight_list(flights, f"Live: {orig} → {dest}", detailed=detailed)
            seg_data["live_flights"] = flights

        result["segments"].append(seg_data)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'=' * 70}")
        fam = 5
        if total_miles_united > 0:
            total_fam = total_miles_united * fam
            print(f"  United total (family of {fam}): ~{total_fam:,} miles for all segments")
            if excursionist_eligible:
                cheapest_seg = min(total_miles_united // len(segments), total_miles_united)
                saved = cheapest_seg * fam
                print(f"  With Excursionist Perk: ~{(total_fam - saved):,} miles (save ~{saved:,})")
        if total_miles_delta > 0:
            print(f"  Delta total (family of {fam}): ~{total_miles_delta * fam:,} SkyMiles for all segments")

        if excursionist_eligible:
            print(f"\n  ★ United Excursionist Perk: Book as multi-city on united.com")
            print(f"    One intra-region segment can be free when booked as part of round trip")

        print()


def cmd_watch(args):
    action = args.watch_action

    if action == "add":
        origin = args.origin
        dest = args.dest
        cabin = args.cabin or "economy"
        max_miles = args.max_miles or 50000
        name = args.name or f"{origin}-{dest}"

        items = load_watchlist()
        entry = {
            "name": name,
            "origin": origin,
            "dest": dest,
            "cabin": cabin,
            "max_miles": max_miles,
        }
        items.append(entry)
        save_watchlist(items)
        print(f"Added watchlist: {name} ({origin}->{dest}, {cabin}, max {max_miles:,} miles)")

    elif action == "list":
        items = load_watchlist()
        if not items:
            print("Watchlist is empty. Use 'vplan watch add --origin IAD --dest CUN' to add one.")
            return
        if args.json:
            print(json.dumps(items, indent=2))
            return
        print(f"\n{'=' * 60}")
        print(f"  Watchlist ({len(items)} searches)")
        print(f"{'=' * 60}")
        for i, item in enumerate(items, 1):
            print(f"  {i}. {item['name']}: {item['origin']}->{item['dest']} ({item['cabin']}, max {item['max_miles']:,}mi)")
        print()

    elif action == "remove":
        index = args.index
        items = load_watchlist()
        if index < 1 or index > len(items):
            print(f"Invalid index {index}. Run 'vplan watch list' to see items.", file=sys.stderr)
            sys.exit(1)
        removed = items.pop(index - 1)
        save_watchlist(items)
        print(f"Removed: {removed['name']}")

    elif action == "run":
        items = load_watchlist()
        if not items:
            print("Watchlist is empty. Nothing to check.")
            return

        log_path = getattr(args, "log", "")
        _log(f"Running {len(items)} watchlist searches...")

        from vplan_cli.scraper_seats import SeatsAeroScraper
        from datetime import datetime

        all_deals = []
        with SeatsAeroScraper(headless=True) as scraper:
            for i, item in enumerate(items):
                _log(f"  [{i+1}/{len(items)}] {item['origin']}->{item['dest']} ({item['cabin']})...")
                flights = scraper.search_flights(item["origin"], item["dest"], item["cabin"], 50)
                max_mi = item["max_miles"]
                cheap = [
                    f for f in flights
                    if isinstance(f["mileage_cost"], (int, float)) and 0 < f["mileage_cost"] <= max_mi
                ]
                for f in cheap:
                    f["_watch"] = item["name"]
                all_deals.extend(cheap)

        all_deals.sort(key=lambda x: x["mileage_cost"])

        if args.json:
            print(json.dumps({"timestamp": datetime.now().isoformat(), "deals": all_deals}, indent=2))
        else:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            if not all_deals:
                output = f"[{timestamp}] No deals found across {len(items)} watchlist searches.\n"
            else:
                lines = [f"[{timestamp}] {len(all_deals)} deal(s) found:"]
                seen = set()
                for d in all_deals:
                    key = (d["_watch"], d["carriers"], d["mileage_cost"], d["stops"])
                    if key in seen:
                        continue
                    seen.add(key)
                    miles = d["mileage_cost"]
                    miles_str = f"{miles:,}" if isinstance(miles, int) else str(miles)
                    stops = "nonstop" if d["stops"] == 0 else f"{d['stops']}stop"
                    date = d.get("date", "")
                    lines.append(
                        f"  [{d['_watch']}] {d['carriers']} | {miles_str}mi | "
                        f"${d['taxes_usd']:.0f}tax | {stops} | {d['duration']} | {date} | {d['source']}"
                    )
                output = "\n".join(lines) + "\n"

            print(output, end="")

            if log_path:
                with open(log_path, "a") as f:
                    f.write(output)
                _log(f"Appended to {log_path}")


def cmd_calendar(args):
    import calendar
    from collections import defaultdict

    origin = args.origin
    dest = args.dest
    cabin = args.cabin

    _log(f"Searching award flights: {origin} -> {dest} ({cabin})...")
    _log("Building calendar from seats.aero data (15-30 seconds)...")

    from vplan_cli.scraper_seats import SeatsAeroScraper

    with SeatsAeroScraper(headless=True) as scraper:
        flights = scraper.search_flights(origin, dest, cabin, 100)

    if not flights:
        print("No flights found — cannot build calendar.")
        return

    if args.json:
        by_date: dict[str, list] = defaultdict(list)
        for f in flights:
            if f.get("date"):
                by_date[f["date"]].append(f)
        print(json.dumps({"origin": origin, "destination": dest, "dates": dict(by_date)}, indent=2))
        return

    cheapest: dict[str, dict] = {}
    for f in flights:
        date = f.get("date", "")
        if not date or len(date) < 10:
            continue
        cost = f["mileage_cost"]
        if not isinstance(cost, (int, float)) or cost <= 0:
            continue
        if date not in cheapest or cost < cheapest[date]["mileage_cost"]:
            cheapest[date] = f

    if not cheapest:
        print("No dated flights found — seats.aero data may lack date info for this route.")
        return

    months: dict[str, dict[int, dict]] = defaultdict(dict)
    for date_str, flight in cheapest.items():
        try:
            year, month, day = date_str.split("-")
            key = f"{year}-{month}"
            months[key][int(day)] = flight
        except (ValueError, IndexError):
            continue

    for month_key in sorted(months.keys()):
        year, mon = month_key.split("-")
        year_i, mon_i = int(year), int(mon)
        month_data = months[month_key]

        print(f"\n{'=' * 62}")
        print(f"  {calendar.month_name[mon_i]} {year_i} — {origin} -> {dest} ({cabin})")
        print(f"{'=' * 62}")
        print(f"  {'Mon':>8}  {'Tue':>8}  {'Wed':>8}  {'Thu':>8}  {'Fri':>8}  {'Sat':>8}  {'Sun':>8}")
        print(f"  {'─' * 62}")

        cal = calendar.monthcalendar(year_i, mon_i)
        for week in cal:
            cells = []
            for day in week:
                if day == 0:
                    cells.append("        ")
                elif day in month_data:
                    cost = month_data[day]["mileage_cost"]
                    if isinstance(cost, int):
                        if cost >= 100000:
                            label = f"{cost // 1000}k"
                        else:
                            label = f"{cost // 1000}k" if cost >= 1000 else str(cost)
                    else:
                        label = "?"
                    cells.append(f"{day:2d}={label:>4}")
                else:
                    cells.append(f"{day:2d}      ")
            print(f"  {'  '.join(cells)}")

    all_costs = [f["mileage_cost"] for f in cheapest.values() if isinstance(f["mileage_cost"], int)]
    if all_costs:
        print(f"\n  Cheapest: {min(all_costs):,} miles | Most expensive: {max(all_costs):,} miles")
        print(f"  {len(cheapest)} dates with availability across {len(months)} month(s)")

    print(f"\n  Legend: DD=XXk means day DD has availability at XXk miles (cheapest option)")
    print(f"  Data from seats.aero — cached availability, not real-time.")
    print()


def cmd_compare(args):
    destinations = args.destinations
    month = args.month or ""
    origin = args.origin

    if len(destinations) < 2:
        print("Error: provide at least 2 destinations to compare.", file=sys.stderr)
        sys.exit(1)

    from vplan_cli.advisor import (
        DESTINATION_CODES,
        _gather_destination_context,
    )

    _log(f"Comparing {len(destinations)} destinations...")

    contexts = []
    for dest in destinations:
        code = DESTINATION_CODES.get(dest.lower(), dest.upper() if len(dest) == 3 else "")
        _log(f"  Fetching data for {dest} ({code})...")
        ctx = _gather_destination_context(dest, code, origin, month if month else None, live=False)
        contexts.append(ctx)

    if args.json:
        print(json.dumps(contexts, indent=2))
        return

    col_width = max(20, 70 // len(contexts))

    def _col(text: str, width: int = col_width) -> str:
        text = str(text)
        return text[:width].ljust(width)

    header = "  " + "".join(_col(ctx.get("destination", "?").upper()) for ctx in contexts)
    sep = "  " + "─" * (col_width * len(contexts))

    print(f"\n{'=' * (col_width * len(contexts) + 2)}")
    print(f"  Destination Comparison — {month or 'Any month'}")
    print(f"  Origin: {origin}")
    print(f"{'=' * (col_width * len(contexts) + 2)}")
    print(header)
    print(sep)

    rows = [
        ("Weather", lambda c: c.get("weather", "N/A")[:col_width - 2]),
        ("Visa", lambda c: (
            "No visa" if c.get("visa", {}).get("required") is False
            else "Visa req'd" if c.get("visa", {}).get("required") is True
            else "Unknown"
        )),
        ("Nonstop", lambda c: (
            f"{c['nonstop']['from']} via {','.join(c['nonstop']['airlines'])}"
            if c.get("nonstop") else "No nonstop"
        )),
        ("Family", lambda c: "; ".join(c.get("family_tips", []))[:col_width - 2] if c.get("family_tips") else "N/A"),
    ]

    for prog_name in ["United MileagePlus", "Chase UR -> United (1:1 transfer)", "Delta SkyMiles"]:
        def _make_prog_fn(pn: str):
            def fn(c):
                for p in c.get("award_programs", []):
                    if p["program"] == pn:
                        for k in ["family_of_5_saver", "family_of_5_low"]:
                            if k in p:
                                afford = "✓" if p.get("can_afford_saver", p.get("can_afford_low", p.get("can_afford", False))) else "✗"
                                return f"{p[k]} [{afford}]"
                        return p.get("balance", "N/A")
                return "N/A"
            return fn
        label = prog_name.split(" ")[0] if len(prog_name) > 12 else prog_name
        rows.append((label[:12], _make_prog_fn(prog_name)))

    for label, fn in rows:
        vals = "".join(_col(fn(ctx)) for ctx in contexts)
        print(f"  {label:<12} {vals}")

    print(sep)

    print(f"\n  Use 'vplan ask \"compare {' vs '.join(destinations)}\"' for AI-powered detailed comparison.")
    print()


def cmd_config(args):
    action = args.config_action

    if action == "show":
        data = {
            "family": FAMILY,
            "points": POINTS,
            "sweet_spots": SWEET_SPOTS,
        }
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"\n{'=' * 60}")
            print("  vplan configuration")
            print(f"{'=' * 60}")

            print(f"\n  Family: {FAMILY.get('name', 'N/A')}")
            print(f"  Adults: {FAMILY.get('adults', 0)}")
            kids = FAMILY.get("kids", [])
            print(f"  Kids: {', '.join(kids) if kids else 'None'}")
            airports = FAMILY.get("home_airports", [])
            print(f"  Home airports: {', '.join(airports) if airports else 'N/A'}")

            print(f"\n{'─' * 60}")
            print("  Points balances:")
            for prog, info in POINTS.items():
                bal = info.get("balance", 0)
                extras = []
                if info.get("status"):
                    extras.append(info["status"])
                if info.get("expires"):
                    extras.append(f"expires {info['expires']}")
                if info.get("plus_points"):
                    extras.append(f"{info['plus_points']} PlusPoints")
                extra_str = f" ({', '.join(extras)})" if extras else ""
                print(f"    {prog}: {bal:,}{extra_str}")

            print(f"\n{'─' * 60}")
            print("  Sweet spots:")
            for sp in SWEET_SPOTS:
                print(f"    {sp['from']} -> {sp['to']} ({sp['ratio']}): {sp['note']}")

            print(f"\n  Config file: ~/.vplan/config.json")
            print()

    elif action == "set":
        key = args.key
        value = args.value

        if key == "chase_ur" or key == "united" or key == "delta":
            try:
                balance = int(value)
            except ValueError:
                print(f"Error: balance must be an integer, got '{value}'", file=sys.stderr)
                sys.exit(1)
            points = dict(POINTS)
            if key not in points:
                points[key] = {}
            points[key] = dict(points[key])
            points[key]["balance"] = balance
            update_config("points", points)
            print(f"Updated {key} balance to {balance:,}")

        elif key == "name":
            family = dict(FAMILY)
            family["name"] = value
            update_config("family", family)
            print(f"Updated family name to '{value}'")

        elif key == "adults":
            try:
                adults = int(value)
            except ValueError:
                print(f"Error: adults must be an integer, got '{value}'", file=sys.stderr)
                sys.exit(1)
            family = dict(FAMILY)
            family["adults"] = adults
            update_config("family", family)
            print(f"Updated adults to {adults}")

        elif key == "kids":
            kids = [k.strip() for k in value.split(",") if k.strip()]
            family = dict(FAMILY)
            family["kids"] = kids
            update_config("family", family)
            print(f"Updated kids to: {', '.join(kids)}")

        elif key == "airports":
            airports = [a.strip().upper() for a in value.split(",") if a.strip()]
            family = dict(FAMILY)
            family["home_airports"] = airports
            update_config("family", family)
            print(f"Updated home airports to: {', '.join(airports)}")

        else:
            print(f"Unknown config key: {key}", file=sys.stderr)
            print("Valid keys: chase_ur, united, delta, name, adults, kids, airports", file=sys.stderr)
            sys.exit(1)

    elif action == "reset":
        from vplan_cli.config import CONFIG_PATH
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
            print("Config reset to defaults. Restart vplan for changes to take effect.")
        else:
            print("No custom config found — already using defaults.")


def cmd_ask(args):
    query = " ".join(args.query)
    if not query.strip():
        print("Usage: vplan ask 'your travel question here'", file=sys.stderr)
        sys.exit(1)

    live = getattr(args, "live", False)
    model = getattr(args, "model", "gpt-4o-mini")
    verbose = getattr(args, "verbose", False)
    export_path = getattr(args, "export", None)
    copy = getattr(args, "copy", False)

    from vplan_cli.advisor import ask as advisor_ask

    chunks = []
    for chunk in advisor_ask(query, live=live, model=model, verbose=verbose):
        print(chunk, end="", flush=True)
        chunks.append(chunk)
    print()

    full_response = "".join(chunks)

    if export_path:
        with open(export_path, "w") as f:
            if export_path.endswith(".json"):
                import json as _json
                _json.dump({"query": query, "response": full_response, "model": model}, f, indent=2)
            else:
                f.write(f"# {query}\n\n{full_response}\n")
        _log(f"Exported to {export_path}")

    if copy:
        import subprocess
        copied = False
        for cmd in [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]:
            try:
                proc = subprocess.run(cmd, input=full_response.encode(), capture_output=True, timeout=5)
                if proc.returncode == 0:
                    _log("Copied to clipboard")
                    copied = True
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        if not copied:
            _log("Could not copy to clipboard (install xclip or xsel)")


def cmd_chat(args):
    model = getattr(args, "model", "gpt-4o-mini")
    verbose = getattr(args, "verbose", False)

    from vplan_cli.advisor import chat as advisor_chat

    advisor_chat(model=model, verbose=verbose)


def cmd_deals(args):
    origin = args.origin
    max_miles = args.max_miles
    cabin = args.cabin
    region_filter = getattr(args, "region", "")

    from vplan_cli.routes import NONSTOP_ROUTES

    if origin not in NONSTOP_ROUTES:
        print(f"No nonstop routes found for {origin}", file=sys.stderr)
        sys.exit(1)

    routes = NONSTOP_ROUTES[origin]
    if region_filter:
        routes = {k: v for k, v in routes.items() if v["region"] == region_filter}

    dest_codes = list(routes.keys())
    _log(f"Scanning {len(dest_codes)} nonstop routes from {origin} for deals under {max_miles:,} miles...")
    _log(f"This will use seats.aero — ~30 seconds per route. Scanning up to {min(len(dest_codes), 5)} routes.")

    from vplan_cli.scraper_seats import SeatsAeroScraper

    deals = []
    scan_limit = min(len(dest_codes), 5)

    with SeatsAeroScraper(headless=True) as scraper:
        for i, dest in enumerate(dest_codes[:scan_limit]):
            route_info = NONSTOP_ROUTES[origin][dest]
            _log(f"  [{i+1}/{scan_limit}] {origin}->{dest} ({route_info['region']})...")
            flights = scraper.search_flights(origin, dest, cabin, 30)
            for f in flights:
                if isinstance(f["mileage_cost"], (int, float)) and f["mileage_cost"] <= max_miles and f["mileage_cost"] > 0:
                    f["_dest"] = dest
                    f["_region"] = route_info["region"]
                    deals.append(f)

    deals.sort(key=lambda x: x["mileage_cost"])

    if args.json:
        print(json.dumps({"origin": origin, "deals": deals, "routes_scanned": scan_limit}, indent=2))
        return

    print(f"\n{'=' * 70}")
    print(f"  Award Deals from {origin} ({cabin}) — under {max_miles:,} miles")
    print(f"  Scanned {scan_limit}/{len(dest_codes)} nonstop routes")
    print(f"{'=' * 70}")

    if not deals:
        print(f"\n  No deals found under {max_miles:,} miles.")
    else:
        seen = set()
        for d in deals:
            key = (d["_dest"], d["carriers"], d["mileage_cost"], d["stops"])
            if key in seen:
                continue
            seen.add(key)
            miles = d["mileage_cost"]
            miles_str = f"{miles:,}" if isinstance(miles, int) else str(miles)
            stops = d["stops"]
            stop_str = "nonstop" if stops == 0 else f"{stops}stop"
            print(f"  {origin}->{d['_dest']} ({d['_region']}): {d['carriers']} | {miles_str}mi | ${d['taxes_usd']:.0f}tax | {stop_str} | {d['source']}")

    if scan_limit < len(dest_codes):
        remaining = [d for d in dest_codes[scan_limit:]]
        print(f"\n  {len(remaining)} routes not scanned: {', '.join(remaining[:10])}")
        print(f"  Re-run with specific routes using: vplan search --dest <CODE> --round-trip")

    print(f"\n{'─' * 70}")
    print(f"  Data from seats.aero (free tier, cached last ~60 days)")
    print()


def cmd_chase(args):
    action = args.chase_action
    profile = getattr(args, "profile", "") or None
    dump_raw = getattr(args, "dump_raw", False)

    from vplan_cli.scraper_chase import ChaseTravel

    if action == "login":
        _log("Opening Chase Travel — log in manually, then explore freely.")
        _log("API responses will be captured in the background.")
        _log("Press Ctrl+C when done.\n")
        with ChaseTravel(profile_dir=profile) as ct:
            ct.open_browser()
            ct.navigate_to_login()
            try:
                ct.wait_for_auth(timeout_seconds=600)
                _log("\nAuthenticated! Browse Chase Travel to capture results.")
                _log("Press Ctrl+C when done.\n")
                while True:
                    time.sleep(5)
                    captured = ct.get_captured()
                    if captured:
                        _log(f"  {len(captured)} result(s) captured so far...")
            except KeyboardInterrupt:
                results = ct.get_captured()
                if results:
                    print(json.dumps(results, indent=2))
                else:
                    _log("No results captured.")
                    if dump_raw:
                        raw = ct.get_raw_api_responses()
                        if raw:
                            print(json.dumps(raw, indent=2, default=str))
        return

    if action == "flights":
        origin = getattr(args, "origin", "IAD")
        dest = getattr(args, "dest", "")

        _log(f"Chase Travel flight search: {origin} -> {dest}")
        _log("A browser will open — log in to your Chase account.")
        _log("Then search for flights. Results will be captured automatically.\n")

        with ChaseTravel(profile_dir=profile) as ct:
            results = ct.interactive_session("flights", origin=origin, destination=dest)

        if not results:
            _log("No flight results captured.")
            return

        flights = [r for r in results if r.get("type") == "flight"]
        if args.json:
            print(json.dumps({"flights": flights, "origin": origin, "destination": dest}, indent=2))
        else:
            print(f"\n{'=' * 70}")
            print(f"  Chase Travel Flights: {origin} -> {dest}")
            print(f"  {len(flights)} result(s) captured")
            print(f"{'=' * 70}")
            for f in flights:
                pts = f.get("ur_points", 0)
                pts_str = f"{pts:,} UR" if pts else "N/A"
                cash = f.get("cash_price_usd", 0)
                cash_str = f"${cash:,.0f}" if cash else "N/A"
                carrier = f.get("carrier", "?")
                stops = f.get("stops", 0)
                stop_str = "nonstop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"
                duration = f.get("duration", "")
                print(f"\n    {carrier} | {pts_str} | {cash_str} cash | {stop_str} | {duration}")
                if f.get("departure"):
                    print(f"      dep {f['departure'][:16]} → arr {f.get('arrival', '')[:16]}")
            print()

    elif action == "hotels":
        city = getattr(args, "city", "")
        checkin = getattr(args, "checkin", "")
        checkout = getattr(args, "checkout", "")

        _log(f"Chase Travel hotel search: {city}")
        _log("A browser will open — log in to your Chase account.")
        _log("Then search for hotels. Results will be captured automatically.\n")

        with ChaseTravel(profile_dir=profile) as ct:
            results = ct.interactive_session("hotels", city=city, checkin=checkin, checkout=checkout)

        if not results:
            _log("No hotel results captured.")
            return

        hotels = [r for r in results if r.get("type") == "hotel"]
        if args.json:
            print(json.dumps({"hotels": hotels, "city": city}, indent=2))
        else:
            print(f"\n{'=' * 70}")
            print(f"  Chase Travel Hotels: {city}")
            print(f"  {len(hotels)} result(s) captured")
            print(f"{'=' * 70}")
            for h in hotels:
                name = h.get("name", "?")[:40]
                pts = h.get("ur_points", 0)
                pts_str = f"{pts:,} UR" if pts else "N/A"
                nightly = h.get("nightly_usd", 0)
                nightly_str = f"${nightly:,.0f}/nt" if nightly else ""
                total = h.get("total_usd", 0)
                total_str = f"${total:,.0f} total" if total else ""
                rating = h.get("rating", 0)
                stars = f"{'★' * int(rating)}" if rating else ""
                print(f"\n    {name:<40} {stars}")
                print(f"      {pts_str} | {nightly_str} | {total_str}")
            print()


def cmd_alert(args):
    origin = args.origin
    dest = args.dest
    max_miles = args.max_miles
    cabin = args.cabin

    _log(f"Searching for awards under {max_miles:,} miles: {origin} -> {dest} ({cabin})...")

    from vplan_cli.scraper_seats import SeatsAeroScraper

    with SeatsAeroScraper(headless=True) as scraper:
        results = scraper.search_flights(origin, dest, cabin, 50)

    cheap = [f for f in results if isinstance(f["mileage_cost"], int) and f["mileage_cost"] <= max_miles]

    if not cheap:
        print(f"No awards found under {max_miles:,} miles for {origin}->{dest}")
        return

    msg_lines = [f"Award Alert: {origin}->{dest} ({cabin})"]
    seen = set()
    for f in cheap:
        key = (f["carriers"], f["mileage_cost"], f["stops"])
        if key in seen:
            continue
        seen.add(key)
        stops = f["stops"]
        stop_str = "nonstop" if stops == 0 else f"{stops}stop"
        msg_lines.append(f"  {f['carriers']} {f['mileage_cost']:,}mi ${f['taxes_usd']:.0f}tax {stop_str} {f['duration']} ({f['source']})")

    message = "\n".join(msg_lines)
    print(message)


def main():
    parser = argparse.ArgumentParser(
        prog="vplan",
        description="OpenClaw Vacation Planner — points optimization, award search, trip research",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    sp_research = subparsers.add_parser("research", help="Research a destination")
    sp_research.add_argument("destination", help="Destination name")
    sp_research.add_argument("--nights", type=int, default=7, help="Number of nights")
    sp_research.add_argument("--month", default="", help="Travel month")
    sp_research.add_argument("--json", action="store_true", help="Output as JSON")
    sp_research.set_defaults(func=cmd_research)

    sp_points = subparsers.add_parser("points", help="Calculate optimal points redemption")
    sp_points.add_argument("--hotel-rate", type=float, default=0, help="Nightly hotel rate in USD")
    sp_points.add_argument("--flights-usd", type=float, default=0, help="Total flights cost in USD")
    sp_points.add_argument("--json", action="store_true", help="Output as JSON")
    sp_points.set_defaults(func=cmd_points)

    sp_awards = subparsers.add_parser("awards", help="Search award availability")
    sp_awards.add_argument("--origin", default="IAD", help="Origin airport code")
    sp_awards.add_argument("--dest", required=True, help="Destination airport code")
    sp_awards.add_argument("--month", default="", help="Travel month")
    sp_awards.add_argument("--live", action="store_true", help="Include live availability from seats.aero")
    sp_awards.add_argument("--json", action="store_true", help="Output as JSON")
    sp_awards.set_defaults(func=cmd_awards)

    sp_visa = subparsers.add_parser("visa", help="Check visa/entry requirements")
    sp_visa.add_argument("--destination", required=True, help="Country name")
    sp_visa.add_argument("--json", action="store_true", help="Output as JSON")
    sp_visa.set_defaults(func=cmd_visa)

    sp_save = subparsers.add_parser("save", help="Save trip idea to Karakeep")
    sp_save.add_argument("--title", required=True, help="Trip title")
    sp_save.add_argument("--url", required=True, help="URL to save")
    sp_save.add_argument("--notes", default="", help="Notes about the trip")
    sp_save.add_argument("--tags", default="travel,trip-idea", help="Comma-separated tags")
    sp_save.add_argument("--json", action="store_true", help="Output as JSON")
    sp_save.set_defaults(func=cmd_save)

    sp_itin = subparsers.add_parser("itinerary", help="Generate day-by-day itinerary")
    sp_itin.add_argument("destination", help="Destination name")
    sp_itin.add_argument("--nights", type=int, default=7, help="Number of nights")
    sp_itin.add_argument("--ages", default="8,10,14", help="Comma-separated kid ages")
    sp_itin.add_argument("--json", action="store_true", help="Output as JSON")
    sp_itin.set_defaults(func=cmd_itinerary)

    sp_search = subparsers.add_parser("search", help="Search live award flight availability")
    sp_search.add_argument("--origin", default="IAD", help="Origin airport code")
    sp_search.add_argument("--dest", required=True, help="Destination airport code")
    sp_search.add_argument("--cabin", default="economy", choices=["economy", "premium", "business", "first"], help="Cabin class")
    sp_search.add_argument("--limit", type=int, default=50, help="Max results")
    sp_search.add_argument("--round-trip", action="store_true", help="Search both outbound and return flights")
    sp_search.add_argument("--detailed", "-d", action="store_true", help="Show flight numbers, departure/arrival times")
    sp_search.add_argument("--json", action="store_true", help="Output as JSON")
    sp_search.set_defaults(func=cmd_search)

    sp_login = subparsers.add_parser("login", help="Store login credentials for a service")
    sp_login.add_argument("service", help="Service name (e.g. hyatt, united)")
    sp_login.add_argument("--username", help="Username/email")
    sp_login.set_defaults(func=cmd_login)

    sp_trips = subparsers.add_parser("trips", help="Manage saved trips")
    sp_trips.add_argument("trips_action", choices=["list", "show", "save", "delete"])
    sp_trips.add_argument("name", nargs="?", default="")
    sp_trips.add_argument("--destination", default="")
    sp_trips.add_argument("--month", default="")
    sp_trips.add_argument("--notes", default="")
    sp_trips.add_argument("--json", action="store_true")
    sp_trips.set_defaults(func=cmd_trips)

    sp_hotels = subparsers.add_parser("hotels", help="Search hotels and award pricing")
    sp_hotels.add_argument("destination", help="Destination city")
    sp_hotels.add_argument("--checkin", required=True, help="Check-in date (YYYY-MM-DD)")
    sp_hotels.add_argument("--checkout", required=True, help="Check-out date (YYYY-MM-DD)")
    sp_hotels.add_argument("--nights", type=int, required=True, help="Number of nights")
    sp_hotels.add_argument("--country", default="", help="ISO country code for LiteAPI (e.g. MX, FR, JP)")
    sp_hotels.add_argument("--json", action="store_true")
    sp_hotels.set_defaults(func=cmd_hotels)

    sp_plan = subparsers.add_parser("plan", help="Full trip planning pipeline")
    sp_plan.add_argument("destination", help="Destination name")
    sp_plan.add_argument("--dest-code", required=True, help="Destination airport code")
    sp_plan.add_argument("--month", required=True, help="Travel month (e.g. June)")
    sp_plan.add_argument("--nights", type=int, default=7, help="Number of nights")
    sp_plan.set_defaults(func=cmd_plan)

    sp_multi = subparsers.add_parser("multicity", help="Multi-city award search (A→B→C routing)")
    sp_multi.add_argument("stops", nargs="+", help="Airport codes in order (e.g., IAD LHR BCN IAD)")
    sp_multi.add_argument("--cabin", default="economy", choices=["economy", "premium", "business", "first"])
    sp_multi.add_argument("--live", action="store_true", help="Include live seats.aero data per segment")
    sp_multi.add_argument("--detailed", "-d", action="store_true", help="Show flight details")
    sp_multi.add_argument("--json", action="store_true")
    sp_multi.set_defaults(func=cmd_multicity)

    sp_watch = subparsers.add_parser("watch", help="Manage saved flight searches (watchlist)")
    sp_watch.add_argument("watch_action", choices=["add", "list", "remove", "run"], help="add/list/remove/run watchlist items")
    sp_watch.add_argument("--origin", default="IAD", help="Origin airport code")
    sp_watch.add_argument("--dest", default="", help="Destination airport code")
    sp_watch.add_argument("--cabin", default="economy", help="Cabin class")
    sp_watch.add_argument("--max-miles", type=int, default=50000, help="Max miles to alert on")
    sp_watch.add_argument("--name", default="", help="Watchlist entry name")
    sp_watch.add_argument("--index", type=int, default=0, help="Index to remove (1-based)")
    sp_watch.add_argument("--log", default="", help="Append results to log file (for cron)")
    sp_watch.add_argument("--json", action="store_true")
    sp_watch.set_defaults(func=cmd_watch)

    sp_calendar = subparsers.add_parser("calendar", help="Show award availability calendar (month grid)")
    sp_calendar.add_argument("--origin", default="IAD", help="Origin airport code")
    sp_calendar.add_argument("--dest", required=True, help="Destination airport code")
    sp_calendar.add_argument("--cabin", default="economy", choices=["economy", "premium", "business", "first"])
    sp_calendar.add_argument("--json", action="store_true")
    sp_calendar.set_defaults(func=cmd_calendar)

    sp_compare = subparsers.add_parser("compare", help="Compare destinations side by side")
    sp_compare.add_argument("destinations", nargs="+", help="Destination names or codes (at least 2)")
    sp_compare.add_argument("--month", default="", help="Travel month")
    sp_compare.add_argument("--origin", default="IAD", help="Origin airport code")
    sp_compare.add_argument("--json", action="store_true")
    sp_compare.set_defaults(func=cmd_compare)

    sp_config = subparsers.add_parser("config", help="View or edit your configuration (points, family)")
    sp_config.add_argument("config_action", choices=["show", "set", "reset"], help="show: display config, set: update a value, reset: restore defaults")
    sp_config.add_argument("key", nargs="?", default="", help="Config key (chase_ur, united, delta, name, adults, kids, airports)")
    sp_config.add_argument("value", nargs="?", default="", help="New value")
    sp_config.add_argument("--json", action="store_true")
    sp_config.set_defaults(func=cmd_config)

    sp_ask = subparsers.add_parser("ask", help="Ask the AI travel advisor a question")
    sp_ask.add_argument("query", nargs="+", help="Your travel question (e.g., 'beach trip from IAD in April')")
    sp_ask.add_argument("--live", action="store_true", help="Include live seats.aero data (slower)")
    sp_ask.add_argument("--model", default="gpt-4o-mini", help="LLM model name (default: gpt-4o-mini)")
    sp_ask.add_argument("--verbose", "-v", action="store_true", help="Show debug info")
    sp_ask.add_argument("--export", metavar="FILE", help="Export response to file (.md or .json)")
    sp_ask.add_argument("--copy", action="store_true", help="Copy response to clipboard")
    sp_ask.set_defaults(func=cmd_ask)

    sp_chat = subparsers.add_parser("chat", help="Interactive AI travel advisor chat")
    sp_chat.add_argument("--model", default="gpt-4o-mini", help="LLM model name (default: gpt-4o-mini)")
    sp_chat.add_argument("--verbose", "-v", action="store_true", help="Show debug info")
    sp_chat.set_defaults(func=cmd_chat)

    sp_deals = subparsers.add_parser("deals", help="Scan nonstop routes for cheap award deals")
    sp_deals.add_argument("--origin", default="IAD", help="Origin airport code")
    sp_deals.add_argument("--max-miles", type=int, default=30000, help="Max miles threshold (default: 30000)")
    sp_deals.add_argument("--cabin", default="economy", choices=["economy", "premium", "business", "first"])
    sp_deals.add_argument("--region", default="", help="Filter by region (caribbean, europe, asia, etc.)")
    sp_deals.add_argument("--json", action="store_true")
    sp_deals.set_defaults(func=cmd_deals)

    sp_alert = subparsers.add_parser("alert", help="Search for cheap award flights and send alerts")
    sp_alert.add_argument("--origin", default="IAD", help="Origin airport code")
    sp_alert.add_argument("--dest", required=True, help="Destination airport code")
    sp_alert.add_argument("--max-miles", type=int, required=True, help="Max miles threshold")
    sp_alert.add_argument("--cabin", default="economy", choices=["economy", "premium", "business", "first"])
    sp_alert.set_defaults(func=cmd_alert)

    sp_chase = subparsers.add_parser("chase", help="Search Chase Travel portal (opens browser for manual login)")
    sp_chase.add_argument("chase_action", choices=["login", "flights", "hotels"],
                          help="login: authenticate and browse; flights: search flights; hotels: search hotels")
    sp_chase.add_argument("--origin", default="IAD", help="Flight origin airport code")
    sp_chase.add_argument("--dest", default="", help="Flight destination airport code")
    sp_chase.add_argument("--city", default="", help="Hotel search city")
    sp_chase.add_argument("--checkin", default="", help="Hotel check-in date (YYYY-MM-DD)")
    sp_chase.add_argument("--checkout", default="", help="Hotel check-out date (YYYY-MM-DD)")
    sp_chase.add_argument("--profile", default="", help="Browser profile directory (persists login session)")
    sp_chase.add_argument("--dump-raw", action="store_true", help="Show raw API responses (for debugging)")
    sp_chase.add_argument("--json", action="store_true")
    sp_chase.set_defaults(func=cmd_chase)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)
