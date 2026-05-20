"""Generate daily-high temperature threshold events from the keyless NWS API.

Pulls the daytime high forecast for a few major US stations from the National
Weather Service API (api.weather.gov, no key; a descriptive User-Agent is
required by NWS) and emits binary SHADOW events of the form "Will the high
temperature at <station> on <date> exceed <X>F?". Thresholds are pinned a few
degrees on either side of the forecast high so the events are genuinely
uncertain rather than near-certain, adding a non-sports, non-financial domain
to the shadow-calibration slate and contributing coverage in the hard
0.5-0.9 probability band.

Ticker grammar (one clean, self-describing form):

    SHADOW-WX-<STATION>-HIGH-GT-<X>-<YYYYMMDD>
        Resolves Yes if the NWS observed daily maximum temperature at the
        station on the event date is strictly greater than X degrees F.

<STATION> is a stable short code (e.g. NYC, LAX, ORD) and <X> is an integer
degree threshold with no internal hyphens.

NOTE ON RESOLUTION: auto_resolve_finance.py does NOT resolve these — weather
needs a separate resolver (NWS observed daily-high endpoint), to be added
later. This script only generates clean, leakage-free events with the exact
station, metric, timestamp, and source URLs; the description states precisely
how each one resolves.

Each event flows: NWS /points/{lat,lon} -> gridId/gridX/gridY -> the gridpoint
/forecast endpoint, whose daytime period for the target date gives the
forecast high used to center the thresholds.

Usage:
    python scripts/generate_weather_slate.py --date 20260521
    python scripts/generate_weather_slate.py --dry-run
    python scripts/generate_weather_slate.py --stations NYC,ORD
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

EVENTS = Path("data/shadow_calibration/events.json")
POINTS = "https://api.weather.gov/points/{lat},{lon}"
# NWS requires a descriptive User-Agent identifying the application + contact.
UA = "prophet-hacks-shadow-calibration/1.0 (contact: robbysneiderman@gmail.com)"

# Degree offsets around the forecast high. Tight band keeps each event in the
# genuinely uncertain zone instead of a near-certain Yes/No.
OFFSETS = [-4, -1, 0, 2, 5]

# Major stations: short code -> (lat, lon, human label).
STATIONS = {
    "NYC": (40.7831, -73.9712, "New York City (Central Park, NY)"),
    "LAX": (33.9416, -118.4085, "Los Angeles (LAX, CA)"),
    "ORD": (41.9742, -87.9073, "Chicago (O'Hare, IL)"),
    "DFW": (32.8998, -97.0403, "Dallas-Fort Worth (DFW, TX)"),
    "MIA": (25.7959, -80.2871, "Miami (MIA, FL)"),
    "DEN": (39.8561, -104.6737, "Denver (DEN, CO)"),
    "SEA": (47.4502, -122.3088, "Seattle (SEA, WA)"),
}


def _http_get(url: str, timeout: int = 25) -> str:
    proc = subprocess.run(
        ["curl", "-s", "--fail", "--max-time", str(timeout), "-H", f"User-Agent: {UA}", url],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit {proc.returncode}: {proc.stderr.strip() or url}")
    return proc.stdout


def forecast_high(lat: float, lon: float, date_str: str) -> tuple[int, str, str]:
    """Return (forecast_high_F, forecast_url, station_tz_offset) for the date.

    Resolves /points -> gridpoint /forecast, then finds the daytime period whose
    local start date matches the target YYYYMMDD.
    """
    pts = json.loads(_http_get(POINTS.format(lat=lat, lon=lon)))
    forecast_url = pts["properties"]["forecast"]
    fc = json.loads(_http_get(forecast_url))
    target = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    for period in fc["properties"]["periods"]:
        start = str(period.get("startTime") or "")
        if period.get("isDaytime") and start[:10] == target:
            if period.get("temperatureUnit") != "F":
                raise RuntimeError(f"unexpected temp unit {period.get('temperatureUnit')}")
            return int(period["temperature"]), forecast_url, start[10:]
    raise RuntimeError(f"no daytime forecast period for {target}")


def station_events(code: str, date_str: str, as_of: str) -> list[dict]:
    lat, lon, label = STATIONS[code]
    high, forecast_url, _tz = forecast_high(lat, lon, date_str)
    pretty = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    # Close at end of the local forecast day; 06:00Z next day safely covers all
    # contiguous-US time zones for the calendar date in question.
    close = (datetime.strptime(date_str, "%Y%m%d") + timedelta(days=1)).strftime("%Y-%m-%dT06:00:00Z")
    points_url = POINTS.format(lat=lat, lon=lon)
    out: list[dict] = []
    seen: set[int] = set()
    for off in OFFSETS:
        thr = high + off
        if thr in seen:
            continue
        seen.add(thr)
        ticker = f"SHADOW-WX-{code}-HIGH-GT-{thr}-{date_str}"
        out.append({
            "event_ticker": ticker,
            "market_ticker": ticker,
            "title": f"Will the high temperature at {label} on {pretty} exceed {thr}F?",
            "description": (
                f"Forecast whether the NWS observed daily maximum temperature at "
                f"{label} on {pretty} will be strictly greater than {thr}F. The NWS "
                f"daytime forecast high at queue time was {high}F, so this threshold "
                f"sits {off:+d}F from forecast. Resolves from the NWS observed daily "
                f"high for the station on the local calendar date."
            ),
            "category": "Weather",
            "close_time": close,
            "outcomes": ["Yes", "No"],
            "rules": (
                f"Resolve Yes if the NWS observed daily maximum temperature at "
                f"{label} on {pretty} (local calendar date) is strictly greater than "
                f"{thr}F. Resolve No otherwise. Use the official NWS observed daily high."
            ),
            "market_snapshot": {
                "as_of": as_of,
                "source_urls": [forecast_url, points_url],
                "notes": (
                    f"NWS daytime forecast high {high}F at queue time; threshold {off:+d}F "
                    f"from forecast to keep the event uncertain. Resolve from observed daily "
                    f"high, not the forecast."
                ),
            },
            "notes": (
                "Weather threshold calibration candidate; non-sports domain. "
                "Needs the dedicated NWS observed-high resolver (not auto_resolve_finance)."
            ),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y%m%d")
    ap.add_argument("--date", default=tomorrow, help="YYYYMMDD (default: tomorrow UTC)")
    ap.add_argument("--stations", default="NYC,LAX,ORD", help="comma-separated station codes")
    ap.add_argument("--events", type=Path, default=EVENTS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        datetime.strptime(args.date, "%Y%m%d")
    except ValueError:
        print(f"bad --date {args.date!r}; expected YYYYMMDD")
        return 2

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing = json.loads(args.events.read_text()) if args.events.exists() else []
    have = {e.get("event_ticker") for e in existing}

    codes = [s.strip().upper() for s in args.stations.split(",") if s.strip().upper() in STATIONS]
    unknown = [s.strip().upper() for s in args.stations.split(",")
               if s.strip() and s.strip().upper() not in STATIONS]
    for u in unknown:
        print(f"  unknown station {u}; known: {','.join(sorted(STATIONS))}")

    new: list[dict] = []
    for code in codes:
        try:
            evs = station_events(code, args.date, as_of)
            highs = sorted({int(e["event_ticker"].split("-")[-2]) for e in evs})
            print(f"  {code}: thresholds {highs}")
            for ev in evs:
                if ev["event_ticker"] not in have:
                    new.append(ev)
                    have.add(ev["event_ticker"])
        except (RuntimeError, ValueError, KeyError, IndexError) as exc:
            print(f"  {code}: fetch failed ({exc})")

    print(f"\nweather slate {args.date}: {len(new)} new event(s)")
    for ev in new:
        print(f"  + {ev['event_ticker']:<36} {ev['close_time']}")

    if not new:
        print("nothing to add.")
        return 0
    if args.dry_run:
        print("\n--dry-run: not writing.")
        return 0
    args.events.write_text(json.dumps(existing + new, indent=2) + "\n")
    print(f"\nappended {len(new)} event(s) to {args.events}")
    print("next: forecast them, then resolve via the NWS observed-high resolver")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
