"""Tripwire test for scorecard band constants (P1-3).

This test verifies that SymbolScorecard band constants have not drifted from
the audit-known values. Changing these constants requires explicit review
and should only happen after P1-1 has been deployed for 48h with production
data showing the new universe distribution.

See P1-3 in the audit for the recommendation on post-deployment evaluation.
"""

from __future__ import annotations

from quant_v2.telebot.symbol_scorecard import SymbolScorecard


class TestScorecardBandConstants:
    """Verify band constants match audit-known values."""

    def test_scorecard_band_constants_are_stable(self) -> None:
        """Band constants should not drift from audit-known values.

        This is a tripwire test — if this fails, someone changed the band
        constants and the change must be reviewed against the P1-3 audit
        recommendation before being accepted.
        """
        # These values were established at audit time (2026-04-23)
        assert SymbolScorecard.STRONG_HIT_RATE == 0.55
        assert SymbolScorecard.WEAK_HIT_RATE == 0.45
        assert SymbolScorecard.MULT_STRONG == 1.0
        assert SymbolScorecard.MULT_NEUTRAL == 0.60
        assert SymbolScorecard.MULT_WEAK == 0.30
