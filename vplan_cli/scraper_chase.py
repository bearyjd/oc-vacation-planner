"""Chase Travel portal scraper — manual-assist approach.

Opens a visible Chromium browser so the user can log into their Chase account
and navigate to Chase Travel.  Once authenticated, Playwright intercepts the
cxLoyalty search API responses and extracts flight/hotel pricing in UR points.

Usage (standalone):
    python -m vplan_cli.scraper_chase --search flights --origin IAD --dest CUN

Usage (CLI integration):
    vplan chase flights --origin IAD --dest CUN
    vplan chase hotels --city Cancun --checkin 2026-06-01 --checkout 2026-06-08
    vplan chase login          # Just open browser and authenticate
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Response,
    sync_playwright,
)
from playwright_stealth import Stealth

# Chase portal URLs
CHASE_LOGIN_URL = "https://secure.chase.com/web/auth/dashboard"
CHASE_TRAVEL_URL = "https://www.chasetravel.com"
CHASE_TRAVEL_FLIGHTS_URL = "https://www.chasetravel.com/flights"
CHASE_TRAVEL_HOTELS_URL = "https://www.chasetravel.com/hotels"

# API patterns to intercept — cxLoyalty / Chase Travel backend
API_PATTERNS = [
    re.compile(r"/api/.*flight", re.IGNORECASE),
    re.compile(r"/api/.*hotel", re.IGNORECASE),
    re.compile(r"/api/.*search", re.IGNORECASE),
    re.compile(r"/api/.*offer", re.IGNORECASE),
    re.compile(r"/api/.*availability", re.IGNORECASE),
    re.compile(r"/api/.*pricing", re.IGNORECASE),
    re.compile(r"cxloyalty", re.IGNORECASE),
    re.compile(r"travel.*api", re.IGNORECASE),
]


def _is_api_response(url: str) -> bool:
    """Check if URL looks like a Chase Travel API endpoint."""
    return any(p.search(url) for p in API_PATTERNS)


def _is_json_response(response: Response) -> bool:
    """Check if response has JSON content type."""
    ct = response.headers.get("content-type", "")
    return "application/json" in ct or "text/json" in ct


class ChaseTravel:
    """Manual-assist Chase Travel scraper.

    Opens a VISIBLE browser for the user to log in.  After authentication,
    intercepts API calls as the user (or automated navigation) searches
    for flights/hotels.
    """

    def __init__(self, profile_dir: str | None = None) -> None:
        self._profile_dir = profile_dir
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._stealth = Stealth()
        self._captured: list[dict[str, Any]] = []
        self._api_responses: list[dict[str, Any]] = []
        self._authenticated = False

    @property
    def _p(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started — call open_browser() first")
        return self._page

    def open_browser(self) -> None:
        """Launch a visible Chromium browser with stealth."""
        if self._browser and self._browser.is_connected():
            return

        self._pw = sync_playwright().start()

        _browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--window-size=1400,900",
        ]
        _ua = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

        # Use persistent context if profile_dir provided (keeps cookies/session)
        if self._profile_dir:
            self._context = self._pw.chromium.launch_persistent_context(
                self._profile_dir,
                headless=False,
                args=_browser_args,
                viewport={"width": 1400, "height": 900},
                user_agent=_ua,
            )
            self._browser = None  # persistent context doesn't use separate browser
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        else:
            self._browser = self._pw.chromium.launch(
                headless=False,
                args=_browser_args,
            )
            self._context = self._browser.new_context(
                viewport={"width": 1400, "height": 900},
                user_agent=_ua,
            )
            self._page = self._context.new_page()

        self._stealth.apply_stealth_sync(self._context)
        self._p.set_default_timeout(120_000)  # 2 min timeout for manual login

        # Start intercepting API responses
        self._p.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        """Capture JSON API responses from Chase Travel backend."""
        url = response.url
        status = response.status

        # Skip non-success, non-JSON, or irrelevant responses
        if status < 200 or status >= 400:
            return

        if not (_is_api_response(url) or _is_json_response(response)):
            return

        try:
            body = response.json()
        except Exception:
            return

        entry = {
            "url": url,
            "status": status,
            "timestamp": time.time(),
            "data": body,
        }

        self._api_responses.append(entry)

        # Try to extract flight/hotel results from the response
        extracted = self._extract_results(url, body)
        if extracted:
            self._captured.extend(extracted)
            print(f"  [chase] Captured {len(extracted)} result(s) from {url[:80]}...", file=sys.stderr)

    def _extract_results(self, url: str, body: Any) -> list[dict[str, Any]]:
        """Extract normalized flight/hotel results from API response.

        Chase Travel's exact API shape is discovered at runtime, so this
        method tries multiple common patterns.
        """
        results: list[dict[str, Any]] = []

        if not isinstance(body, (dict, list)):
            return results

        # If body is a list of results directly
        if isinstance(body, list):
            for item in body:
                if isinstance(item, dict):
                    normalized = self._normalize_item(item, url)
                    if normalized:
                        results.append(normalized)
            return results

        assert isinstance(body, dict)

        # Common API response wrappers
        for key in ["results", "data", "offers", "flights", "hotels",
                     "searchResults", "flightResults", "hotelResults",
                     "itineraries", "listings", "items", "response"]:
            val = body.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        normalized = self._normalize_item(item, url)
                        if normalized:
                            results.append(normalized)
                if results:
                    return results

        # Nested data.results pattern
        data_val = body.get("data")
        if isinstance(data_val, dict):
            for key in ["results", "offers", "flights", "hotels", "items"]:
                inner = data_val.get(key)
                if isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict):
                            normalized = self._normalize_item(item, url)
                            if normalized:
                                results.append(normalized)
                    if results:
                        return results

        # Single result in body (check for known fields)
        normalized = self._normalize_item(body, url)
        if normalized:
            results.append(normalized)

        return results

    def _normalize_item(self, item: dict[str, Any], url: str) -> dict[str, Any] | None:
        """Normalize a single flight or hotel result."""
        # Detect if this is a flight
        flight_signals = ["airline", "carrier", "flightNumber", "flight_number",
                          "departureTime", "departure", "arrivalTime", "arrival",
                          "departsAt", "arrivesAt", "origin", "destination",
                          "segments", "legs", "slices"]

        hotel_signals = ["hotel", "hotelName", "hotel_name", "property",
                         "propertyName", "checkIn", "check_in", "roomRate",
                         "room_rate", "nightly", "nightlyRate"]

        is_flight = any(k in item for k in flight_signals)
        is_hotel = any(k in item for k in hotel_signals)

        if is_flight:
            return self._normalize_flight(item, url)
        elif is_hotel:
            return self._normalize_hotel(item, url)
        return None

    def _normalize_flight(self, raw: dict[str, Any], url: str) -> dict[str, Any]:
        """Normalize a Chase Travel flight result."""
        # Points/price extraction — try multiple field names
        points = (
            raw.get("points") or raw.get("rewardPoints") or
            raw.get("pointsRequired") or raw.get("miles") or
            raw.get("loyaltyPoints") or 0
        )
        cash_price = (
            raw.get("totalPrice") or raw.get("price") or
            raw.get("cashPrice") or raw.get("total") or
            raw.get("fareTotal") or 0
        )
        points_price = (
            raw.get("pointsPrice") or raw.get("rewardCost") or
            raw.get("pointsCost") or points or 0
        )

        # If price is in cents, convert
        if isinstance(cash_price, (int, float)) and cash_price > 10000:
            cash_price = cash_price / 100

        origin = (
            raw.get("origin") or raw.get("departureAirport") or
            raw.get("originCode") or raw.get("from") or ""
        )
        destination = (
            raw.get("destination") or raw.get("arrivalAirport") or
            raw.get("destinationCode") or raw.get("to") or ""
        )
        carrier = (
            raw.get("carrier") or raw.get("airline") or
            raw.get("carriers") or raw.get("marketingCarrier") or ""
        )
        departure = (
            raw.get("departureTime") or raw.get("departsAt") or
            raw.get("departure") or ""
        )
        arrival = (
            raw.get("arrivalTime") or raw.get("arrivesAt") or
            raw.get("arrival") or ""
        )

        # Handle nested origin/destination objects
        if isinstance(origin, dict):
            origin = origin.get("code", origin.get("iata", str(origin)))
        if isinstance(destination, dict):
            destination = destination.get("code", destination.get("iata", str(destination)))
        if isinstance(carrier, dict):
            carrier = carrier.get("code", carrier.get("name", str(carrier)))

        stops = raw.get("stops", raw.get("numStops", raw.get("connections", 0)))
        if isinstance(stops, list):
            stops = len(stops)

        duration = raw.get("duration", raw.get("totalDuration", raw.get("flightDuration", "")))
        if isinstance(duration, (int, float)) and duration > 0:
            hours, minutes = divmod(int(duration), 60)
            duration = f"{hours}h {minutes}m"

        return {
            "type": "flight",
            "source": "chase_travel",
            "origin": str(origin),
            "destination": str(destination),
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

    def _normalize_hotel(self, raw: dict[str, Any], url: str) -> dict[str, Any]:
        """Normalize a Chase Travel hotel result."""
        name = (
            raw.get("hotelName") or raw.get("hotel_name") or
            raw.get("propertyName") or raw.get("name") or
            raw.get("hotel", {}).get("name", "") if isinstance(raw.get("hotel"), dict)
            else raw.get("hotel", "")
        )
        nightly = (
            raw.get("nightlyRate") or raw.get("nightly") or
            raw.get("averageNightlyRate") or raw.get("rate") or 0
        )
        total = (
            raw.get("totalPrice") or raw.get("total") or
            raw.get("price") or 0
        )
        points = (
            raw.get("points") or raw.get("rewardPoints") or
            raw.get("pointsRequired") or raw.get("pointsCost") or 0
        )
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

    def navigate_to_login(self) -> None:
        """Navigate to Chase login page."""
        print("Opening Chase login...", file=sys.stderr)
        print("Please log in with your Chase credentials.", file=sys.stderr)
        print("Complete any MFA/verification steps in the browser.", file=sys.stderr)
        self._p.goto(CHASE_LOGIN_URL, wait_until="domcontentloaded")

    def wait_for_auth(self, timeout_seconds: int = 300) -> bool:
        """Wait for user to complete Chase login (up to timeout).

        Detects authentication by checking if we can reach Chase Travel
        without being redirected to login.
        """
        print(f"\nWaiting up to {timeout_seconds // 60} minutes for login...", file=sys.stderr)
        print("(The script will detect when you're logged in)\n", file=sys.stderr)

        start = time.time()
        check_interval = 5  # seconds between checks

        while time.time() - start < timeout_seconds:
            current_url = self._p.url

            # Check if we're past the login page
            if "chasetravel.com" in current_url and "auth" not in current_url:
                self._authenticated = True
                print("Authenticated! Chase Travel is loaded.", file=sys.stderr)
                return True

            # Check if the dashboard loaded (meaning login succeeded)
            if "dashboard" in current_url and "auth" not in current_url:
                self._authenticated = True
                print("Login detected — navigating to Chase Travel...", file=sys.stderr)
                self._p.goto(CHASE_TRAVEL_URL, wait_until="domcontentloaded")
                time.sleep(3)
                return True

            # Check for Chase-specific auth success indicators
            try:
                # Look for account greeting or nav elements that only appear when logged in
                greeting = self._p.query_selector('[class*="greeting"], [class*="welcome"], [data-testid*="account"]')
                if greeting:
                    self._authenticated = True
                    print("Login detected — navigating to Chase Travel...", file=sys.stderr)
                    self._p.goto(CHASE_TRAVEL_URL, wait_until="domcontentloaded")
                    time.sleep(3)
                    return True
            except Exception:
                pass

            time.sleep(check_interval)

        print("Login timeout — did not detect authentication.", file=sys.stderr)
        return False

    def navigate_to_flights(self, origin: str = "", destination: str = "", date: str = "") -> None:
        """Navigate to Chase Travel flight search page.

        If origin/destination provided, tries to pre-fill the search form.
        """
        if not self._authenticated:
            print("Warning: Not authenticated — results may be limited.", file=sys.stderr)

        url = CHASE_TRAVEL_FLIGHTS_URL
        self._p.goto(url, wait_until="domcontentloaded")
        time.sleep(3)

        if origin or destination:
            print(f"Search page loaded. Please search for: {origin} -> {destination}", file=sys.stderr)
            if date:
                print(f"  Date: {date}", file=sys.stderr)
            print("The tool will capture results as they load.\n", file=sys.stderr)

    def navigate_to_hotels(self, city: str = "", checkin: str = "", checkout: str = "") -> None:
        """Navigate to Chase Travel hotel search page."""
        if not self._authenticated:
            print("Warning: Not authenticated — results may be limited.", file=sys.stderr)

        url = CHASE_TRAVEL_HOTELS_URL
        self._p.goto(url, wait_until="domcontentloaded")
        time.sleep(3)

        if city:
            print(f"Search page loaded. Please search for hotels in: {city}", file=sys.stderr)
            if checkin and checkout:
                print(f"  Dates: {checkin} to {checkout}", file=sys.stderr)
            print("The tool will capture results as they load.\n", file=sys.stderr)

    def wait_for_results(self, timeout_seconds: int = 120, min_results: int = 1) -> list[dict[str, Any]]:
        """Wait for search results to be captured via API interception.

        Returns captured results once min_results are found or timeout.
        """
        print(f"Waiting for search results (up to {timeout_seconds}s)...", file=sys.stderr)
        start = time.time()
        last_count = 0

        while time.time() - start < timeout_seconds:
            current_count = len(self._captured)
            if current_count > last_count:
                print(f"  {current_count} result(s) captured so far...", file=sys.stderr)
                last_count = current_count

            if current_count >= min_results:
                # Wait a bit more for additional results to stream in
                time.sleep(3)
                if len(self._captured) == current_count:
                    # No new results — we're done
                    break

            time.sleep(2)

        results = list(self._captured)
        print(f"\nTotal captured: {len(results)} result(s)", file=sys.stderr)
        return results

    def get_captured(self) -> list[dict[str, Any]]:
        """Get all captured results so far."""
        return list(self._captured)

    def get_raw_api_responses(self) -> list[dict[str, Any]]:
        """Get all raw API responses (for debugging/reverse-engineering)."""
        return list(self._api_responses)

    def interactive_session(self, search_type: str = "flights",
                            origin: str = "", destination: str = "",
                            city: str = "", checkin: str = "",
                            checkout: str = "") -> list[dict[str, Any]]:
        """Full interactive session: login -> search -> capture.

        This is the main entry point for CLI usage.
        """
        self.open_browser()
        self.navigate_to_login()

        if not self.wait_for_auth():
            print("Authentication failed or timed out.", file=sys.stderr)
            return []

        if search_type == "flights":
            self.navigate_to_flights(origin, destination)
        elif search_type == "hotels":
            self.navigate_to_hotels(city, checkin, checkout)
        else:
            print(f"Navigate to what you want to search. Capturing all API responses...", file=sys.stderr)

        results = self.wait_for_results(timeout_seconds=180)

        # Also dump raw API responses for debugging
        raw = self.get_raw_api_responses()
        if raw:
            print(f"\n  [debug] {len(raw)} total API responses intercepted", file=sys.stderr)
            for r in raw[:5]:
                url_short = r["url"][:100]
                data_type = type(r["data"]).__name__
                data_len = len(json.dumps(r["data"])) if isinstance(r["data"], (dict, list)) else 0
                print(f"    {r['status']} {url_short}... ({data_type}, {data_len} bytes)", file=sys.stderr)

        return results

    def close(self) -> None:
        """Close browser."""
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._page = None
        self._pw = None

    def __enter__(self) -> ChaseTravel:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def search_chase_flights(origin: str = "IAD", destination: str = "",
                         profile_dir: str | None = None) -> list[dict[str, Any]]:
    """Convenience function: open Chase Travel, let user log in, capture flight results."""
    with ChaseTravel(profile_dir=profile_dir) as ct:
        return ct.interactive_session("flights", origin=origin, destination=destination)


def search_chase_hotels(city: str = "", checkin: str = "", checkout: str = "",
                        profile_dir: str | None = None) -> list[dict[str, Any]]:
    """Convenience function: open Chase Travel, let user log in, capture hotel results."""
    with ChaseTravel(profile_dir=profile_dir) as ct:
        return ct.interactive_session("hotels", city=city, checkin=checkin, checkout=checkout)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Chase Travel scraper (manual-assist)")
    parser.add_argument("--search", choices=["flights", "hotels", "browse"], default="browse",
                        help="What to search for")
    parser.add_argument("--origin", default="IAD", help="Flight origin airport")
    parser.add_argument("--dest", default="", help="Flight destination airport")
    parser.add_argument("--city", default="", help="Hotel city")
    parser.add_argument("--checkin", default="", help="Hotel check-in date")
    parser.add_argument("--checkout", default="", help="Hotel check-out date")
    parser.add_argument("--profile", default="", help="Browser profile directory (persists login)")
    parser.add_argument("--dump-raw", action="store_true", help="Dump all raw API responses")

    args = parser.parse_args()

    profile = args.profile or None

    with ChaseTravel(profile_dir=profile) as ct:
        results = ct.interactive_session(
            search_type=args.search,
            origin=args.origin,
            destination=args.dest,
            city=args.city,
            checkin=args.checkin,
            checkout=args.checkout,
        )

        if results:
            print(json.dumps(results, indent=2))
        else:
            print("No structured results captured.", file=sys.stderr)
            if args.dump_raw:
                raw = ct.get_raw_api_responses()
                if raw:
                    print("\n--- Raw API Responses ---", file=sys.stderr)
                    print(json.dumps(raw, indent=2, default=str))
                else:
                    print("No API responses intercepted.", file=sys.stderr)
