import sys
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from vplan_cli.config import LITEAPI_KEY, POINTS


MONTH_NUMBERS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

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


def resolve_wikivoyage_title(destination: str, s: requests.Session) -> str | None:
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


def scrape_wikivoyage(destination: str, s: requests.Session) -> dict:
    title = resolve_wikivoyage_title(destination, s) or destination
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


def geocode(destination: str, s: requests.Session) -> tuple[float, float] | None:
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


def fetch_weather(destination: str, month: str, s: requests.Session) -> str:
    coords = geocode(destination, s)
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
            f"Avg High: {avg_high_f:.0f}\u00b0F ({avg_high_c:.1f}\u00b0C) | "
            f"Avg Low: {avg_low_f:.0f}\u00b0F ({avg_low_c:.1f}\u00b0C) | "
            f"Rain: {total_rain_in:.1f}in ({total_rain_mm:.0f}mm) over {rainy_days} days"
        )
    except requests.RequestException:
        return f"Could not fetch weather data for {destination}."


def family_suitability(destination: str, sections: dict) -> list:
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


def calculate_redemption(hotel_rate_usd: float, flights_usd: float) -> list:
    total_cash = hotel_rate_usd + flights_usd
    options = []

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

    if hotel_rate_usd > 0:
        hyatt_points_per_night = int(hotel_rate_usd / 0.02)
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

    united = POINTS["united"]
    if flights_usd > 0:
        united_miles_est = int(flights_usd / 0.013)
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

    delta = POINTS["delta"]
    if flights_usd > 0:
        delta_miles_est = int(flights_usd / 0.012)
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


def lookup_awards(origin: str, dest: str, month: str) -> dict:
    region = REGION_MAP.get(dest.upper(), "americas")

    result = {
        "route": f"{origin} -> {dest}",
        "region": region,
        "month": month,
        "programs": [],
        "search_links": [],
    }

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


def lookup_visa(country: str) -> dict:
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


def generate_itinerary(destination: str, nights: int, ages: list[int]) -> dict:
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


def search_hotels_liteapi(city: str, country_code: str, checkin: str, checkout: str,
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
            print(f"LiteAPI returned {r.status_code}: {r.text[:200]}", file=sys.stderr)
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
        print(f"LiteAPI request failed: {e}", file=sys.stderr)
        return []
