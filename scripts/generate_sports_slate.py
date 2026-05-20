"""Generate a clean pregame sports slate as SHADOW events.

Pulls a date's *scheduled* (not-yet-started) games from the keyless ESPN
scoreboard API for MLB/NBA/NHL/WNBA and emits SHADOW events with exact
full-team outcome labels and the real game-start close_time, appended to
``data/shadow_calibration/events.json`` (deduped by ticker).

Pairing this with the pregame forecast runner + auto_resolve_sports.py turns the
daily sports schedule into a leakage-free calibration firehose: forecast before
first pitch, resolve after the final. Zero leakage by construction — the games
haven't happened when we query.

Outcome labels are the ESPN displayNames, which auto_resolve_sports.py matches
verbatim, so the loop closes cleanly.

Usage:
    python scripts/generate_sports_slate.py --date 20260520
    python scripts/generate_sports_slate.py --leagues MLB,NBA --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

EVENTS = Path("data/shadow_calibration/events.json")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={date}"
LEAGUE_PATHS = {
    "MLB": "baseball/mlb",
    "NBA": "basketball/nba",
    "NHL": "hockey/nhl",
    "WNBA": "basketball/wnba",
}
LEAGUE_NOUN = {"MLB": "MLB", "NBA": "NBA", "NHL": "NHL", "WNBA": "WNBA"}
UA = "Mozilla/5.0 (prophet-hacks-shadow-resolver/1.0)"


def _http_get(url: str, timeout: int = 20) -> str:
    proc = subprocess.run(
        ["curl", "-s", "--fail", "--max-time", str(timeout), "-H", f"User-Agent: {UA}", url],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit {proc.returncode}: {proc.stderr.strip() or url}")
    return proc.stdout


def slate_for(league: str, date_str: str) -> list[dict]:
    """Return SHADOW event dicts for scheduled games on date_str (YYYYMMDD)."""
    data = json.loads(_http_get(ESPN.format(path=LEAGUE_PATHS[league], date=date_str)))
    out: list[dict] = []
    for ev in data.get("events", []) or []:
        for comp in ev.get("competitions", []) or []:
            status = (((comp.get("status") or {}).get("type") or {}).get("name")) or ""
            if "SCHEDULED" not in status.upper() and "PRE" not in status.upper():
                continue  # only future games stay leakage-free
            comps = comp.get("competitors", []) or []
            away = next((c for c in comps if c.get("homeAway") == "away"), None)
            home = next((c for c in comps if c.get("homeAway") == "home"), None)
            if not away or not home:
                continue
            a_t, h_t = away.get("team") or {}, home.get("team") or {}
            a_name, h_name = a_t.get("displayName"), h_t.get("displayName")
            a_abbr, h_abbr = a_t.get("abbreviation"), h_t.get("abbreviation")
            if not (a_name and h_name and a_abbr and h_abbr):
                continue
            start = comp.get("date") or ev.get("date")  # ISO start time
            try:
                close = datetime.fromisoformat(str(start).replace("Z", "+00:00")) \
                    .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (ValueError, TypeError):
                close = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}T23:59:00Z"
            ticker = f"SHADOW-{league}-{a_abbr.upper()}-{h_abbr.upper()}-{date_str}"
            pretty = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            link = (ev.get("links") or [{}])[0].get("href") or "https://www.espn.com"
            out.append({
                "event_ticker": ticker,
                "market_ticker": ticker,
                "title": f"Who will win the {a_name} at {h_name} {LEAGUE_NOUN[league]} game on {pretty}?",
                "description": f"Resolves to the winner of the {a_name} at {h_name} "
                               f"{LEAGUE_NOUN[league]} game scheduled {close}. No draws.",
                "category": "Sports",
                "outcomes": [a_name, h_name],
                "close_time": close,
                "market_snapshot": {
                    "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "notes": "Pregame slate auto-generated from ESPN schedule.",
                    "source_urls": [link, ESPN.format(path=LEAGUE_PATHS[league], date=date_str)],
                },
                "notes": "Forward calibration candidate; auto-generated pregame slate.",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"),
                    help="YYYYMMDD (default: today UTC)")
    ap.add_argument("--leagues", default="MLB,NBA,NHL,WNBA")
    ap.add_argument("--events", type=Path, default=EVENTS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    leagues = [s.strip().upper() for s in args.leagues.split(",") if s.strip().upper() in LEAGUE_PATHS]
    existing = json.loads(args.events.read_text()) if args.events.exists() else []
    have = {e.get("event_ticker") for e in existing}

    new: list[dict] = []
    for lg in leagues:
        try:
            for game in slate_for(lg, args.date):
                if game["event_ticker"] not in have:
                    new.append(game)
                    have.add(game["event_ticker"])
        except (RuntimeError, ValueError) as exc:
            print(f"  {lg}: fetch failed ({exc})")

    print(f"slate {args.date}: {len(new)} new scheduled game(s)")
    for g in new:
        print(f"  + {g['event_ticker']:<34} {g['close_time']}  {g['outcomes'][0]} @ {g['outcomes'][1]}")

    if not new:
        print("nothing to add.")
        return 0
    if args.dry_run:
        print("\n--dry-run: not writing.")
        return 0
    args.events.write_text(json.dumps(existing + new, indent=2) + "\n")
    print(f"\nappended {len(new)} event(s) to {args.events}")
    print("next: forecast them pregame, then auto_resolve_sports.py after finals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
