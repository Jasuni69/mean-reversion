"""Mean reversion strategy - bet NO when YES spikes."""

from dataclasses import dataclass
from typing import Optional

from config import config
from spike_detector import SpikeSignal
from orderbook import OrderbookAnalysis, OrderbookAnalyzer, SmartOrderParams


@dataclass
class TradeDecision:
    """Decision to place a trade."""

    signal: SpikeSignal
    token_id: str
    side: str  # Always "buy" for NO tokens
    size: float
    limit_price: float
    reason: str
    order_params: Optional[SmartOrderParams] = None


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
        self.orderbook_analyzer = OrderbookAnalyzer()

    def evaluate(
        self,
        signal: SpikeSignal,
        no_orderbook: Optional[dict] = None,
    ) -> Optional[TradeDecision]:
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

        # Calculate base position size
        size = self._calculate_size(signal)

        # If we have orderbook data, use smart order placement
        if no_orderbook:
            order_params = self._get_smart_order_params(
                signal=signal,
                orderbook=no_orderbook,
                target_size=size,
            )

            if order_params:
                return TradeDecision(
                    signal=signal,
                    token_id=signal.token_id_no,
                    side="buy",
                    size=order_params.size,
                    limit_price=order_params.price,
                    reason=f"YES spiked {signal.spike_pct:.1%} | {order_params.reason}",
                    order_params=order_params,
                )

        # Fallback: simple order placement
        limit_price = min(signal.no_price + 0.02, 0.95)

        return TradeDecision(
            signal=signal,
            token_id=signal.token_id_no,
            side="buy",
            size=size,
            limit_price=limit_price,
            reason=f"YES spiked {signal.spike_pct:.1%}, buying NO at {limit_price:.2f}",
        )

    def _get_smart_order_params(
        self,
        signal: SpikeSignal,
        orderbook: dict,
        target_size: float,
    ) -> Optional[SmartOrderParams]:
        """Use orderbook analysis to determine optimal order placement."""
        import time

        try:
            analysis = self.orderbook_analyzer.analyze(orderbook)

            # Time since spike was detected
            time_since_spike = time.time() - signal.timestamp

            # Get optimal order parameters
            params = self.orderbook_analyzer.get_optimal_order(
                analysis=analysis,
                target_size=target_size,
                spike_magnitude=signal.spike_pct,
                time_since_spike=time_since_spike,
            )

            # Adjust size if book is thin
            if analysis.is_thin:
                # Reduce size on thin books to avoid impact
                params.size = min(params.size, analysis.bid_depth_1pct * 0.3)
                params.size = max(params.size, 10.0)  # Still at least $10

            return params

        except Exception as e:
            print(f"Error in smart order placement: {e}")
            return None

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
