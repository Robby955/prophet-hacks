"""connectors/ — OPTIONAL read-only research connectors.

Per the v6 playbook Section 12: every connector record needs
`source_id`, `URL/API endpoint`, `retrieved_at`, `event_id/market_id`,
`source_type`, `quality_score`, `staleness_score`, and whether it
changed `p_final`. If it doesn't enter the trace, it does not exist.

Currently included as stubs (no live calls):
- `kalshi.py` — optional read-only microstructure/pastcast connector
- `polymarket.py` — optional cross-market consensus comparator

NOT included (forbidden by policy):
- Discord scraper (Discord Developer Policy prohibits)
- Live-money trading on any external venue
- Hardcoded credentials of any kind
"""
