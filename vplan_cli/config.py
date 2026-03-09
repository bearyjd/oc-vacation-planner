import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _env_path = Path.home() / ".vplan" / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
    else:
        load_dotenv()
except ImportError:
    pass

VPLAN_DIR = Path.home() / ".vplan"
CONFIG_PATH = VPLAN_DIR / "config.json"

# Defaults — overridden by ~/.vplan/config.json if it exists
_DEFAULT_FAMILY = {
    "name": "John Beary",
    "adults": 2,
    "kids": ["Ford", "John", "Pennington"],
    "home_airports": ["IAD", "DCA"],
    "continents_visited": 7,
}

_DEFAULT_POINTS = {
    "chase_ur": {
        "balance": 552585,
        "portal_multiplier": 1.5,
        "portal_value_cents": 552585 * 1.5,
        "expires": "2027-10-01",
    },
    "united": {
        "balance": 778858,
        "status": "Premier 1K",
        "plus_points": 320,
        "travel_bank_usd": 200,
    },
    "delta": {
        "balance": 731691,
    },
}

_DEFAULT_SWEET_SPOTS = [
    {"from": "Chase UR", "to": "Hyatt", "ratio": "1:1", "note": "Best hotel value"},
    {"from": "Chase UR", "to": "United", "ratio": "1:1", "note": "Star Alliance awards"},
    {"from": "United", "to": "ANA/Lufthansa/Singapore", "ratio": "partner", "note": "Star Alliance partners"},
    {"from": "Delta", "to": "Air France/KLM Flying Blue", "ratio": "partner", "note": "SkyTeam partners"},
]


def _load_user_config() -> dict:
    """Load user config from ~/.vplan/config.json, return empty dict if not found."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


_user_cfg = _load_user_config()

FAMILY = _user_cfg.get("family", _DEFAULT_FAMILY)
POINTS = _user_cfg.get("points", _DEFAULT_POINTS)
SWEET_SPOTS = _user_cfg.get("sweet_spots", _DEFAULT_SWEET_SPOTS)


def update_config(section: str, data: dict | list):
    """Update a section of the user config and save to disk."""
    cfg = _load_user_config()
    cfg[section] = data
    ensure_dir()
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)
    global FAMILY, POINTS, SWEET_SPOTS, _user_cfg
    _user_cfg = cfg
    FAMILY = cfg.get("family", _DEFAULT_FAMILY)
    POINTS = cfg.get("points", _DEFAULT_POINTS)
    SWEET_SPOTS = cfg.get("sweet_spots", _DEFAULT_SWEET_SPOTS)

KARAKEEP_URL = os.environ.get("KARAKEEP_URL", "")
KARAKEEP_API_KEY = os.environ.get("KARAKEEP_API_KEY", "")

LITEAPI_KEY = os.environ.get("LITEAPI_KEY", "")

CREDENTIALS_PATH = VPLAN_DIR / "credentials.json"

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def ensure_dir():
    VPLAN_DIR.mkdir(mode=0o700, exist_ok=True)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_config(cfg: dict):
    ensure_dir()
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


def load_credentials() -> dict:
    if CREDENTIALS_PATH.exists():
        with open(CREDENTIALS_PATH) as f:
            return json.load(f)
    return {}


def save_credentials(service: str, username: str, password: str):
    ensure_dir()
    creds = load_credentials()
    creds[service] = {"username": username, "password": password}
    with open(CREDENTIALS_PATH, "w") as f:
        json.dump(creds, f, indent=2)
    os.chmod(CREDENTIALS_PATH, 0o600)


def get_credentials(service: str) -> tuple[str, str] | None:
    creds = load_credentials()
    entry = creds.get(service)
    if entry and "username" in entry and "password" in entry:
        return entry["username"], entry["password"]
    return None


TRIPS_DIR = VPLAN_DIR / "trips"


def _ensure_trips_dir():
    ensure_dir()
    TRIPS_DIR.mkdir(mode=0o700, exist_ok=True)


def save_trip(name: str, data: dict) -> Path:
    _ensure_trips_dir()
    slug = name.lower().replace(" ", "-")
    for ch in "/:?#[]@!$&'()*+,;=":
        slug = slug.replace(ch, "")
    path = TRIPS_DIR / f"{slug}.json"
    data["_name"] = name
    data["_slug"] = slug
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)
    return path


def load_trip(slug: str) -> dict | None:
    path = TRIPS_DIR / f"{slug}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def list_trips() -> list[dict]:
    _ensure_trips_dir()
    trips = []
    for path in sorted(TRIPS_DIR.glob("*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            trips.append({
                "slug": path.stem,
                "name": data.get("_name", path.stem),
                "destination": data.get("destination", ""),
                "month": data.get("month", ""),
                "file": str(path),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return trips


def delete_trip(slug: str) -> bool:
    path = TRIPS_DIR / f"{slug}.json"
    if path.exists():
        path.unlink()
        return True
    return False
