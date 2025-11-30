"""Detects price spikes in YES outcomes that present NO betting opportunities."""

import time
from dataclasses import dataclass
from typing import Optional

from config import config
from polymarket_client import Market, PolymarketClient


@dataclass
class SpikeSignal:
    """Represents a detected price spike opportunity."""

    market: Market
    token_id_no: str
    yes_price_before: float
    yes_price_after: float
    spike_pct: float
    no_price: float
    timestamp: float
    confidence: float  # 0-1 score based on signal quality


class SpikeDetector:
    """Detects sudden YES price spikes that may revert."""

    def __init__(self, client: PolymarketClient):
        self.client = client
        self._baseline_prices: dict[str, float] = {}
        self._last_spike_time: dict[str, float] = {}
        self._cooldown_seconds = 300  # 5 min cooldown per market

    async def update_baselines(self, markets: list[Market]):
        """Update baseline prices for markets we're tracking."""
        for market in markets:
            token_id = market.token_id_yes
            current_price = await self.client.get_price(token_id)

            # Only update baseline if price is "normal" (not already spiked)
            if token_id not in self._baseline_prices:
                self._baseline_prices[token_id] = current_price
            else:
                # Slowly adjust baseline using EMA
                alpha = 0.1
                old_baseline = self._baseline_prices[token_id]
                self._baseline_prices[token_id] = (
                    alpha * current_price + (1 - alpha) * old_baseline
                )

            # Record for history tracking
            self.client.record_price(token_id, current_price)

    async def detect_spike(self, market: Market) -> Optional[SpikeSignal]:
        """Check if a market has a YES spike worth betting against."""
        token_id = market.token_id_yes
        now = time.time()

        # Check cooldown
        last_spike = self._last_spike_time.get(token_id, 0)
        if now - last_spike < self._cooldown_seconds:
            return None

        # Get current YES price
        current_yes_price = await self.client.get_price(token_id)

        # Get baseline (or use stored price from earlier)
        baseline = self._baseline_prices.get(token_id)
        if baseline is None:
            return None

        # Also check recent price change from our history
        recent_change = self.client.get_price_change(token_id)

        # Calculate spike percentage
        if baseline > 0:
            spike_from_baseline = (current_yes_price - baseline) / baseline
        else:
            spike_from_baseline = 0

        # Use the larger of baseline spike or recent change
        spike_pct = max(
            spike_from_baseline, recent_change if recent_change else 0
        )

        # Check if spike exceeds threshold
        if spike_pct < config.min_spike_threshold:
            return None

        # Get current NO price
        no_price = await self.client.get_price(market.token_id_no)

        # Calculate confidence based on signal quality
        confidence = self._calculate_confidence(
            spike_pct=spike_pct,
            liquidity=market.liquidity,
            no_price=no_price,
        )

        # Mark spike time for cooldown
        self._last_spike_time[token_id] = now

        return SpikeSignal(
            market=market,
            token_id_no=market.token_id_no,
            yes_price_before=baseline,
            yes_price_after=current_yes_price,
            spike_pct=spike_pct,
            no_price=no_price,
            timestamp=now,
            confidence=confidence,
        )

    def _calculate_confidence(
        self,
        spike_pct: float,
        liquidity: float,
        no_price: float,
    ) -> float:
        """Calculate confidence score for the signal."""
        score = 0.0

        # Higher spike = higher confidence (up to a point)
        if spike_pct >= 0.30:
            score += 0.4
        elif spike_pct >= 0.20:
            score += 0.3
        else:
            score += 0.2

        # Better liquidity = higher confidence
        if liquidity >= 10000:
            score += 0.3
        elif liquidity >= 5000:
            score += 0.2
        elif liquidity >= config.min_liquidity:
            score += 0.1

        # NO price attractiveness (cheaper NO = better risk/reward)
        if no_price <= 0.30:
            score += 0.3
        elif no_price <= 0.50:
            score += 0.2
        else:
            score += 0.1

        return min(score, 1.0)

    async def scan_markets(self, markets: list[Market]) -> list[SpikeSignal]:
        """Scan all markets for spike opportunities."""
        signals = []

        # Filter for markets with sufficient liquidity
        tradeable = [m for m in markets if m.liquidity >= config.min_liquidity]

        for market in tradeable:
            signal = await self.detect_spike(market)
            if signal:
                signals.append(signal)

        # Sort by confidence
        signals.sort(key=lambda s: s.confidence, reverse=True)

        return signals
