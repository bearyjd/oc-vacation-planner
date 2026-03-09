NONSTOP_ROUTES = {
    "IAD": {
        "CUN": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "MBJ": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "SJU": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "PUJ": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "NAS": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "AUA": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "PLS": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "STT": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "SXM": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "BGI": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "UVF": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "GDL": {"airlines": ["UA"], "frequency": "daily", "region": "mexico"},
        "SJD": {"airlines": ["UA"], "frequency": "daily", "region": "mexico"},
        "SJO": {"airlines": ["UA"], "frequency": "daily", "region": "central_america"},
        "PTY": {"airlines": ["UA"], "frequency": "daily", "region": "central_america"},
        "BZE": {"airlines": ["UA"], "frequency": "daily", "region": "central_america"},
        "LIR": {"airlines": ["UA"], "frequency": "daily", "region": "central_america"},
        "LHR": {"airlines": ["UA", "BA"], "frequency": "daily", "region": "europe"},
        "CDG": {"airlines": ["UA", "AF"], "frequency": "daily", "region": "europe"},
        "FRA": {"airlines": ["UA", "LH"], "frequency": "daily", "region": "europe"},
        "AMS": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "FCO": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "BCN": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "LIS": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "ATH": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "IST": {"airlines": ["UA", "TK"], "frequency": "daily", "region": "europe"},
        "DUB": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "ZRH": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "MUC": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "MAD": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "CPH": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "ARN": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "EDI": {"airlines": ["UA"], "frequency": "daily", "region": "europe"},
        "NCE": {"airlines": ["UA"], "frequency": "seasonal", "region": "europe"},
        "VCE": {"airlines": ["UA"], "frequency": "seasonal", "region": "europe"},
        "NRT": {"airlines": ["UA"], "frequency": "daily", "region": "asia"},
        "ICN": {"airlines": ["UA"], "frequency": "daily", "region": "asia"},
        "DEL": {"airlines": ["UA"], "frequency": "daily", "region": "asia"},
        "BOM": {"airlines": ["UA"], "frequency": "daily", "region": "asia"},
        "HND": {"airlines": ["UA"], "frequency": "daily", "region": "asia"},
        "DOH": {"airlines": ["QR"], "frequency": "daily", "region": "middle_east"},
        "DXB": {"airlines": ["EK"], "frequency": "daily", "region": "middle_east"},
        "TLV": {"airlines": ["UA"], "frequency": "daily", "region": "middle_east"},
        "ADD": {"airlines": ["UA"], "frequency": "daily", "region": "africa"},
        "ACC": {"airlines": ["UA"], "frequency": "daily", "region": "africa"},
        "DSS": {"airlines": ["UA"], "frequency": "daily", "region": "africa"},
        "BOG": {"airlines": ["UA"], "frequency": "daily", "region": "south_america"},
        "GRU": {"airlines": ["UA"], "frequency": "daily", "region": "south_america"},
    },
    "DCA": {
        "SJU": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "CUN": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "NAS": {"airlines": ["UA"], "frequency": "daily", "region": "caribbean"},
        "LAX": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "SFO": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "ORD": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "DFW": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "MIA": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "ATL": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "BOS": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "SEA": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "DEN": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "MSP": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "PHX": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "MCO": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "TPA": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "FLL": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "RSW": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
        "SRQ": {"airlines": ["UA"], "frequency": "daily", "region": "domestic"},
    },
}


def get_beach_destinations(origin: str) -> list[dict]:
    """Return nonstop beach destinations from an airport."""
    if origin not in NONSTOP_ROUTES:
        return []
    
    beach_regions = {"caribbean", "mexico"}
    destinations = []
    
    for code, info in NONSTOP_ROUTES[origin].items():
        if info["region"] in beach_regions:
            destinations.append({
                "code": code,
                "region": info["region"],
                "airlines": info["airlines"],
                "frequency": info["frequency"]
            })
    
    return destinations


def get_destinations_by_region(origin: str, region: str) -> list[dict]:
    """Return nonstop destinations for a specific region."""
    if origin not in NONSTOP_ROUTES:
        return []
    
    destinations = []
    
    for code, info in NONSTOP_ROUTES[origin].items():
        if info["region"] == region:
            destinations.append({
                "code": code,
                "region": info["region"],
                "airlines": info["airlines"],
                "frequency": info["frequency"]
            })
    
    return destinations


def get_all_nonstop_codes(origin: str) -> list[str]:
    """Return all airport codes reachable nonstop from origin."""
    if origin not in NONSTOP_ROUTES:
        return []
    
    return list(NONSTOP_ROUTES[origin].keys())
