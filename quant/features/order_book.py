"""Order book snapshot features.

Reads a pre-injected ``_ob_snapshot`` column (list of dicts with keys
``bids`` and ``asks``, each a list of [price, qty] pairs) from the
DataFrame. If the column is absent or the snapshot is None/empty, all
order book features default to 0.0 (neutral), ensuring graceful
degradation during backtesting when no L2 data is available.

Features (6):
    bid_ask_spread_bps   — (ask - bid) / mid × 10000
    book_imbalance_5     — (bid_qty_top5 - ask_qty_top5) / total_top5
    book_imbalance_20    — Same for full 20-level depth
    depth_ratio_5        — sum(bid_notional_top5) / sum(ask_notional_top5)
    volume_at_touch_ratio— qty_at_best_bid / qty_at_best_ask
    spread_vol_ratio     — bid_ask_spread_bps / realized_vol_5 (if available)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _parse_snapshot(snapshot: object) -> tuple[list, list]:
    """Extract (bids, asks) from a snapshot dict. Returns empty lists on failure."""
    if not isinstance(snapshot, dict):
        return [], []
    bids = snapshot.get("bids") or []
    asks = snapshot.get("asks") or []
    return bids, asks


def _safe_qty_sum(levels: list, n: int) -> float:
    """Sum qty (index 1) for up to n levels, returning 0.0 on any failure."""
    try:
        return float(sum(float(lvl[1]) for lvl in levels[:n] if len(lvl) >= 2))
    except (TypeError, ValueError, IndexError):
        return 0.0


def _safe_notional_sum(levels: list, n: int) -> float:
    """Sum price×qty for up to n levels."""
    try:
        return float(sum(float(lvl[0]) * float(lvl[1]) for lvl in levels[:n] if len(lvl) >= 2))
    except (TypeError, ValueError, IndexError):
        return 0.0


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """Compute order book snapshot features.

    Reads ``_ob_snapshot`` column if present; otherwise all features are 0.0.
    Safe to call during backtesting (no live L2 data available).
    """
    out = df.copy()

    n = len(df)
    bid_ask_spread_bps = np.zeros(n, dtype=float)
    book_imbalance_5 = np.zeros(n, dtype=float)
    book_imbalance_20 = np.zeros(n, dtype=float)
    depth_ratio_5 = np.ones(n, dtype=float)   # 1.0 = neutral balance
    vol_at_touch_ratio = np.ones(n, dtype=float)
    spread_vol_ratio = np.zeros(n, dtype=float)

    has_snapshots = "_ob_snapshot" in df.columns

    for i, snap in enumerate(df["_ob_snapshot"] if has_snapshots else []):
        bids, asks = _parse_snapshot(snap)
        if not bids or not asks:
            continue

        try:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
        except (IndexError, TypeError, ValueError):
            continue

        mid = (best_bid + best_ask) / 2.0
        if mid <= 0.0:
            continue

        # --- Bid-ask spread in bps ---
        bid_ask_spread_bps[i] = (best_ask - best_bid) / mid * 10_000.0

        # --- Book imbalance (5 levels) ---
        bid_qty5 = _safe_qty_sum(bids, 5)
        ask_qty5 = _safe_qty_sum(asks, 5)
        total5 = bid_qty5 + ask_qty5
        if total5 > 0.0:
            book_imbalance_5[i] = (bid_qty5 - ask_qty5) / total5

        # --- Book imbalance (20 levels) ---
        bid_qty20 = _safe_qty_sum(bids, 20)
        ask_qty20 = _safe_qty_sum(asks, 20)
        total20 = bid_qty20 + ask_qty20
        if total20 > 0.0:
            book_imbalance_20[i] = (bid_qty20 - ask_qty20) / total20

        # --- Depth ratio (top 5, notional-weighted) ---
        bid_not5 = _safe_notional_sum(bids, 5)
        ask_not5 = _safe_notional_sum(asks, 5)
        if ask_not5 > 0.0:
            depth_ratio_5[i] = bid_not5 / ask_not5

        # --- Volume at touch (best bid vs best ask qty) ---
        try:
            best_bid_qty = float(bids[0][1])
            best_ask_qty = float(asks[0][1])
            if best_ask_qty > 0.0:
                vol_at_touch_ratio[i] = best_bid_qty / best_ask_qty
        except (IndexError, TypeError, ValueError):
            pass

    out["bid_ask_spread_bps"] = bid_ask_spread_bps
    out["book_imbalance_5"] = book_imbalance_5
    out["book_imbalance_20"] = book_imbalance_20
    out["depth_ratio_5"] = np.log(np.clip(depth_ratio_5, 1e-6, 1e6))  # log-scale ratio
    out["volume_at_touch_ratio"] = np.log(np.clip(vol_at_touch_ratio, 1e-6, 1e6))

    # --- Spread-volatility ratio ---
    if "realized_vol_5" in df.columns:
        rv5 = pd.to_numeric(df["realized_vol_5"], errors="coerce").fillna(1e-9)
        out["spread_vol_ratio"] = out["bid_ask_spread_bps"] / (rv5 * 10_000.0 + 1e-9)
    else:
        out["spread_vol_ratio"] = 0.0

    return out
