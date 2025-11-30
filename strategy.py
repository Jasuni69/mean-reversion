"""Mean reversion strategy - bet NO when YES spikes."""

from dataclasses import dataclass
from typing import Optional

from config import config
from spike_detector import SpikeSignal


@dataclass
class TradeDecision:
    """Decision to place a trade."""

    signal: SpikeSignal
    token_id: str
    side: str  # Always "buy" for NO tokens
    size: float
    limit_price: float
    reason: str


class MeanReversionStrategy:
    """
    Core strategy: When YES spikes >20%, bet on NO.

    Rationale: ~80% of Polymarket markets resolve to NO.
    Sudden YES pumps often revert as they're usually speculative.
    """

    def __init__(self):
        self.min_confidence = 0.5
        self.min_no_price = 0.10  # Don't buy NO if it's too cheap (already priced in)
        self.max_no_price = 0.70  # Don't buy NO if YES hasn't actually spiked much

    def evaluate(self, signal: SpikeSignal) -> Optional[TradeDecision]:
        """Evaluate a spike signal and decide whether to trade."""

        # Check confidence threshold
        if signal.confidence < self.min_confidence:
            return None

        # Check NO price bounds
        if signal.no_price < self.min_no_price:
            return TradeDecision(
                signal=signal,
                token_id=signal.token_id_no,
                side="buy",
                size=0,
                limit_price=0,
                reason=f"NO price too low ({signal.no_price:.2f}), likely already priced in",
            )

        if signal.no_price > self.max_no_price:
            return TradeDecision(
                signal=signal,
                token_id=signal.token_id_no,
                side="buy",
                size=0,
                limit_price=0,
                reason=f"NO price too high ({signal.no_price:.2f}), YES hasn't spiked enough",
            )

        # Calculate position size based on confidence and max position
        size = self._calculate_size(signal)

        # Set limit price slightly above current NO price to ensure fill
        limit_price = min(signal.no_price + 0.02, 0.95)

        return TradeDecision(
            signal=signal,
            token_id=signal.token_id_no,
            side="buy",
            size=size,
            limit_price=limit_price,
            reason=f"YES spiked {signal.spike_pct:.1%}, buying NO at {limit_price:.2f}",
        )

    def _calculate_size(self, signal: SpikeSignal) -> float:
        """Calculate position size based on signal strength."""
        base_size = config.max_position_size

        # Scale by confidence
        size = base_size * signal.confidence

        # Scale by spike magnitude (bigger spike = more confident in reversion)
        if signal.spike_pct >= 0.30:
            size *= 1.0
        elif signal.spike_pct >= 0.25:
            size *= 0.8
        else:
            size *= 0.6

        # Ensure minimum viable size
        return max(size, 10.0)  # At least $10
