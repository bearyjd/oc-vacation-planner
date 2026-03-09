"""LLM-powered vacation advisor using OpenAI SDK against LiteLLM proxy."""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generator

import requests

from vplan_cli.config import FAMILY, POINTS, SWEET_SPOTS
from vplan_cli.data_sources import (
    AWARD_CHARTS,
    HYATT_CATEGORIES,
    REGION_MAP,
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


# ---------------------------------------------------------------------------
# LLM client (lazy-loaded)
# ---------------------------------------------------------------------------

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    try:
        from openai import OpenAI
    except ImportError:
        print(
            "Error: openai package not installed. Run: pip install openai",
            file=sys.stderr,
        )
        sys.exit(1)

    api_url = os.environ.get("LITELLM_API_URL", "http://192.168.1.20:4000")
    api_key = os.environ.get("LITELLM_API_KEY", "")

    if not api_key:
        print(
            "Error: LITELLM_API_KEY not set. Add it to ~/.vplan/.env or set the env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    _client = OpenAI(base_url=api_url, api_key=api_key)
    return _client


DEFAULT_MODEL = "gpt-4o-mini"


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_system_prompt() -> str:
    """Build the system prompt with family info, points, routes."""

    kids_str = ", ".join(FAMILY["kids"])
    airports = ", ".join(FAMILY["home_airports"])

    chase = POINTS["chase_ur"]
    united = POINTS["united"]
    delta = POINTS["delta"]

    sweet_spots_lines = []
    for sp in SWEET_SPOTS:
        sweet_spots_lines.append(f"  - {sp['from']} -> {sp['to']} ({sp['ratio']}): {sp['note']}")

    # Compact nonstop route summary
    route_lines = []
    for airport in FAMILY["home_airports"]:
        if airport in NONSTOP_ROUTES:
            codes = list(NONSTOP_ROUTES[airport].keys())
            route_lines.append(f"  {airport}: {', '.join(codes)}")

    # Hyatt chart summary (compact)
    hyatt_lines = []
    for cat, info in HYATT_CATEGORIES.items():
        hyatt_lines.append(f"  Cat {cat}: {info['points']:,}/night")

    return f"""You are a vacation planning advisor for the Beary family. Give specific, actionable recommendations.

FAMILY:
- {FAMILY['adults']} adults + 3 kids: {kids_str}
- Home airports: {airports}
- Visited all 7 continents

POINTS BALANCES:
- Chase Ultimate Rewards: {chase['balance']:,} points (1.5x via portal = ${chase['balance'] * 1.5 / 100:,.0f} travel value, expires {chase['expires']})
- United MileagePlus: {united['balance']:,} miles (Premier 1K status, {united['plus_points']} PlusPoints, ${united['travel_bank_usd']} travel bank)
- Delta SkyMiles: {delta['balance']:,} miles

TRANSFER SWEET SPOTS:
{chr(10).join(sweet_spots_lines)}

PREMIER 1K BENEFITS: Complimentary upgrades on domestic/short-haul, PlusPoints for confirmed premium cabin upgrades, Economy Plus at booking, 2 free checked bags, Star Alliance Gold.

NONSTOP ROUTES:
{chr(10).join(route_lines)}

HYATT AWARD CHART (Chase UR transfers 1:1 to Hyatt):
{chr(10).join(hyatt_lines)}

RULES:
1. Always consider the family has 3 kids when recommending activities and logistics.
2. Prioritize Chase UR usage — they expire Oct 2027. Suggest transfer partners for best value.
3. For flights, prefer nonstop routes from IAD/DCA when available.
4. Show specific miles/points costs for family of 5 (round trip).
5. Include booking links where helpful.
6. When mentioning award flights, note that seats.aero data is cached (last ~60 days) and may not reflect real-time availability.
7. Be concise but thorough. Use sections with headers.
8. If the user asks about a destination without specifying dates, suggest the best month to visit.
9. Include visa/entry requirements for international destinations.
10. Suggest both points and cash options so the family can compare value.
11. For Caribbean/coastal destinations, mention cruise options. Use these search links:
    - Royal Caribbean: https://www.royalcaribbean.com/cruises?sailing-date=MONTH&departure-port=BAL
    - Norwegian: https://www.ncl.com/cruises?embarkPort=BAL
    - Disney Cruise Line: https://disneycruise.disney.go.com/cruises-destinations/
    - Chase UR portal can book cruises at 1.5x value."""


# ---------------------------------------------------------------------------
# Query parsing
# ---------------------------------------------------------------------------

# Airport codes that appear in our routes
_ALL_AIRPORT_CODES = set()
for _ap, _routes in NONSTOP_ROUTES.items():
    _ALL_AIRPORT_CODES.add(_ap)
    _ALL_AIRPORT_CODES.update(_routes.keys())

# Common destination -> airport code mapping
DESTINATION_CODES = {
    "cancun": "CUN", "cancún": "CUN", "mexico city": "MEX",
    "guadalajara": "GDL", "cabo": "SJD", "los cabos": "SJD", "san jose del cabo": "SJD",
    "montego bay": "MBJ", "jamaica": "MBJ", "san juan": "SJU", "puerto rico": "SJU",
    "punta cana": "PUJ", "nassau": "NAS", "bahamas": "NAS",
    "aruba": "AUA", "turks and caicos": "PLS", "st thomas": "STT", "virgin islands": "STT",
    "st maarten": "SXM", "sint maarten": "SXM", "barbados": "BGI",
    "st lucia": "UVF", "saint lucia": "UVF",
    "london": "LHR", "paris": "CDG", "frankfurt": "FRA", "amsterdam": "AMS",
    "rome": "FCO", "barcelona": "BCN", "lisbon": "LIS", "athens": "ATH",
    "istanbul": "IST", "dublin": "DUB", "zurich": "ZRH", "munich": "MUC",
    "madrid": "MAD", "copenhagen": "CPH", "stockholm": "ARN", "edinburgh": "EDI",
    "nice": "NCE", "venice": "VCE",
    "tokyo": "NRT", "seoul": "ICN", "delhi": "DEL", "mumbai": "BOM",
    "doha": "DOH", "dubai": "DXB", "tel aviv": "TLV",
    "bogota": "BOG", "bogotá": "BOG", "sao paulo": "GRU", "são paulo": "GRU",
    "san jose": "SJO", "costa rica": "SJO", "panama city": "PTY", "panama": "PTY",
    "belize": "BZE", "addis ababa": "ADD", "accra": "ACC", "dakar": "DSS",
    "liberia": "LIR",
    # DCA domestic
    "los angeles": "LAX", "san francisco": "SFO", "chicago": "ORD",
    "dallas": "DFW", "miami": "MIA", "atlanta": "ATL", "boston": "BOS",
    "seattle": "SEA", "denver": "DEN", "minneapolis": "MSP", "phoenix": "PHX",
    "orlando": "MCO", "tampa": "TPA", "fort lauderdale": "FLL",
}

MONTH_NAMES = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]
MONTH_ABBREVS = {m[:3]: m for m in MONTH_NAMES}


def parse_query(query: str) -> dict:
    """Extract structured info from a natural language query.

    Returns dict with optional keys: destinations, origin, month, nights, cabin, budget, theme.
    """
    q = query.lower().strip()
    parsed = {}

    # Extract origin airport
    origin_match = re.search(r'\bfrom\s+([A-Z]{3})\b', query, re.IGNORECASE)
    if origin_match:
        code = origin_match.group(1).upper()
        if code in _ALL_AIRPORT_CODES:
            parsed["origin"] = code

    if "origin" not in parsed:
        parsed["origin"] = "IAD"

    # Extract month
    for month in MONTH_NAMES:
        if month in q:
            parsed["month"] = month.capitalize()
            break
    if "month" not in parsed:
        for abbr, full in MONTH_ABBREVS.items():
            if re.search(rf'\b{abbr}\b', q):
                parsed["month"] = full.capitalize()
                break

    # Extract nights
    nights_match = re.search(r'(\d+)\s*(?:night|day|noche)', q)
    if nights_match:
        n = int(nights_match.group(1))
        parsed["nights"] = n if "night" in q or "noche" in q else max(1, n - 1)

    # Extract cabin
    for cabin in ["first", "business", "premium"]:
        if cabin in q:
            parsed["cabin"] = cabin
            break

    # Extract theme
    themes = {
        "beach": ["beach", "ocean", "snorkel", "surf", "sand", "tropical", "island", "caribbean"],
        "city": ["city", "urban", "museum", "shopping", "culture"],
        "adventure": ["adventure", "hiking", "trek", "safari", "nature", "mountain"],
        "ski": ["ski", "snow", "winter sport"],
        "cruise": ["cruise", "sailing"],
        "resort": ["resort", "all-inclusive", "all inclusive"],
    }
    for theme, keywords in themes.items():
        if any(kw in q for kw in keywords):
            parsed["theme"] = theme
            break

    # Extract specific destinations mentioned
    destinations = []
    for dest_name, code in DESTINATION_CODES.items():
        if dest_name in q:
            destinations.append({"name": dest_name.title(), "code": code})

    # Also check for raw airport codes in the query
    code_matches = re.findall(r'\b([A-Z]{3})\b', query)
    for code in code_matches:
        if code in _ALL_AIRPORT_CODES and code != parsed.get("origin"):
            if not any(d["code"] == code for d in destinations):
                destinations.append({"name": code, "code": code})

    if destinations:
        parsed["destinations"] = destinations

    # Extract number of people
    people_match = re.search(r'(\d+)\s*(?:people|person|pax|travelers)', q)
    if people_match:
        parsed["travelers"] = int(people_match.group(1))

    return parsed


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------

def _gather_destination_context(dest_name: str, dest_code: str, origin: str,
                                 month: str | None, live: bool = False) -> dict:
    """Gather all available data for a single destination."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    ctx: dict[str, object] = {"destination": dest_name, "code": dest_code}

    # Wikivoyage
    wiki = scrape_wikivoyage(dest_name, s)
    if wiki:
        overview = wiki.get("Overview", wiki.get("Understand", ""))
        ctx["overview"] = overview[:600] if overview else ""
        tips = family_suitability(dest_name, wiki)
        if tips:
            ctx["family_tips"] = tips
    else:
        ctx["overview"] = ""

    # Weather
    if month:
        weather = fetch_weather(dest_name, month, s)
        ctx["weather"] = weather

    # Visa
    visa = lookup_visa(dest_name)
    if visa.get("visa_required") is not None:
        ctx["visa"] = {
            "required": visa.get("visa_required"),
            "max_days": visa.get("max_stay_days"),
            "docs": visa.get("documents", ""),
            "notes": visa.get("notes", ""),
        }

    # Award availability (static)
    awards = lookup_awards(origin, dest_code, month or "")
    programs = awards.get("programs", [])
    # Compact: just the essentials
    compact_programs = []
    for p in programs:
        entry = {"program": p["program"], "balance": p["balance"]}
        for k in ["saver_rt", "everyday_rt", "low_rt", "mid_rt", "family_of_5_saver", "family_of_5_low",
                   "can_afford_saver", "can_afford_low", "can_afford"]:
            if k in p:
                entry[k] = p[k]
        compact_programs.append(entry)
    ctx["award_programs"] = compact_programs

    # Nonstop info
    for airport in FAMILY["home_airports"]:
        if airport in NONSTOP_ROUTES and dest_code in NONSTOP_ROUTES[airport]:
            route_info = NONSTOP_ROUTES[airport][dest_code]
            ctx["nonstop"] = {
                "from": airport,
                "airlines": route_info["airlines"],
                "frequency": route_info["frequency"],
            }
            break

    # Live seats.aero search
    if live and dest_code:
        try:
            from vplan_cli.scraper_seats import SeatsAeroScraper
            with SeatsAeroScraper(headless=True) as scraper:
                flights = scraper.search_flights(origin, dest_code, "economy", 50)
            if flights:
                # Dedup and take top 5 by lowest miles
                seen = set()
                unique = []
                for f in sorted(flights, key=lambda x: x["mileage_cost"] if isinstance(x["mileage_cost"], (int, float)) else 999999):
                    key = (f["carriers"], f["mileage_cost"], f["stops"])
                    if key not in seen:
                        seen.add(key)
                        unique.append({
                            "carriers": f["carriers"],
                            "miles": f["mileage_cost"],
                            "tax": f["taxes_usd"],
                            "stops": f["stops"],
                            "duration": f["duration"],
                            "source": f["source"],
                            "seats": f["remaining_seats"],
                            "date": f.get("date", ""),
                        })
                    if len(unique) >= 5:
                        break
                ctx["live_flights"] = unique
        except Exception as e:
            ctx["live_flights_error"] = str(e)

    return ctx


def gather_context(query: str, parsed: dict, live: bool = False) -> str:
    """Gather data for all destinations in the parsed query and format as context string."""
    origin = parsed.get("origin", "IAD")
    month = parsed.get("month")
    destinations = parsed.get("destinations", [])

    # If no specific destinations, let the LLM recommend based on theme/month
    if not destinations:
        # Still provide general context
        context_parts = []
        if month:
            context_parts.append(f"Travel month: {month}")
        context_parts.append(f"Origin: {origin}")
        if parsed.get("theme"):
            context_parts.append(f"Theme: {parsed['theme']}")

        # Points summary for recommendation
        chase = POINTS["chase_ur"]
        united = POINTS["united"]
        delta = POINTS["delta"]
        context_parts.append(
            f"Available points: {chase['balance']:,} Chase UR, "
            f"{united['balance']:,} United miles, {delta['balance']:,} Delta SkyMiles"
        )

        return "\n".join(context_parts)

    # Gather data for each destination (parallel if multiple)
    dest_contexts = []

    if len(destinations) > 1 and not live:
        # Parallel fetch for multiple destinations (static data only)
        with ThreadPoolExecutor(max_workers=min(3, len(destinations))) as pool:
            futures = {
                pool.submit(
                    _gather_destination_context,
                    d["name"], d["code"], origin, month, False,
                ): d
                for d in destinations
            }
            for fut in as_completed(futures):
                try:
                    dest_contexts.append(fut.result())
                except Exception as e:
                    d = futures[fut]
                    dest_contexts.append({"destination": d["name"], "error": str(e)})
    else:
        for d in destinations:
            ctx = _gather_destination_context(d["name"], d["code"], origin, month, live)
            dest_contexts.append(ctx)

    # Format into compact text
    parts = []
    for ctx in dest_contexts:
        lines = [f"=== {ctx['destination']} ({ctx.get('code', '')}) ==="]

        if ctx.get("overview"):
            lines.append(f"Overview: {ctx['overview'][:400]}")

        if ctx.get("weather"):
            lines.append(f"Weather: {ctx['weather']}")

        if ctx.get("visa"):
            v = ctx["visa"]
            visa_str = "No visa" if v["required"] is False else "Visa required"
            if v.get("max_days"):
                visa_str += f" (max {v['max_days']} days)"
            if v.get("notes"):
                visa_str += f" — {v['notes']}"
            lines.append(f"Entry: {visa_str}")

        if ctx.get("nonstop"):
            ns = ctx["nonstop"]
            lines.append(f"Nonstop: {ns['from']} via {', '.join(ns['airlines'])} ({ns['frequency']})")

        if ctx.get("award_programs"):
            lines.append("Awards:")
            for p in ctx["award_programs"]:
                prog_line = f"  {p['program']}: {p['balance']}"
                for k in ["saver_rt", "family_of_5_saver", "can_afford_saver",
                           "low_rt", "family_of_5_low", "can_afford_low", "can_afford"]:
                    if k in p:
                        prog_line += f" | {k}: {p[k]}"
                lines.append(prog_line)

        if ctx.get("family_tips"):
            lines.append("Family: " + "; ".join(ctx["family_tips"]))

        if ctx.get("live_flights"):
            lines.append(f"Live flights ({len(ctx['live_flights'])} best):")
            for f in ctx["live_flights"]:
                stops = "nonstop" if f["stops"] == 0 else f"{f['stops']}stop"
                miles = f"{f['miles']:,}" if isinstance(f["miles"], int) else str(f["miles"])
                lines.append(
                    f"  {f['carriers']} | {miles}mi | ${f['tax']:.0f}tax | "
                    f"{stops} | {f['duration']} | {f.get('date', '')} | {f['seats']}seats | {f['source']}"
                )

        parts.append("\n".join(lines))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Streaming ask
# ---------------------------------------------------------------------------

def ask(query: str, live: bool = False, model: str = DEFAULT_MODEL,
        verbose: bool = False) -> Generator[str, None, None]:
    """Process a natural language travel query, gather context, stream LLM response.

    Yields chunks of the response text as they arrive.
    """
    if verbose:
        print(f"[advisor] Parsing query...", file=sys.stderr)

    parsed = parse_query(query)

    if verbose:
        print(f"[advisor] Parsed: {json.dumps(parsed, default=str)}", file=sys.stderr)
        print(f"[advisor] Gathering context (live={live})...", file=sys.stderr)

    context = gather_context(query, parsed, live=live)

    if verbose:
        ctx_preview = context[:300].replace("\n", " ")
        print(f"[advisor] Context ({len(context)} chars): {ctx_preview}...", file=sys.stderr)

    system_prompt = _build_system_prompt()

    # Build user message with context
    user_parts = [query]
    if context:
        user_parts.append(f"\n\n--- DATA (use this to support your recommendations) ---\n{context}")

    user_message = "\n".join(user_parts)

    # Check total size — warn if large
    total_chars = len(system_prompt) + len(user_message)
    if verbose:
        est_tokens = total_chars // 4
        print(f"[advisor] Estimated tokens: ~{est_tokens} (system={len(system_prompt)//4}, user={len(user_message)//4})", file=sys.stderr)
        print(f"[advisor] Calling {model}...", file=sys.stderr)

    client = _get_client()

    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            stream=True,
            temperature=0.7,
            max_tokens=4096,
        )

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    except Exception as e:
        yield f"\n\nError calling LLM: {e}"


# ---------------------------------------------------------------------------
# Interactive chat
# ---------------------------------------------------------------------------

def chat(model: str = DEFAULT_MODEL, verbose: bool = False):
    """Start an interactive multi-turn chat session."""
    system_prompt = _build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]

    client = _get_client()

    print("vplan chat — Type your travel questions. Type 'quit' or 'exit' to stop.")
    print("Use '/live <query>' to include live seats.aero data (slower).")
    print("Use '/context <destination>' to pre-fetch destination data.")
    print("-" * 60)

    while True:
        try:
            user_input = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break

        # Handle special commands
        live = False
        if user_input.startswith("/live "):
            live = True
            user_input = user_input[6:].strip()

        if user_input.startswith("/context "):
            dest = user_input[9:].strip()
            parsed = parse_query(f"tell me about {dest}")
            if not parsed.get("destinations"):
                # Try to use it as a raw code or name
                code = DESTINATION_CODES.get(dest.lower(), dest.upper())
                parsed["destinations"] = [{"name": dest.title(), "code": code}]

            if verbose:
                print(f"[chat] Fetching context for {dest}...", file=sys.stderr)

            context = gather_context(f"tell me about {dest}", parsed, live=False)
            # Inject context as a system-ish message
            messages.append({
                "role": "user",
                "content": f"/context {dest}\n\n--- DATA ---\n{context}",
            })
            messages.append({
                "role": "assistant",
                "content": f"Got it — I've loaded data for {dest}. What would you like to know?",
            })
            print(f"\nassistant> Got it — I've loaded data for {dest}. What would you like to know?")
            continue

        # For regular messages, optionally gather context
        parsed = parse_query(user_input)
        context = ""

        if parsed.get("destinations") or live:
            if verbose:
                print(f"[chat] Auto-fetching context...", file=sys.stderr)
            context = gather_context(user_input, parsed, live=live)

        user_msg = user_input
        if context:
            user_msg += f"\n\n--- DATA ---\n{context}"

        messages.append({"role": "user", "content": user_msg})

        # Stream response
        print("\nassistant> ", end="", flush=True)
        full_response = []

        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                temperature=0.7,
                max_tokens=4096,
            )

            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    print(text, end="", flush=True)
                    full_response.append(text)

        except Exception as e:
            error_msg = f"\n\nError: {e}"
            print(error_msg, end="")
            full_response.append(error_msg)

        print()  # newline after response

        messages.append({"role": "assistant", "content": "".join(full_response)})

        # Trim history if getting too long (keep system + last 10 exchanges)
        if len(messages) > 22:
            messages = [messages[0]] + messages[-20:]
