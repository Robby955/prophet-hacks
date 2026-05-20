"""Generate a price-threshold GRID as SHADOW events spanning easy->hard.

Fetches the current level of a handful of liquid instruments from keyless
public sources (SPY/QQQ/GLD via the Yahoo Finance chart API range=1d, BTC/ETH
via Coinbase spot, all via curl) and emits a GRID of binary "close above /
price >= X" threshold events at several distances from the live price. By
placing thresholds at, just below, and just above the current price, the grid
manufactures events that span the easy -> hard difficulty range so the shadow
loop earns calibration coverage across the 0.5-0.9 probability band instead of
piling up near-certain outcomes.

Every emitted ticker uses the grammar that scripts/auto_resolve_finance.py
already parses, so the loop closes with zero new resolver work:

  Equities/ETFs:  SHADOW-FIN-<SYM>-CLOSE-ABOVE-<THRESH>-<YYYYMMDD>
                  (close strictly greater than THRESH on event date)
  Crypto:         SHADOW-CRYPTO-<SYM>-ABOVE-<THRESH>-<YYYYMMDD>   (> at close_time)
                  SHADOW-CRYPTO-<SYM>-GE-<THRESH>-<YYYYMMDD>      (>= at close_time)

THRESH is an integer with no internal hyphens, which is exactly what
auto_resolve_finance.parse_spec extracts and compares. Events are appended to
data/shadow_calibration/events.json, deduped by ticker.

Usage:
    python scripts/generate_threshold_slate.py --date 20260521
    python scripts/generate_threshold_slate.py --dry-run
    python scripts/generate_threshold_slate.py --equities SPY,QQQ --crypto BTC
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

EVENTS = Path("data/shadow_calibration/events.json")
YAHOO_1D = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1d"
COINBASE_SPOT = "https://api.coinbase.com/v2/prices/{product}/spot"
UA = "Mozilla/5.0 (prophet-hacks-shadow-resolver/1.0)"

# Fraction-of-price offsets for the grid. Spanning below->above the live price
# manufactures a difficulty gradient: deep-OTM (near-certain No), at-the-money
# (~coin-flip), deep-ITM (near-certain Yes), with the +/-1-3% rungs landing in
# the genuinely uncertain 0.5-0.9 band over a one-day horizon.
GRID = [0.97, 0.99, 1.00, 1.01, 1.03]

EQUITY_NAMES = {
    "SPY": "the SPDR S&P 500 ETF Trust (SPY)",
    "QQQ": "the Invesco QQQ Trust (QQQ)",
    "GLD": "the SPDR Gold Shares ETF (GLD)",
    "DIA": "the SPDR Dow Jones Industrial Average ETF (DIA)",
    "IWM": "the iShares Russell 2000 ETF (IWM)",
}
CRYPTO_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "LTC": "Litecoin",
}


def _http_get(url: str, timeout: int = 20) -> str:
    proc = subprocess.run(
        ["curl", "-s", "--fail", "--max-time", str(timeout), "-H", f"User-Agent: {UA}", url],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit {proc.returncode}: {proc.stderr.strip() or url}")
    return proc.stdout


def fetch_equity_price(symbol: str) -> float:
    """Latest quoted level for an equity/ETF via the Yahoo chart API."""
    data = json.loads(_http_get(YAHOO_1D.format(sym=symbol.upper())))
    meta = data["chart"]["result"][0]["meta"]
    px = meta.get("regularMarketPrice") or meta.get("chartPreviousClose")
    if px is None:
        raise RuntimeError(f"no price in Yahoo payload for {symbol}")
    return float(px)


def fetch_crypto_price(symbol: str) -> float:
    """Live spot for a crypto pair via Coinbase."""
    data = json.loads(_http_get(COINBASE_SPOT.format(product=f"{symbol.upper()}-USD")))
    return float(data["data"]["amount"])


def _thresholds(price: float) -> list[int]:
    """Integer thresholds across the grid, deduped and order-preserving.

    Integers keep the THRESH token hyphen-free and digit-only, which is exactly
    what auto_resolve_finance.parse_spec requires to recover the number.
    """
    seen: set[int] = set()
    out: list[int] = []
    for mult in GRID:
        thr = round(price * mult)
        if thr not in seen:
            seen.add(thr)
            out.append(thr)
    return out


def _pretty_date(date_str: str) -> str:
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


def equity_events(symbol: str, price: float, date_str: str, as_of: str) -> list[dict]:
    sym = symbol.upper()
    name = EQUITY_NAMES.get(sym, f"the {sym} ETF/equity ({sym})")
    pretty = _pretty_date(date_str)
    close = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}T20:00:00Z"  # ~US market close
    yq = f"https://finance.yahoo.com/quote/{sym}/"
    out: list[dict] = []
    for thr in _thresholds(price):
        ticker = f"SHADOW-FIN-{sym}-CLOSE-ABOVE-{thr}-{date_str}"
        dist = (thr / price - 1.0) * 100.0
        out.append({
            "event_ticker": ticker,
            "market_ticker": ticker,
            "title": f"Will {sym} close above {thr}.00 on {pretty}?",
            "description": (
                f"Forecast whether {name} official regular-session close on "
                f"{pretty} will be strictly greater than {thr}.00. Threshold is "
                f"{dist:+.1f}% from the {price:.2f} level quoted at queue time."
            ),
            "category": "Financial Markets",
            "close_time": close,
            "outcomes": ["Yes", "No"],
            "rules": (
                f"Resolve Yes if {sym}'s official regular-session close on {pretty} "
                f"is strictly greater than {thr}.00. Resolve No otherwise."
            ),
            "market_snapshot": {
                "as_of": as_of,
                "source_urls": [yq, f"{yq}history/"],
                "notes": (
                    f"Grid rung at {dist:+.1f}% of the {price:.2f} level quoted via the "
                    f"Yahoo chart API at queue time; use the official close for resolution."
                ),
            },
            "notes": (
                "Threshold-grid calibration candidate; same-day financial close. "
                "Grid spans easy->hard to cover the 0.5-0.9 band."
            ),
        })
    return out


def crypto_events(symbol: str, price: float, date_str: str, as_of: str) -> list[dict]:
    sym = symbol.upper()
    name = CRYPTO_NAMES.get(sym, sym)
    pretty = _pretty_date(date_str)
    close = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}T00:00:00Z"  # threshold at 00:00 UTC
    cb = f"https://www.coinbase.com/price/{name.lower()}"
    yq = f"https://finance.yahoo.com/quote/{sym}-USD/"
    out: list[dict] = []
    for i, thr in enumerate(_thresholds(price)):
        # Alternate ABOVE (>) and GE (>=) so both grammars get exercised.
        rel, op_word, op_sym = ("GE", "at or above", ">=") if i % 2 else ("ABOVE", "above", ">")
        ticker = f"SHADOW-CRYPTO-{sym}-{rel}-{thr}-{date_str}"
        dist = (thr / price - 1.0) * 100.0
        out.append({
            "event_ticker": ticker,
            "market_ticker": ticker,
            "title": f"Will {name} be {op_word} {thr} USD at 00:00 UTC on {pretty}?",
            "description": (
                f"Forecast whether the {sym}-USD reference price will be {op_word} "
                f"{thr} at 00:00 UTC on {pretty}. Threshold is {dist:+.1f}% from the "
                f"{price:.2f} spot quoted at queue time."
            ),
            "category": "Crypto",
            "close_time": close,
            "outcomes": ["Yes", "No"],
            "rules": (
                f"Resolve Yes if {sym}-USD is {op_sym} {thr} at {close} using a major "
                f"reference quote such as Coinbase or Yahoo Finance. Resolve No otherwise."
            ),
            "market_snapshot": {
                "as_of": as_of,
                "source_urls": [cb, yq],
                "notes": (
                    f"Grid rung at {dist:+.1f}% of the {price:.2f} Coinbase spot quoted at "
                    f"queue time; resolves at a precise UTC timestamp."
                ),
            },
            "notes": (
                "Threshold-grid calibration candidate; short-horizon crypto. "
                "Grid spans easy->hard to cover the 0.5-0.9 band."
            ),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y%m%d")
    ap.add_argument("--date", default=tomorrow, help="YYYYMMDD (default: tomorrow UTC)")
    ap.add_argument("--equities", default="SPY,QQQ,GLD", help="comma-separated equity/ETF symbols")
    ap.add_argument("--crypto", default="BTC,ETH", help="comma-separated crypto symbols")
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

    equities = [s.strip().upper() for s in args.equities.split(",") if s.strip()]
    cryptos = [s.strip().upper() for s in args.crypto.split(",") if s.strip()]

    new: list[dict] = []
    for sym in equities:
        try:
            px = fetch_equity_price(sym)
            print(f"  {sym}: {px:.2f} (Yahoo)  thresholds {_thresholds(px)}")
            for ev in equity_events(sym, px, args.date, as_of):
                if ev["event_ticker"] not in have:
                    new.append(ev)
                    have.add(ev["event_ticker"])
        except (RuntimeError, ValueError, KeyError, IndexError) as exc:
            print(f"  {sym}: fetch failed ({exc})")
    for sym in cryptos:
        try:
            px = fetch_crypto_price(sym)
            print(f"  {sym}: {px:.2f} (Coinbase)  thresholds {_thresholds(px)}")
            for ev in crypto_events(sym, px, args.date, as_of):
                if ev["event_ticker"] not in have:
                    new.append(ev)
                    have.add(ev["event_ticker"])
        except (RuntimeError, ValueError, KeyError, IndexError) as exc:
            print(f"  {sym}: fetch failed ({exc})")

    print(f"\nthreshold slate {args.date}: {len(new)} new event(s)")
    for ev in new:
        print(f"  + {ev['event_ticker']:<42} {ev['close_time']}")

    if not new:
        print("nothing to add.")
        return 0
    if args.dry_run:
        print("\n--dry-run: not writing.")
        return 0
    args.events.write_text(json.dumps(existing + new, indent=2) + "\n")
    print(f"\nappended {len(new)} event(s) to {args.events}")
    print("next: forecast them, then auto_resolve_finance.py after close")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
