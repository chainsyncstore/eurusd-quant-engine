"""Transaction cost model for Binance Futures perpetual contracts.

Covers:
- Maker/taker fees (configurable via env vars)
- Square-root market impact (function of trade notional vs. average hourly volume)
- Round-trip cost estimate (entry + exit)
- Cost-coverage check: signal edge must exceed 1.5× round-trip cost to trade
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default fee schedule (Binance Futures, VIP0 non-BNB-discount)
# Override via environment variables for accurate production costs.
# ---------------------------------------------------------------------------
_DEFAULT_MAKER_FEE_BPS: float = 2.0   # 0.02% — limit orders (current execution path)
_DEFAULT_TAKER_FEE_BPS: float = 4.0   # 0.04% — market fallback orders

# Minimum edge-to-cost coverage ratio required to pass the cost gate.
# Signal edge must be >= COST_COVERAGE_RATIO × round_trip_cost to be tradeable.
_COST_COVERAGE_RATIO: float = 1.5

# Market impact model: impact_bps = IMPACT_COEFF × sqrt(notional / adv)
# IMPACT_COEFF of 10.0 is conservative for Binance top-10 perp liquidity.
_IMPACT_COEFF: float = 10.0

# Fallback ADV (average daily volume in USD) per symbol when live data unavailable.
# Using conservative lower estimates to avoid understating costs.
_FALLBACK_ADV_USD: dict[str, float] = {
    "BTCUSDT":  1_500_000_000.0,
    "ETHUSDT":    600_000_000.0,
    "BNBUSDT":     80_000_000.0,
    "XRPUSDT":    150_000_000.0,
    "SOLUSDT":    200_000_000.0,
    "ADAUSDT":     60_000_000.0,
    "DOGEUSDT":    80_000_000.0,
    "AVAXUSDT":    80_000_000.0,
    "LINKUSDT":    50_000_000.0,
    "LTCUSDT":     40_000_000.0,
}
_DEFAULT_ADV_USD: float = 50_000_000.0  # fallback for unknown symbols


@dataclass(frozen=True)
class CostEstimate:
    """Round-trip cost breakdown for a single trade."""

    symbol: str
    notional_usd: float
    maker_fee_bps: float
    taker_fee_bps: float
    impact_bps: float
    round_trip_cost_bps: float    # total cost assuming limit entry + limit exit
    round_trip_cost_usd: float
    is_economic: bool             # True if edge > COST_COVERAGE_RATIO × cost
    min_edge_bps: float           # minimum edge required to pass cost gate

    def as_signal_bps(self) -> float:
        """Cost expressed as probability-equivalent basis points for edge comparison."""
        return self.round_trip_cost_bps


class BinanceCostModel:
    """Estimate round-trip transaction costs for Binance Futures perpetuals.

    Parameters
    ----------
    maker_fee_bps : float
        Maker (limit) fee in basis points. Default: 2.0 bps (0.02%).
        Override via BOT_MAKER_FEE_BPS environment variable.
    taker_fee_bps : float
        Taker (market) fee in basis points. Default: 4.0 bps (0.04%).
        Override via BOT_TAKER_FEE_BPS environment variable.
    use_taker_for_exit : bool
        Whether to assume exit is a taker (market) order. Default: False
        (assumes limit exit, which is common with reduce-only limits).
    """

    def __init__(
        self,
        maker_fee_bps: float | None = None,
        taker_fee_bps: float | None = None,
        use_taker_for_exit: bool = False,
    ) -> None:
        self.maker_fee_bps = maker_fee_bps or float(
            os.getenv("BOT_MAKER_FEE_BPS", str(_DEFAULT_MAKER_FEE_BPS))
        )
        self.taker_fee_bps = taker_fee_bps or float(
            os.getenv("BOT_TAKER_FEE_BPS", str(_DEFAULT_TAKER_FEE_BPS))
        )
        self.use_taker_for_exit = use_taker_for_exit

    def estimate(
        self,
        symbol: str,
        notional_usd: float,
        adv_usd: float | None = None,
    ) -> CostEstimate:
        """Compute round-trip cost estimate for a trade.

        Parameters
        ----------
        symbol : str
            Trading symbol, e.g. "BTCUSDT".
        notional_usd : float
            Trade notional in USD (quantity × price).
        adv_usd : float | None
            Average daily volume in USD. If None, uses built-in fallback table.

        Returns
        -------
        CostEstimate
            Full cost breakdown including market impact and cost-gate result.
        """
        if notional_usd <= 0.0:
            return CostEstimate(
                symbol=symbol,
                notional_usd=notional_usd,
                maker_fee_bps=self.maker_fee_bps,
                taker_fee_bps=self.taker_fee_bps,
                impact_bps=0.0,
                round_trip_cost_bps=0.0,
                round_trip_cost_usd=0.0,
                is_economic=False,
                min_edge_bps=0.0,
            )

        adv = adv_usd or _FALLBACK_ADV_USD.get(symbol, _DEFAULT_ADV_USD)

        # Participation rate: what fraction of hourly volume is this trade?
        hourly_adv = adv / 24.0
        participation = notional_usd / max(hourly_adv, 1.0)

        # Square-root impact model
        impact_bps = _IMPACT_COEFF * (participation ** 0.5)

        # Round-trip fee: entry (maker) + exit (maker or taker)
        entry_fee_bps = self.maker_fee_bps
        exit_fee_bps = self.taker_fee_bps if self.use_taker_for_exit else self.maker_fee_bps
        total_fee_bps = entry_fee_bps + exit_fee_bps

        # Total cost: fees both ways + impact on entry
        round_trip_cost_bps = total_fee_bps + impact_bps
        round_trip_cost_usd = notional_usd * round_trip_cost_bps / 10_000.0

        min_edge_bps = round_trip_cost_bps * _COST_COVERAGE_RATIO

        logger.debug(
            "Cost estimate: %s notional=%.0f fee=%.2fbps impact=%.2fbps "
            "round_trip=%.2fbps (%.4f USD) min_edge=%.2fbps",
            symbol, notional_usd, total_fee_bps, impact_bps,
            round_trip_cost_bps, round_trip_cost_usd, min_edge_bps,
        )

        return CostEstimate(
            symbol=symbol,
            notional_usd=notional_usd,
            maker_fee_bps=self.maker_fee_bps,
            taker_fee_bps=self.taker_fee_bps,
            impact_bps=impact_bps,
            round_trip_cost_bps=round_trip_cost_bps,
            round_trip_cost_usd=round_trip_cost_usd,
            is_economic=False,  # caller sets after edge comparison
            min_edge_bps=min_edge_bps,
        )

    def is_economic(
        self,
        symbol: str,
        notional_usd: float,
        edge_bps: float,
        adv_usd: float | None = None,
    ) -> tuple[bool, CostEstimate]:
        """Check whether a trade's edge covers its cost by the required ratio.

        Parameters
        ----------
        symbol : str
            Trading symbol.
        notional_usd : float
            Trade notional in USD.
        edge_bps : float
            Expected edge of the signal in basis points.
            Derived from: (2 × confidence - 1) × 10000
        adv_usd : float | None
            Average daily volume in USD.

        Returns
        -------
        tuple[bool, CostEstimate]
            (is_economic, cost_estimate)
        """
        estimate = self.estimate(symbol, notional_usd, adv_usd)
        economic = edge_bps >= estimate.min_edge_bps
        return economic, CostEstimate(
            symbol=estimate.symbol,
            notional_usd=estimate.notional_usd,
            maker_fee_bps=estimate.maker_fee_bps,
            taker_fee_bps=estimate.taker_fee_bps,
            impact_bps=estimate.impact_bps,
            round_trip_cost_bps=estimate.round_trip_cost_bps,
            round_trip_cost_usd=estimate.round_trip_cost_usd,
            is_economic=economic,
            min_edge_bps=estimate.min_edge_bps,
        )


def confidence_to_edge_bps(confidence: float, uncertainty: float | None = None) -> float:
    """Convert model confidence to expected edge in basis points.

    Edge = (2 × confidence - 1) × 10000
    Adjusted for uncertainty: edge × (1 - uncertainty)

    A 55% confident signal → 1000 bps (10%) raw edge.
    With 20% uncertainty → 800 bps effective edge.
    """
    raw_edge = max((2.0 * confidence - 1.0), 0.0)
    uncertainty_factor = 1.0 - (uncertainty or 0.0)
    return raw_edge * max(uncertainty_factor, 0.0) * 10_000.0


# Module-level singleton for convenience
_default_model: BinanceCostModel | None = None


def get_default_cost_model() -> BinanceCostModel:
    """Return the shared default cost model instance."""
    global _default_model
    if _default_model is None:
        _default_model = BinanceCostModel()
    return _default_model
