"""Orderbook analysis and smart order placement."""

from dataclasses import dataclass
from typing import Optional
from enum import Enum


class OrderUrgency(Enum):
    """How aggressively to place the order."""
    PASSIVE = "passive"      # Join the bid, wait for fill
    MODERATE = "moderate"    # Improve bid slightly
    AGGRESSIVE = "aggressive"  # Cross spread, take liquidity


@dataclass
class OrderbookLevel:
    """Single price level in the orderbook."""
    price: float
    size: float


@dataclass
class OrderbookAnalysis:
    """Analysis of orderbook state."""
    best_bid: float
    best_ask: float
    spread: float
    spread_bps: float  # Spread in basis points

    bid_depth_1pct: float   # Liquidity within 1% of best bid
    ask_depth_1pct: float   # Liquidity within 1% of best ask

    bid_levels: list[OrderbookLevel]
    ask_levels: list[OrderbookLevel]

    imbalance: float  # Positive = more bids, negative = more asks

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def is_thin(self) -> bool:
        """Book is thin if spread > 5% or depth < $500."""
        return self.spread_bps > 500 or self.bid_depth_1pct < 500


@dataclass
class SmartOrderParams:
    """Parameters for a smart order."""
    price: float
    size: float
    urgency: OrderUrgency
    reason: str

    # For tracking
    expected_queue_position: int
    estimated_fill_time_seconds: Optional[float]


class OrderbookAnalyzer:
    """Analyzes orderbook and determines optimal order placement."""

    def __init__(self):
        # Price tick size on Polymarket
        self.tick_size = 0.01

        # Thresholds
        self.thin_spread_threshold = 0.05  # 5%
        self.wide_spread_threshold = 0.10  # 10%

    def analyze(self, orderbook: dict) -> OrderbookAnalysis:
        """Analyze an orderbook and return metrics."""
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        # Parse levels
        bid_levels = [
            OrderbookLevel(float(b["price"]), float(b["size"]))
            for b in bids
        ]
        ask_levels = [
            OrderbookLevel(float(a["price"]), float(a["size"]))
            for a in asks
        ]

        # Best prices
        best_bid = bid_levels[0].price if bid_levels else 0.0
        best_ask = ask_levels[0].price if ask_levels else 1.0

        spread = best_ask - best_bid
        spread_bps = (spread / best_bid * 10000) if best_bid > 0 else 0

        # Calculate depth within 1%
        bid_depth = self._calc_depth(bid_levels, best_bid, direction=-1)
        ask_depth = self._calc_depth(ask_levels, best_ask, direction=1)

        # Order imbalance
        total_bid = sum(l.size for l in bid_levels[:5])
        total_ask = sum(l.size for l in ask_levels[:5])
        imbalance = (total_bid - total_ask) / (total_bid + total_ask) if (total_bid + total_ask) > 0 else 0

        return OrderbookAnalysis(
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            spread_bps=spread_bps,
            bid_depth_1pct=bid_depth,
            ask_depth_1pct=ask_depth,
            bid_levels=bid_levels,
            ask_levels=ask_levels,
            imbalance=imbalance,
        )

    def _calc_depth(
        self,
        levels: list[OrderbookLevel],
        reference: float,
        direction: int,  # -1 for bids, +1 for asks
    ) -> float:
        """Calculate total size within 1% of reference price."""
        if not levels or reference <= 0:
            return 0.0

        threshold = reference * (1 + direction * 0.01)
        total = 0.0

        for level in levels:
            if direction == -1 and level.price >= threshold:
                total += level.size
            elif direction == 1 and level.price <= threshold:
                total += level.size

        return total

    def calculate_queue_position(
        self,
        price: float,
        size: float,
        bid_levels: list[OrderbookLevel],
    ) -> int:
        """Estimate queue position if we place at this price."""
        position = 0

        for level in bid_levels:
            if level.price > price:
                # Orders ahead of us
                position += int(level.size)
            elif level.price == price:
                # Same price level - we're at the back
                position += int(level.size)
                break
            else:
                # Our price is better
                break

        return position

    def get_optimal_order(
        self,
        analysis: OrderbookAnalysis,
        target_size: float,
        spike_magnitude: float,
        time_since_spike: float,
    ) -> SmartOrderParams:
        """
        Determine optimal order placement based on orderbook state.

        Key insight: Bigger spikes warrant more aggressive entry because
        mean reversion is more likely. But we still want to avoid
        crossing the spread if we can get filled passively.
        """

        # Determine urgency based on spike size and book state
        urgency = self._determine_urgency(
            spike_magnitude=spike_magnitude,
            time_since_spike=time_since_spike,
            analysis=analysis,
        )

        # Calculate price based on urgency
        price, reason = self._calculate_price(analysis, urgency)

        # Estimate queue position
        queue_pos = self.calculate_queue_position(
            price,
            target_size,
            analysis.bid_levels,
        )

        # Rough fill time estimate (assumes $100/min volume at this level)
        est_fill_time = (queue_pos / 100 * 60) if queue_pos > 0 else 5.0

        return SmartOrderParams(
            price=price,
            size=target_size,
            urgency=urgency,
            reason=reason,
            expected_queue_position=queue_pos,
            estimated_fill_time_seconds=est_fill_time,
        )

    def _determine_urgency(
        self,
        spike_magnitude: float,
        time_since_spike: float,
        analysis: OrderbookAnalysis,
    ) -> OrderUrgency:
        """Determine how aggressively to place the order."""

        # Massive spike (>40%) - be aggressive, this is likely to revert fast
        if spike_magnitude >= 0.40:
            return OrderUrgency.AGGRESSIVE

        # Large spike (>30%) or thin book - moderate aggression
        if spike_magnitude >= 0.30 or analysis.is_thin:
            return OrderUrgency.MODERATE

        # If book is imbalanced toward asks (sellers), we can be passive
        # because there's selling pressure that will come to us
        if analysis.imbalance < -0.2:
            return OrderUrgency.PASSIVE

        # Fresh spike (< 30 seconds) - be more aggressive
        if time_since_spike < 30:
            return OrderUrgency.MODERATE

        # Default: passive
        return OrderUrgency.PASSIVE

    def _calculate_price(
        self,
        analysis: OrderbookAnalysis,
        urgency: OrderUrgency,
    ) -> tuple[float, str]:
        """Calculate order price based on urgency."""

        if urgency == OrderUrgency.AGGRESSIVE:
            # Cross the spread - pay up to get filled immediately
            # But don't pay more than mid
            price = min(analysis.best_ask, analysis.mid_price + self.tick_size)
            return round(price, 2), "Crossing spread for immediate fill"

        elif urgency == OrderUrgency.MODERATE:
            # Improve the bid by one tick
            price = analysis.best_bid + self.tick_size
            # Don't exceed mid price
            price = min(price, analysis.mid_price)
            return round(price, 2), "Improving bid by 1 tick"

        else:  # PASSIVE
            # Join the best bid
            return analysis.best_bid, "Joining best bid"

    def should_cancel_order(
        self,
        order_price: float,
        order_age_seconds: float,
        current_analysis: OrderbookAnalysis,
        original_spike_pct: float,
    ) -> tuple[bool, str]:
        """
        Determine if an unfilled order should be cancelled.

        Cancel if:
        1. Price moved significantly against us
        2. Order is stale (> 5 min) and not near top of book
        3. Spike has fully reverted (opportunity gone)
        """

        # Price moved against us - YES continued up
        if current_analysis.best_bid > order_price + 0.05:
            return True, "Price moved up significantly, order stale"

        # Order is old and not near front of queue
        if order_age_seconds > 300:
            # Check if we're close to best bid
            if order_price < current_analysis.best_bid - 0.02:
                return True, "Order stale and far from best bid"

        # Spike has reverted - check if NO price dropped
        # (which means YES came back down, opportunity passed)
        current_no_price = 1.0 - current_analysis.mid_price  # Rough approximation
        if current_no_price > 0.60:  # NO is expensive now, spike reverted
            return True, "Spike already reverted, opportunity passed"

        return False, ""


class OrderTracker:
    """Tracks open orders and their status."""

    def __init__(self):
        self.orders: dict[str, dict] = {}  # order_id -> order info

    def add_order(
        self,
        order_id: str,
        token_id: str,
        price: float,
        size: float,
        params: SmartOrderParams,
    ):
        """Track a new order."""
        import time
        self.orders[order_id] = {
            "order_id": order_id,
            "token_id": token_id,
            "price": price,
            "size": size,
            "original_size": size,
            "filled": 0.0,
            "created_at": time.time(),
            "params": params,
            "status": "open",
        }

    def update_fill(self, order_id: str, filled_size: float):
        """Update fill amount for an order."""
        if order_id in self.orders:
            self.orders[order_id]["filled"] = filled_size
            self.orders[order_id]["size"] = (
                self.orders[order_id]["original_size"] - filled_size
            )
            if self.orders[order_id]["size"] <= 0:
                self.orders[order_id]["status"] = "filled"

    def cancel_order(self, order_id: str):
        """Mark order as cancelled."""
        if order_id in self.orders:
            self.orders[order_id]["status"] = "cancelled"

    def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        return [o for o in self.orders.values() if o["status"] == "open"]

    def get_order_age(self, order_id: str) -> float:
        """Get age of order in seconds."""
        import time
        if order_id in self.orders:
            return time.time() - self.orders[order_id]["created_at"]
        return 0.0

    def cleanup_old_orders(self, max_age_seconds: int = 3600):
        """Remove old completed/cancelled orders from tracking."""
        import time
        now = time.time()
        to_remove = []

        for order_id, order in self.orders.items():
            if order["status"] in ("filled", "cancelled"):
                if now - order["created_at"] > max_age_seconds:
                    to_remove.append(order_id)

        for order_id in to_remove:
            del self.orders[order_id]
