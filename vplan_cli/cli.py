import argparse
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

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
    save_credentials,
    save_trip,
)


def _log(msg: str):
    print(msg, file=sys.stderr)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA})
    return s


# ---------------------------------------------------------------------------
# vplan research
# ---------------------------------------------------------------------------

RESEARCH_SOURCES = [
    {
        "name": "Wikivoyage",
        "url_template": "https://en.wikivoyage.org/wiki/{query}",
        "search_url": "https://en.wikivoyage.org/w/index.php?search={query}&fulltext=1",
    },
    {
        "name": "Wikitravel",
        "url_template": "https://wikitravel.org/en/{query}",
    },
]


def _resolve_wikivoyage_title(destination: str, s: requests.Session) -> str | None:
    try:
        r = s.get(
            "https://en.wikivoyage.org/w/api.php",
            params={"action": "opensearch", "search": destination, "limit": 1, "format": "json"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if len(data) >= 4 and data[1]:
                return data[1][0]
    except requests.RequestException:
        pass
    return None


def _scrape_wikivoyage(destination: str, s: requests.Session) -> dict:
    title = _resolve_wikivoyage_title(destination, s) or destination
    slug = title.replace(" ", "_")
    url = f"https://en.wikivoyage.org/wiki/{requests.utils.quote(slug)}"
    try:
        r = s.get(url, timeout=15)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "html.parser")
        parser_output = soup.select_one("#mw-content-text .mw-parser-output")
        if not parser_output:
            return {}

        sections = {}
        current_heading = "Overview"
        current_text = []

        for el in parser_output.descendants:
            tag_name = getattr(el, "name", None)
            if tag_name in ("h2", "h3"):
                heading_el = el.select_one(".mw-headline") or el.select_one("[id]")
                heading_text = heading_el.get_text(strip=True) if heading_el else el.get_text(strip=True)
                heading_text = heading_text.replace("[edit]", "").strip()
                if heading_text:
                    if current_text:
                        sections[current_heading] = "\n".join(current_text).strip()
                    current_heading = heading_text
                    current_text = []
            elif tag_name == "p":
                text = el.get_text(strip=True)
                if text and len(text) > 10:
                    current_text.append(text)

        if current_text:
            sections[current_heading] = "\n".join(current_text).strip()

        return sections

    except requests.RequestException:
        return {}


MONTH_NUMBERS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _geocode(destination: str, s: requests.Session) -> tuple[float, float] | None:
    try:
        r = s.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": destination, "count": 1, "format": "json"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("results"):
                loc = data["results"][0]
                return loc["latitude"], loc["longitude"]
    except requests.RequestException:
        pass
    return None


def _fetch_weather(destination: str, month: str, s: requests.Session) -> str:
    coords = _geocode(destination, s)
    if not coords:
        return f"Could not find location for '{destination}'."

    lat, lon = coords
    month_num = MONTH_NUMBERS.get(month.lower().strip(), 0)
    if not month_num:
        return f"Unknown month '{month}'. Use full name (e.g. June) or abbreviation (e.g. Jun)."

    year = datetime.now().year - 1
    start_date = f"{year}-{month_num:02d}-01"
    if month_num == 12:
        end_date = f"{year}-12-31"
    else:
        next_month_start = datetime(year, month_num + 1, 1)
        last_day = next_month_start - timedelta(days=1)
        end_date = last_day.strftime("%Y-%m-%d")

    try:
        r = s.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start_date,
                "end_date": end_date,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            },
            timeout=15,
        )
        if r.status_code != 200:
            return f"Weather API returned {r.status_code}. Try again later."

        data = r.json()
        daily = data.get("daily", {})
        temps_max = daily.get("temperature_2m_max", [])
        temps_min = daily.get("temperature_2m_min", [])
        precip = daily.get("precipitation_sum", [])

        if not temps_max:
            return f"No weather data available for {destination} in {month}."

        avg_high_c = sum(temps_max) / len(temps_max)
        avg_low_c = sum(temps_min) / len(temps_min)
        avg_high_f = avg_high_c * 9 / 5 + 32
        avg_low_f = avg_low_c * 9 / 5 + 32
        total_rain_mm = sum(precip)
        total_rain_in = total_rain_mm / 25.4
        rainy_days = sum(1 for p in precip if p > 1.0)

        return (
            f"Avg High: {avg_high_f:.0f}°F ({avg_high_c:.1f}°C) | "
            f"Avg Low: {avg_low_f:.0f}°F ({avg_low_c:.1f}°C) | "
            f"Rain: {total_rain_in:.1f}in ({total_rain_mm:.0f}mm) over {rainy_days} days"
        )
    except requests.RequestException:
        return f"Could not fetch weather data for {destination}."


def _family_suitability(destination: str, sections: dict) -> list:
    tips = []
    all_text = " ".join(sections.values()).lower()

    kid_keywords = ["family", "children", "kids", "playground", "water park", "beach", "zoo",
                    "aquarium", "theme park", "amusement", "snorkel"]
    found = [kw for kw in kid_keywords if kw in all_text]
    if found:
        tips.append(f"Family-friendly mentions: {', '.join(found)}")
    else:
        tips.append("Limited family-specific info found — research kid-friendly activities separately.")

    safety_keywords = ["safe", "danger", "crime", "scam", "avoid", "caution", "warning"]
    safety_found = [kw for kw in safety_keywords if kw in all_text]
    if safety_found:
        tips.append(f"Safety-related mentions: {', '.join(safety_found)}")

    return tips


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

    sections = _scrape_wikivoyage(destination, s)
    if sections:
        result["overview"] = sections.get("Overview", sections.get("Understand", ""))[:1000]

        for key in ["See", "Do", "Eat", "Drink", "Sleep", "Stay safe", "Get in", "Get around"]:
            if key in sections:
                result[key.lower().replace(" ", "_")] = sections[key][:800]
    else:
        result["overview"] = f"Could not fetch guide for {destination}. Try Wikivoyage or TripAdvisor directly."

    result["weather"] = _fetch_weather(destination, month, s)
    result["family_tips"] = _family_suitability(destination, sections)

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


# ---------------------------------------------------------------------------
# vplan points
# ---------------------------------------------------------------------------

TRANSFER_PARTNERS = {
    "chase_ur_to_hyatt": {"name": "Chase UR -> Hyatt", "ratio": 1.0, "type": "hotel", "cpp_target": 2.0},
    "chase_ur_to_united": {"name": "Chase UR -> United", "ratio": 1.0, "type": "airline", "cpp_target": 1.5},
    "chase_ur_to_southwest": {"name": "Chase UR -> Southwest", "ratio": 1.0, "type": "airline", "cpp_target": 1.4},
    "chase_ur_portal": {"name": "Chase Travel Portal (1.5x)", "ratio": 1.5, "type": "portal", "cpp_target": 1.5},
    "united_direct": {"name": "United MileagePlus", "ratio": 1.0, "type": "airline", "cpp_target": 1.3},
    "delta_direct": {"name": "Delta SkyMiles", "ratio": 1.0, "type": "airline", "cpp_target": 1.2},
    "delta_to_flying_blue": {"name": "Delta -> Flying Blue (indirect)", "ratio": 1.0, "type": "airline", "cpp_target": 1.5},
}


def _calculate_redemption(hotel_rate_usd: float, flights_usd: float) -> list:
    total_cash = hotel_rate_usd + flights_usd
    options = []

    # Chase Travel Portal (1.5x) — covers both hotel + flights
    chase = POINTS["chase_ur"]
    portal_points_needed = int(total_cash / 0.015)
    if portal_points_needed <= chase["balance"]:
        options.append({
            "strategy": "Chase Travel Portal (1.5x)",
            "points_used": f"{portal_points_needed:,} Chase UR",
            "cash_saved": f"${total_cash:,.0f}",
            "cpp": 1.5,
            "notes": f"Covers full trip. {chase['balance'] - portal_points_needed:,} UR remaining.",
            "priority": "HIGH — UR expires Oct 2027, use first",
        })

    # Chase UR -> Hyatt for hotel, cash for flights
    if hotel_rate_usd > 0:
        hyatt_points_per_night = int(hotel_rate_usd / 0.02)  # ~2cpp target
        hyatt_points_total = hyatt_points_per_night
        if hyatt_points_total <= chase["balance"]:
            cpp_actual = (hotel_rate_usd / hyatt_points_total) * 100 if hyatt_points_total else 0
            options.append({
                "strategy": "Chase UR -> Hyatt (hotel) + cash (flights)",
                "points_used": f"{hyatt_points_total:,} Chase UR (transferred to Hyatt)",
                "cash_spent": f"${flights_usd:,.0f} (flights)",
                "cash_saved": f"${hotel_rate_usd:,.0f} (hotel)",
                "cpp": round(cpp_actual, 1),
                "notes": "Hyatt transfer is best hotel value. Check Hyatt availability at destination.",
            })

    # United miles for flights
    united = POINTS["united"]
    if flights_usd > 0:
        united_miles_est = int(flights_usd / 0.013)  # ~1.3cpp
        if united_miles_est <= united["balance"]:
            cpp_actual = (flights_usd / united_miles_est) * 100 if united_miles_est else 0
            options.append({
                "strategy": "United miles (flights) + cash (hotel)",
                "points_used": f"{united_miles_est:,} United miles",
                "cash_spent": f"${hotel_rate_usd:,.0f} (hotel)",
                "cash_saved": f"${flights_usd:,.0f} (flights)",
                "cpp": round(cpp_actual, 1),
                "notes": f"Premier 1K gets upgrades + PlusPoints. {united['balance'] - united_miles_est:,} miles remaining.",
            })

    # Delta SkyMiles for flights
    delta = POINTS["delta"]
    if flights_usd > 0:
        delta_miles_est = int(flights_usd / 0.012)  # ~1.2cpp
        if delta_miles_est <= delta["balance"]:
            cpp_actual = (flights_usd / delta_miles_est) * 100 if delta_miles_est else 0
            options.append({
                "strategy": "Delta SkyMiles (flights) + cash (hotel)",
                "points_used": f"{delta_miles_est:,} Delta SkyMiles",
                "cash_spent": f"${hotel_rate_usd:,.0f} (hotel)",
                "cash_saved": f"${flights_usd:,.0f} (flights)",
                "cpp": round(cpp_actual, 1),
                "notes": f"{delta['balance'] - delta_miles_est:,} SkyMiles remaining.",
            })

    # Combo: Chase UR -> United (flights) + Chase UR -> Hyatt (hotel)
    if hotel_rate_usd > 0 and flights_usd > 0:
        united_pts = int(flights_usd / 0.015)
        hyatt_pts = int(hotel_rate_usd / 0.02)
        total_ur = united_pts + hyatt_pts
        if total_ur <= chase["balance"]:
            options.append({
                "strategy": "All points: UR -> United (flights) + UR -> Hyatt (hotel)",
                "points_used": f"{total_ur:,} Chase UR ({united_pts:,} -> United, {hyatt_pts:,} -> Hyatt)",
                "cash_spent": "$0",
                "cash_saved": f"${total_cash:,.0f}",
                "cpp": round((total_cash / total_ur) * 100, 1) if total_ur else 0,
                "notes": f"Zero cash trip! {chase['balance'] - total_ur:,} UR remaining.",
                "priority": "BEST VALUE — maximizes point value",
            })

    options.sort(key=lambda x: x.get("cpp", 0), reverse=True)
    return options


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
        "options": _calculate_redemption(hotel_rate, flights_usd),
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


# ---------------------------------------------------------------------------
# vplan awards
# ---------------------------------------------------------------------------

AWARD_CHARTS = {
    "united": {
        "americas": {"saver": 35000, "everyday": 60000},
        "europe": {"saver": 60000, "everyday": 100000},
        "asia": {"saver": 70000, "everyday": 120000},
        "oceania": {"saver": 70000, "everyday": 120000},
        "africa": {"saver": 75000, "everyday": 130000},
        "middle_east": {"saver": 70000, "everyday": 115000},
    },
    "delta": {
        "americas": {"low": 25000, "mid": 50000, "high": 80000},
        "europe": {"low": 50000, "mid": 85000, "high": 140000},
        "asia": {"low": 65000, "mid": 100000, "high": 170000},
        "oceania": {"low": 70000, "mid": 110000, "high": 180000},
    },
}

REGION_MAP = {
    "CUN": "americas", "MEX": "americas", "GDL": "americas", "SJD": "americas",
    "BOG": "americas", "LIM": "americas", "GRU": "americas", "EZE": "americas",
    "SCL": "americas", "PTY": "americas", "SJO": "americas", "MBJ": "americas",
    "NAS": "americas", "PUJ": "americas", "UVF": "americas", "YYZ": "americas",
    "YVR": "americas",
    "LHR": "europe", "CDG": "europe", "FCO": "europe", "BCN": "europe",
    "AMS": "europe", "FRA": "europe", "MUC": "europe", "ZRH": "europe",
    "LIS": "europe", "ATH": "europe", "IST": "europe", "DUB": "europe",
    "CPH": "europe", "ARN": "europe", "HEL": "europe", "PRG": "europe",
    "VIE": "europe", "WAW": "europe",
    "NRT": "asia", "HND": "asia", "ICN": "asia", "PEK": "asia",
    "PVG": "asia", "HKG": "asia", "SIN": "asia", "BKK": "asia",
    "DEL": "asia", "BOM": "asia", "MNL": "asia", "TPE": "asia",
    "KUL": "asia", "DPS": "asia",
    "SYD": "oceania", "MEL": "oceania", "AKL": "oceania", "NAN": "oceania",
    "PPT": "oceania",
    "JNB": "africa", "CPT": "africa", "NBO": "africa", "ADD": "africa",
    "CMN": "africa", "CAI": "africa",
    "DXB": "middle_east", "DOH": "middle_east", "AUH": "middle_east",
    "TLV": "middle_east", "AMM": "middle_east",
}


def _lookup_awards(origin: str, dest: str, month: str) -> dict:
    region = REGION_MAP.get(dest.upper(), "americas")

    result = {
        "route": f"{origin} -> {dest}",
        "region": region,
        "month": month,
        "programs": [],
        "search_links": [],
    }

    # United awards
    united_chart = AWARD_CHARTS["united"].get(region, {})
    if united_chart:
        saver = united_chart.get("saver", 0)
        everyday = united_chart.get("everyday", 0)
        result["programs"].append({
            "program": "United MileagePlus",
            "balance": f"{POINTS['united']['balance']:,} miles",
            "saver_rt": f"{saver * 2:,} miles (round trip)" if saver else "N/A",
            "everyday_rt": f"{everyday * 2:,} miles (round trip)" if everyday else "N/A",
            "family_of_5_saver": f"{saver * 2 * 5:,} miles" if saver else "N/A",
            "can_afford_saver": (saver * 2 * 5) <= POINTS["united"]["balance"] if saver else False,
            "notes": "Premier 1K: complimentary upgrades, PlusPoints for premium cabin",
        })

    # Chase UR -> United
    if united_chart:
        saver = united_chart.get("saver", 0)
        ur_needed = saver * 2 * 5
        result["programs"].append({
            "program": "Chase UR -> United (1:1 transfer)",
            "balance": f"{POINTS['chase_ur']['balance']:,} UR",
            "family_of_5_saver": f"{ur_needed:,} UR" if saver else "N/A",
            "can_afford": ur_needed <= POINTS["chase_ur"]["balance"] if saver else False,
            "notes": "Transfer 1:1 to United. Combine with existing United miles.",
        })

    # Delta awards
    delta_chart = AWARD_CHARTS["delta"].get(region, {})
    if delta_chart:
        low = delta_chart.get("low", 0)
        mid = delta_chart.get("mid", 0)
        result["programs"].append({
            "program": "Delta SkyMiles",
            "balance": f"{POINTS['delta']['balance']:,} SkyMiles",
            "low_rt": f"{low * 2:,} SkyMiles (round trip)" if low else "N/A",
            "mid_rt": f"{mid * 2:,} SkyMiles (round trip)" if mid else "N/A",
            "family_of_5_low": f"{low * 2 * 5:,} SkyMiles" if low else "N/A",
            "can_afford_low": (low * 2 * 5) <= POINTS["delta"]["balance"] if low else False,
        })

    result["search_links"] = [
        f"https://www.united.com/en/us/fsr/choose-flights?f={origin}&t={dest}&d={month}&tt=1&at=1&sc=7&px=5&taxng=1&newHP=True&clm=7&st=bestmatches&fareWheel=True",
        f"https://www.delta.com/flight-search/book-a-flight?cacheKeySuffix=a{dest}",
        f"https://point.me/?origin={origin}&destination={dest}",
        f"https://www.awardhacker.com/#{origin}-{dest}",
    ]

    return result


def cmd_awards(args):
    origin = args.origin
    dest = args.dest
    month = args.month
    live = getattr(args, "live", False)

    _log(f"Searching award availability: {origin} -> {dest} in {month}...")
    result = _lookup_awards(origin, dest, month)

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


# ---------------------------------------------------------------------------
# vplan visa
# ---------------------------------------------------------------------------

VISA_FREE = {
    "mexico": {"required": False, "max_days": 180, "doc": "US passport (6mo validity)", "notes": "Tourist card (FMM) issued on arrival or online pre-registration. No visa needed."},
    "canada": {"required": False, "max_days": 180, "doc": "US passport", "notes": "No visa needed for US citizens."},
    "united kingdom": {"required": False, "max_days": 180, "doc": "US passport", "notes": "No visa for tourism up to 6 months."},
    "france": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone — 90 days in any 180-day period."},
    "germany": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone — 90 days in any 180-day period."},
    "italy": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone — 90 days in any 180-day period."},
    "spain": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone — 90 days in any 180-day period. ETIAS required starting 2025."},
    "japan": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa for tourism. Visit Japan Web registration recommended."},
    "south korea": {"required": False, "max_days": 90, "doc": "US passport + K-ETA", "notes": "K-ETA (electronic travel authorization) required. Apply online 72hrs before."},
    "australia": {"required": True, "max_days": 90, "doc": "US passport + ETA", "notes": "Electronic Travel Authority (ETA) required. Apply via app. $20 AUD."},
    "thailand": {"required": False, "max_days": 30, "doc": "US passport", "notes": "Visa exemption for 30 days (extendable to 60)."},
    "costa rica": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa. Must show proof of return ticket."},
    "colombia": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa for tourism up to 90 days."},
    "peru": {"required": False, "max_days": 183, "doc": "US passport", "notes": "No visa for tourism. Reciprocity fee eliminated."},
    "brazil": {"required": True, "max_days": 90, "doc": "US passport + visa or e-visa", "notes": "E-visa available online. $80.90 fee. Valid 10 years."},
    "india": {"required": True, "max_days": 90, "doc": "US passport + e-visa", "notes": "E-tourist visa required. Apply online at indianvisaonline.gov.in. 30-day or 1-year options."},
    "china": {"required": True, "max_days": 30, "doc": "US passport + visa", "notes": "Tourist visa (L) required. Apply at Chinese embassy/consulate. 10-year multiple entry available."},
    "singapore": {"required": False, "max_days": 90, "doc": "US passport + SG Arrival Card", "notes": "No visa. Complete SG Arrival Card online 3 days before."},
    "new zealand": {"required": False, "max_days": 90, "doc": "US passport + NZeTA", "notes": "NZeTA required. Apply via app. $17 NZD + $35 IVL."},
    "iceland": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone — 90 days in 180-day period."},
    "portugal": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone. ETIAS coming 2025."},
    "greece": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone."},
    "turkey": {"required": True, "max_days": 90, "doc": "US passport + e-visa", "notes": "E-visa required. Apply at evisa.gov.tr. $50. Multiple entry."},
    "morocco": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa for tourism up to 90 days."},
    "south africa": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa for US citizens for stays under 90 days."},
    "egypt": {"required": True, "max_days": 30, "doc": "US passport + visa", "notes": "Visa on arrival at airport ($25) or e-visa online."},
    "argentina": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa. Reciprocity fee eliminated."},
    "chile": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa for tourism."},
    "dominican republic": {"required": False, "max_days": 30, "doc": "US passport + tourist card", "notes": "Tourist card ($10) usually included in airfare. E-Ticket form required."},
    "jamaica": {"required": False, "max_days": 30, "doc": "US passport", "notes": "No visa. Immigration form required."},
    "bahamas": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa. Bahamas Health Travel Visa may be required."},
    "bermuda": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa for tourism."},
    "aruba": {"required": False, "max_days": 30, "doc": "US passport + ED card", "notes": "No visa. Complete Embarkation/Disembarkation card online."},
    "netherlands": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone."},
    "switzerland": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone."},
    "norway": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone."},
    "sweden": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone."},
    "denmark": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone."},
    "ireland": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Not Schengen — separate entry. No visa."},
    "croatia": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone (joined 2023)."},
    "czech republic": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone."},
    "hungary": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone."},
    "poland": {"required": False, "max_days": 90, "doc": "US passport", "notes": "Schengen zone."},
    "indonesia": {"required": True, "max_days": 30, "doc": "US passport + VOA", "notes": "Visa on Arrival at airport. $35. Extendable 30 days."},
    "vietnam": {"required": True, "max_days": 45, "doc": "US passport + e-visa", "notes": "E-visa available for 45 days single entry. Apply online."},
    "cambodia": {"required": True, "max_days": 30, "doc": "US passport + e-visa or VOA", "notes": "E-visa ($36) or Visa on Arrival ($30)."},
    "philippines": {"required": False, "max_days": 30, "doc": "US passport", "notes": "No visa for 30 days. Extendable."},
    "malaysia": {"required": False, "max_days": 90, "doc": "US passport + MDAC", "notes": "No visa. Malaysia Digital Arrival Card required."},
    "taiwan": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa for tourism."},
    "israel": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa. ETA-IL may be required."},
    "jordan": {"required": True, "max_days": 30, "doc": "US passport + visa", "notes": "Visa on arrival at airport. ~40 JOD. Jordan Pass includes visa + Petra entrance."},
    "uae": {"required": False, "max_days": 30, "doc": "US passport", "notes": "No visa for 30 days. Extendable."},
    "qatar": {"required": False, "max_days": 30, "doc": "US passport", "notes": "Visa waiver for US citizens."},
    "fiji": {"required": False, "max_days": 120, "doc": "US passport", "notes": "No visa for tourism up to 4 months."},
    "maldives": {"required": False, "max_days": 30, "doc": "US passport", "notes": "Visa on arrival (free). Confirmed hotel booking required."},
    "kenya": {"required": True, "max_days": 90, "doc": "US passport + eTA", "notes": "Electronic Travel Authorization required. Apply at etakenya.go.ke."},
    "tanzania": {"required": True, "max_days": 90, "doc": "US passport + visa", "notes": "E-visa required. Apply online. $50."},
    "rwanda": {"required": True, "max_days": 30, "doc": "US passport + visa or VOA", "notes": "Visa on arrival or e-visa. $30."},
    "ethiopia": {"required": True, "max_days": 30, "doc": "US passport + e-visa", "notes": "E-visa required. Apply at evisa.gov.et."},
    "cuba": {"required": True, "max_days": 30, "doc": "US passport + tourist card + license", "notes": "Tourist card (visa) required. US citizens need OFAC general license category. Support for the Cuban People is most common."},
    "belize": {"required": False, "max_days": 30, "doc": "US passport", "notes": "No visa for tourism."},
    "guatemala": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa. CA-4 agreement (90 days shared with Honduras, El Salvador, Nicaragua)."},
    "panama": {"required": False, "max_days": 180, "doc": "US passport", "notes": "No visa for tourism up to 180 days."},
    "ecuador": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa for tourism."},
    "bolivia": {"required": True, "max_days": 30, "doc": "US passport + visa", "notes": "Visa required. Apply at embassy or VOA at some entry points. $160."},
    "uruguay": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa for tourism."},
    "paraguay": {"required": False, "max_days": 90, "doc": "US passport", "notes": "No visa for tourism."},
}


def _lookup_visa(country: str) -> dict:
    key = country.lower().strip()
    if key in VISA_FREE:
        info = VISA_FREE[key]
        return {
            "country": country,
            "visa_required": info["required"],
            "max_stay_days": info["max_days"],
            "documents": info["doc"],
            "notes": info["notes"],
            "source": "Built-in database (verify at travel.state.gov)",
        }

    return {
        "country": country,
        "visa_required": "Unknown",
        "notes": f"Country '{country}' not in built-in database. Check travel.state.gov for current requirements.",
        "links": [
            f"https://travel.state.gov/content/travel/en/international-travel/International-Travel-Country-Information-Pages/{country.replace(' ', '-')}.html",
            "https://www.iatatravelcentre.com/",
        ],
    }


def cmd_visa(args):
    country = args.destination

    _log(f"Checking visa requirements for {country} (US passport)...")
    result = _lookup_visa(country)

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


# ---------------------------------------------------------------------------
# vplan save
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# vplan itinerary
# ---------------------------------------------------------------------------

def _generate_itinerary(destination: str, nights: int, ages: list[int]) -> dict:
    age_groups = []
    for age in ages:
        if age < 5:
            age_groups.append("toddler")
        elif age < 10:
            age_groups.append("young child")
        elif age < 14:
            age_groups.append("tween")
        else:
            age_groups.append("teen")

    itinerary = {
        "destination": destination,
        "nights": nights,
        "travelers": f"2 adults + {len(ages)} kids (ages {', '.join(str(a) for a in ages)})",
        "age_groups": list(set(age_groups)),
        "days": [],
    }

    for day_num in range(1, nights + 2):
        day = {"day": day_num}

        if day_num == 1:
            day["theme"] = "Arrival & Settle In"
            day["activities"] = [
                "Arrive and check in to hotel/resort",
                "Explore immediate surroundings",
                "Grocery/supply run if self-catering",
                "Easy dinner near hotel — let kids decompress",
            ]
        elif day_num == nights + 1:
            day["theme"] = "Departure"
            day["activities"] = [
                "Pack and check out",
                "Last breakfast spot",
                "Airport transfer — allow extra time with kids",
            ]
        else:
            day["theme"] = f"Day {day_num} — Explore"
            day["activities"] = [
                f"Morning: Top attraction/activity for {destination}",
                "Mid-morning: Snack break (important with kids)",
                "Lunch at local restaurant",
                "Afternoon: Beach/pool time or secondary activity",
                "Late afternoon: Rest at hotel (nap time for younger ones)",
                "Evening: Dinner — mix upscale and casual",
            ]

            if "young child" in age_groups or "toddler" in age_groups:
                day["kid_tips"] = [
                    "Build in downtime — young kids need breaks",
                    "Pack snacks and water",
                    "Have a backup indoor activity for meltdowns",
                ]

            if "tween" in age_groups or "teen" in age_groups:
                day["teen_tips"] = [
                    "Let older kids choose one activity per day",
                    "Consider splitting up — adults + younger vs teens doing adventure activity",
                ]

        itinerary["days"].append(day)

    itinerary["packing_tips"] = [
        "Sunscreen + hats for all kids",
        "Snorkeling gear if beach destination",
        "Travel games / tablets for transit",
        "First aid kit — bandaids, Tylenol, Benadryl",
        "Copies of all passports",
    ]

    return itinerary


def cmd_itinerary(args):
    destination = args.destination
    nights = args.nights
    ages = [int(a.strip()) for a in args.ages.split(",")]

    _log(f"Generating itinerary: {destination}, {nights} nights, kids ages {ages}...")
    result = _generate_itinerary(destination, nights, ages)

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


# ---------------------------------------------------------------------------
# vplan search (live award search via seats.aero)
# ---------------------------------------------------------------------------

def cmd_search(args):
    origin = args.origin
    dest = args.dest
    cabin = args.cabin
    limit = args.limit

    _log(f"Searching award flights: {origin} -> {dest} ({cabin})...")
    _log("This uses seats.aero and requires a browser — may take 15-30 seconds...")

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

        seen = set()
        for flight in results:
            key = (flight["carriers"], flight["mileage_cost"], flight["stops"], flight["duration"])
            if key in seen:
                continue
            seen.add(key)

            miles = flight["mileage_cost"]
            miles_str = f"{miles:,}" if isinstance(miles, int) else str(miles)
            seats = flight["remaining_seats"]
            seats_str = f"{seats} seats" if seats else ""
            taxes = flight["taxes_usd"]
            stops = flight["stops"]
            stop_str = "nonstop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"
            duration = flight["duration"]
            source = flight["source"]
            carriers = flight["carriers"]

            print(f"\n  {carriers} | {miles_str} miles | ${taxes:.0f} tax | {stop_str} | {duration}")
            if seats_str:
                print(f"    {seats_str} remaining | via {source}")
            else:
                print(f"    via {source}")

        print(f"\n{'─' * 70}")
        print(f"  Data from seats.aero (free tier, last 60 days)")
        print(f"  For real-time booking, check airline sites directly.")
        print()


# ---------------------------------------------------------------------------
# vplan login (store credentials)
# ---------------------------------------------------------------------------

def cmd_login(args):
    import getpass

    service = args.service
    username = args.username or input(f"{service} username/email: ")
    password = getpass.getpass(f"{service} password: ")

    save_credentials(service, username, password)
    _log(f"Credentials saved for {service} in ~/.vplan/credentials.json")
    print(f"Saved {service} login for {username}")


# ---------------------------------------------------------------------------
# vplan trips (persistent trip storage)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# vplan hotels (hotel search with links)
# ---------------------------------------------------------------------------

HYATT_CATEGORIES = {
    1: {"points": 3500, "label": "Category 1"},
    2: {"points": 6500, "label": "Category 2"},
    3: {"points": 9000, "label": "Category 3"},
    4: {"points": 15000, "label": "Category 4"},
    5: {"points": 20000, "label": "Category 5"},
    6: {"points": 25000, "label": "Category 6"},
    7: {"points": 30000, "label": "Category 7"},
    8: {"points": 40000, "label": "Category 8"},
}


def _search_hotels_liteapi(city: str, country_code: str, checkin: str, checkout: str,
                           adults: int = 2, children: int = 3) -> list[dict]:
    if not LITEAPI_KEY:
        return []

    try:
        r = requests.post(
            "https://api.liteapi.travel/v3.0/hotels/rates",
            headers={"X-API-Key": LITEAPI_KEY, "Content-Type": "application/json"},
            json={
                "checkin": checkin,
                "checkout": checkout,
                "currency": "USD",
                "guestNationality": "US",
                "occupancies": [{"adults": adults, "children": children}],
                "cityName": city,
                "countryCode": country_code,
                "limit": 20,
            },
            timeout=15,
        )
        if r.status_code != 200:
            _log(f"LiteAPI returned {r.status_code}: {r.text[:200]}")
            return []

        data = r.json()
        hotels_raw = data.get("data", {}).get("hotels", [])
        if not hotels_raw:
            hotels_raw = data.get("data", [])

        hotels = []
        for h in hotels_raw[:20]:
            name = h.get("name", h.get("hotelName", "Unknown"))
            rating = h.get("rating", h.get("starRating", ""))
            address = h.get("address", "")

            rates = h.get("rates", h.get("roomTypes", []))
            best_rate = None
            for rate in (rates if isinstance(rates, list) else [rates]):
                price = rate.get("totalPrice", rate.get("retailRate", {}).get("total", [{}]))
                if isinstance(price, list):
                    price = price[0].get("amount") if price else None
                elif isinstance(price, dict):
                    price = price.get("amount", price.get("total"))
                if price is not None:
                    try:
                        price = float(price)
                    except (ValueError, TypeError):
                        continue
                    if best_rate is None or price < best_rate:
                        best_rate = price

            hotels.append({
                "name": name,
                "rating": rating,
                "address": address,
                "price_usd": best_rate,
                "room_type": rates[0].get("roomType", rates[0].get("name", "")) if rates else "",
            })

        hotels.sort(key=lambda x: x["price_usd"] if x["price_usd"] is not None else 999999)
        return hotels

    except requests.RequestException as e:
        _log(f"LiteAPI request failed: {e}")
        return []


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
        live = _search_hotels_liteapi(destination, country_code, checkin, checkout)
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


# ---------------------------------------------------------------------------
# vplan plan (full pipeline)
# ---------------------------------------------------------------------------

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

    # 1. Research
    _log(f"\n[1/5] Researching {destination}...")
    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA})

    wiki = _scrape_wikivoyage(destination, s)
    weather = _fetch_weather(destination, month, s)
    trip_data["overview"] = wiki.get("Overview", "")[:500]
    trip_data["weather"] = weather

    print(f"\n  Overview: {trip_data['overview'][:200]}...")
    print(f"  Weather: {weather}")

    # 2. Visa
    _log(f"\n[2/5] Checking entry requirements...")
    visa = _lookup_visa(destination)
    trip_data["visa"] = visa.get("summary", "Check entry requirements")

    print(f"  Visa: {trip_data['visa'][:150]}")

    # 3. Award flights (static chart)
    _log(f"\n[3/5] Checking award availability for {dest_code}...")
    awards = _lookup_awards("IAD", dest_code, month)
    trip_data["award_programs"] = awards.get("programs", [])

    for prog in awards.get("programs", [])[:3]:
        print(f"  {prog['program']}: {prog.get('balance', 'N/A')}")

    # 4. Points optimization
    _log(f"\n[4/5] Calculating points strategy...")
    trip_data["points_balances"] = {
        "chase_ur": f"{POINTS['chase_ur']['balance']:,}",
        "united": f"{POINTS['united']['balance']:,}",
        "delta": f"{POINTS['delta']['balance']:,}",
    }

    # 5. Itinerary
    _log(f"\n[5/5] Generating itinerary...")
    ages = [int(a) for a in "8,10,14".split(",")]
    itinerary = _generate_itinerary(destination, nights, ages)
    trip_data["itinerary_days"] = len(itinerary.get("days", []))

    print(f"  Itinerary: {trip_data['itinerary_days']} days planned")

    # Save trip
    path = save_trip(destination, trip_data)
    print(f"\n{'─' * 70}")
    print(f"  Trip saved to {path}")
    print(f"  View with: vplan trips show {destination.lower().replace(' ', '-')}")
    print()


# ---------------------------------------------------------------------------
# vplan alert (cheap award search)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="vplan",
        description="OpenClaw Vacation Planner — points optimization, award search, trip research",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # research
    sp_research = subparsers.add_parser("research", help="Research a destination")
    sp_research.add_argument("destination", help="Destination name")
    sp_research.add_argument("--nights", type=int, default=7, help="Number of nights")
    sp_research.add_argument("--month", default="", help="Travel month")
    sp_research.add_argument("--json", action="store_true", help="Output as JSON")
    sp_research.set_defaults(func=cmd_research)

    # points
    sp_points = subparsers.add_parser("points", help="Calculate optimal points redemption")
    sp_points.add_argument("--hotel-rate", type=float, default=0, help="Nightly hotel rate in USD")
    sp_points.add_argument("--flights-usd", type=float, default=0, help="Total flights cost in USD")
    sp_points.add_argument("--json", action="store_true", help="Output as JSON")
    sp_points.set_defaults(func=cmd_points)

    # awards
    sp_awards = subparsers.add_parser("awards", help="Search award availability")
    sp_awards.add_argument("--origin", default="IAD", help="Origin airport code")
    sp_awards.add_argument("--dest", required=True, help="Destination airport code")
    sp_awards.add_argument("--month", default="", help="Travel month")
    sp_awards.add_argument("--live", action="store_true", help="Include live availability from seats.aero")
    sp_awards.add_argument("--json", action="store_true", help="Output as JSON")
    sp_awards.set_defaults(func=cmd_awards)

    # visa
    sp_visa = subparsers.add_parser("visa", help="Check visa/entry requirements")
    sp_visa.add_argument("--destination", required=True, help="Country name")
    sp_visa.add_argument("--json", action="store_true", help="Output as JSON")
    sp_visa.set_defaults(func=cmd_visa)

    # save
    sp_save = subparsers.add_parser("save", help="Save trip idea to Karakeep")
    sp_save.add_argument("--title", required=True, help="Trip title")
    sp_save.add_argument("--url", required=True, help="URL to save")
    sp_save.add_argument("--notes", default="", help="Notes about the trip")
    sp_save.add_argument("--tags", default="travel,trip-idea", help="Comma-separated tags")
    sp_save.add_argument("--json", action="store_true", help="Output as JSON")
    sp_save.set_defaults(func=cmd_save)

    # itinerary
    sp_itin = subparsers.add_parser("itinerary", help="Generate day-by-day itinerary")
    sp_itin.add_argument("destination", help="Destination name")
    sp_itin.add_argument("--nights", type=int, default=7, help="Number of nights")
    sp_itin.add_argument("--ages", default="8,10,14", help="Comma-separated kid ages")
    sp_itin.add_argument("--json", action="store_true", help="Output as JSON")
    sp_itin.set_defaults(func=cmd_itinerary)

    # search (live award flights via seats.aero)
    sp_search = subparsers.add_parser("search", help="Search live award flight availability")
    sp_search.add_argument("--origin", default="IAD", help="Origin airport code")
    sp_search.add_argument("--dest", required=True, help="Destination airport code")
    sp_search.add_argument("--cabin", default="economy", choices=["economy", "premium", "business", "first"], help="Cabin class")
    sp_search.add_argument("--limit", type=int, default=50, help="Max results")
    sp_search.add_argument("--json", action="store_true", help="Output as JSON")
    sp_search.set_defaults(func=cmd_search)

    # login (store credentials)
    sp_login = subparsers.add_parser("login", help="Store login credentials for a service")
    sp_login.add_argument("service", help="Service name (e.g. hyatt, united)")
    sp_login.add_argument("--username", help="Username/email")
    sp_login.set_defaults(func=cmd_login)

    # trips (persistent trip storage)
    sp_trips = subparsers.add_parser("trips", help="Manage saved trips")
    sp_trips.add_argument("trips_action", choices=["list", "show", "save", "delete"])
    sp_trips.add_argument("name", nargs="?", default="")
    sp_trips.add_argument("--destination", default="")
    sp_trips.add_argument("--month", default="")
    sp_trips.add_argument("--notes", default="")
    sp_trips.add_argument("--json", action="store_true")
    sp_trips.set_defaults(func=cmd_trips)

    # hotels (award chart + search links)
    sp_hotels = subparsers.add_parser("hotels", help="Search hotels and award pricing")
    sp_hotels.add_argument("destination", help="Destination city")
    sp_hotels.add_argument("--checkin", required=True, help="Check-in date (YYYY-MM-DD)")
    sp_hotels.add_argument("--checkout", required=True, help="Check-out date (YYYY-MM-DD)")
    sp_hotels.add_argument("--nights", type=int, required=True, help="Number of nights")
    sp_hotels.add_argument("--country", default="", help="ISO country code for LiteAPI (e.g. MX, FR, JP)")
    sp_hotels.add_argument("--json", action="store_true")
    sp_hotels.set_defaults(func=cmd_hotels)

    # plan (full trip planning pipeline)
    sp_plan = subparsers.add_parser("plan", help="Full trip planning pipeline")
    sp_plan.add_argument("destination", help="Destination name")
    sp_plan.add_argument("--dest-code", required=True, help="Destination airport code")
    sp_plan.add_argument("--month", required=True, help="Travel month (e.g. June)")
    sp_plan.add_argument("--nights", type=int, default=7, help="Number of nights")
    sp_plan.set_defaults(func=cmd_plan)

    # alert (cheap award search)
    sp_alert = subparsers.add_parser("alert", help="Search for cheap award flights and send alerts")
    sp_alert.add_argument("--origin", default="IAD", help="Origin airport code")
    sp_alert.add_argument("--dest", required=True, help="Destination airport code")
    sp_alert.add_argument("--max-miles", type=int, required=True, help="Max miles threshold")
    sp_alert.add_argument("--cabin", default="economy", choices=["economy", "premium", "business", "first"])
    sp_alert.set_defaults(func=cmd_alert)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)
