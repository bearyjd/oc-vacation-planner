from __future__ import annotations

import json
import sys
import time
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    sync_playwright,
)
from playwright_stealth import Stealth


SEATS_HOME_URL = "https://seats.aero"


class SeatsAeroScraper:

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._stealth = Stealth()

    @property
    def _p(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started")
        return self._page

    def _ensure_browser(self) -> None:
        if self._browser and self._browser.is_connected():
            return
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        self._stealth.apply_stealth_sync(self._context)
        self._page = self._context.new_page()
        self._p.set_default_timeout(30_000)

    @staticmethod
    def _normalize_trip(raw: dict[str, Any]) -> dict[str, Any]:
        duration_min = raw.get("totalDuration") or 0
        if isinstance(duration_min, (int, float)) and duration_min > 0:
            hours, minutes = divmod(int(duration_min), 60)
            duration_str = f"{hours}h {minutes}m"
        else:
            duration_str = ""

        taxes_raw = raw.get("totalTaxes") or 0
        taxes_currency = raw.get("taxesCurrency", "USD")
        if isinstance(taxes_raw, (int, float)):
            taxes_val = taxes_raw / 100 if taxes_raw > 100 else taxes_raw
        else:
            taxes_val = 0.0

        departs_at = raw.get("departsAt", "")
        arrives_at = raw.get("arrivesAt", "")
        date_str = ""
        depart_time = ""
        arrive_time = ""
        if departs_at:
            date_str = departs_at[:10]
            depart_time = departs_at[11:16] if len(departs_at) > 15 else ""
        if arrives_at:
            arrive_time = arrives_at[11:16] if len(arrives_at) > 15 else ""

        return {
            "source": raw.get("source", ""),
            "carriers": raw.get("carriers", ""),
            "route": raw.get("route", f"{raw.get('originAirport', '')}-{raw.get('destinationAirport', '')}"),
            "mileage_cost": raw.get("mileageCost") or 0,
            "remaining_seats": raw.get("remainingSeats") or 0,
            "taxes": round(taxes_val, 2),
            "taxes_currency": taxes_currency,
            "taxes_usd": round(taxes_val, 2),
            "cabin": raw.get("cabin", ""),
            "stops": raw.get("stops", 0),
            "duration": duration_str,
            "date": date_str,
            "depart_time": depart_time,
            "arrive_time": arrive_time,
            "flight_numbers": raw.get("flightNumbers", ""),
            "origin": raw.get("originAirport", ""),
            "destination": raw.get("destinationAirport", ""),
            "created_at": raw.get("createdAt", "")[:10] if raw.get("createdAt") else "",
        }

    def search_flights(
        self,
        origin: str,
        destination: str,
        cabin: str = "economy",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        try:
            self._ensure_browser()

            trips: list[dict[str, Any]] = []
            target_params = (
                f"origin={origin.upper()}"
                f"&destination={destination.upper()}"
                f"&cabin={cabin.lower()}"
                f"&order_by=recency"
                f"&limit={limit}"
                f"&kind=normal"
            )

            def rewrite_deals(route: Route) -> None:
                new_url = route.request.url.split("?")[0] + "?" + target_params
                route.continue_(url=new_url)

            def capture_trips(response: Any) -> None:
                if "/_api/v1/deals" in response.url and response.status == 200:
                    try:
                        body = response.json()
                        if isinstance(body, dict) and "trips" in body:
                            trips.extend(body["trips"])
                    except Exception:
                        pass

            self._p.route("**/_api/v1/deals*", rewrite_deals)
            self._p.on("response", capture_trips)

            self._p.goto(SEATS_HOME_URL, timeout=60_000, wait_until="networkidle")
            time.sleep(3)

            self._p.unroute("**/_api/v1/deals*")
            self._p.remove_listener("response", capture_trips)

            return [self._normalize_trip(t) for t in trips]

        except Exception as exc:
            print(f"scraper error: {exc}", file=sys.stderr)
            return []

    def close(self) -> None:
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

    def __enter__(self) -> SeatsAeroScraper:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def search_flights(
    origin: str,
    destination: str,
    cabin: str = "economy",
    limit: int = 50,
) -> list[dict[str, Any]]:
    with SeatsAeroScraper(headless=True) as scraper:
        return scraper.search_flights(origin, destination, cabin, limit)


def search_round_trip(
    origin: str,
    destination: str,
    cabin: str = "economy",
    limit: int = 50,
) -> dict[str, list[dict[str, Any]]]:
    with SeatsAeroScraper(headless=True) as scraper:
        outbound = scraper.search_flights(origin, destination, cabin, limit)
        ret = scraper.search_flights(destination, origin, cabin, limit)
        return {"outbound": outbound, "return": ret}


if __name__ == "__main__":
    with SeatsAeroScraper(headless=True) as s:
        results = s.search_flights("IAD", "CUN", "economy")
        if results:
            print(json.dumps(results[:10], indent=2))
            print(f"\n{len(results)} flights found", file=sys.stderr)
        else:
            print("no results found", file=sys.stderr)
