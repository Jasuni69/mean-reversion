"""Metrics collection and analytics for improving trade decisions."""

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from enum import Enum


class SignalOutcome(Enum):
    """What happened after we saw a signal."""
    TRADED = "traded"           # We placed a trade
    SKIPPED_LOW_CONFIDENCE = "skipped_low_confidence"
    SKIPPED_PRICE_BOUNDS = "skipped_price_bounds"
    SKIPPED_MAX_POSITIONS = "skipped_max_positions"
    SKIPPED_THIN_BOOK = "skipped_thin_book"
    MISSED = "missed"           # Signal was valid but we didn't act


class TradeOutcome(Enum):
    """Final result of a trade."""
    WIN = "win"                 # Profit
    LOSS = "loss"               # Loss
    BREAKEVEN = "breakeven"     # Within 1%
    PENDING = "pending"         # Still open
    CANCELLED = "cancelled"     # Order never filled


@dataclass
class SignalRecord:
    """Record of a detected signal."""
    timestamp: float
    market_id: str
    market_question: str

    # Signal characteristics
    yes_price_before: float
    yes_price_after: float
    spike_pct: float
    no_price_at_signal: float
    confidence: float

    # Book state at signal time
    spread_bps: float
    bid_depth: float
    ask_depth: float
    book_imbalance: float

    # What we did
    outcome: str  # SignalOutcome value
    trade_id: Optional[str] = None

    # What actually happened (filled in later)
    yes_price_5min_later: Optional[float] = None
    yes_price_15min_later: Optional[float] = None
    yes_price_1hr_later: Optional[float] = None
    did_revert: Optional[bool] = None  # Did YES come back down?


@dataclass
class TradeRecord:
    """Record of an executed trade."""
    trade_id: str
    timestamp: float
    market_id: str
    market_question: str

    # Entry
    signal_spike_pct: float
    entry_price: float
    entry_size: float
    order_urgency: str
    queue_position: int

    # Fill info
    fill_time_seconds: Optional[float] = None
    fill_price: Optional[float] = None
    partial_fill_pct: float = 0.0

    # Exit
    exit_timestamp: Optional[float] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # take_profit, stop_loss, manual, expired

    # Outcome
    pnl_dollars: Optional[float] = None
    pnl_pct: Optional[float] = None
    outcome: str = "pending"  # TradeOutcome value

    # Time in trade
    hold_time_seconds: Optional[float] = None


@dataclass
class MarketProfile:
    """Profile of a market for classification."""
    market_id: str
    question: str

    # Classification
    category: str = "unknown"  # politics, sports, crypto, entertainment, etc
    event_type: str = "unknown"  # binary_outcome, date_based, threshold, etc

    # Observed behavior
    avg_daily_volume: float = 0.0
    avg_spread_bps: float = 0.0
    volatility_score: float = 0.0  # How much it moves

    # Our history with this market
    signals_seen: int = 0
    trades_taken: int = 0
    win_rate: float = 0.0


@dataclass
class MetricsCollector:
    """Collects and persists metrics for analysis."""

    data_dir: Path = field(default_factory=lambda: Path("data"))
    signals: list[SignalRecord] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    markets: dict[str, MarketProfile] = field(default_factory=dict)

    # Session stats
    session_start: float = field(default_factory=time.time)
    signals_this_session: int = 0
    trades_this_session: int = 0

    def __post_init__(self):
        self.data_dir.mkdir(exist_ok=True)
        self._load_historical()

    def _load_historical(self):
        """Load historical data from disk."""
        signals_file = self.data_dir / "signals.jsonl"
        trades_file = self.data_dir / "trades.jsonl"
        markets_file = self.data_dir / "markets.json"

        if signals_file.exists():
            with open(signals_file) as f:
                for line in f:
                    data = json.loads(line)
                    self.signals.append(SignalRecord(**data))

        if trades_file.exists():
            with open(trades_file) as f:
                for line in f:
                    data = json.loads(line)
                    self.trades.append(TradeRecord(**data))

        if markets_file.exists():
            with open(markets_file) as f:
                data = json.load(f)
                for k, v in data.items():
                    self.markets[k] = MarketProfile(**v)

        print(f"Loaded {len(self.signals)} signals, {len(self.trades)} trades, {len(self.markets)} markets")

    def _save_signal(self, signal: SignalRecord):
        """Append signal to disk."""
        with open(self.data_dir / "signals.jsonl", "a") as f:
            f.write(json.dumps(asdict(signal)) + "\n")

    def _save_trade(self, trade: TradeRecord):
        """Append trade to disk."""
        with open(self.data_dir / "trades.jsonl", "a") as f:
            f.write(json.dumps(asdict(trade)) + "\n")

    def _save_markets(self):
        """Save market profiles."""
        with open(self.data_dir / "markets.json", "w") as f:
            json.dump({k: asdict(v) for k, v in self.markets.items()}, f, indent=2)

    def record_signal(
        self,
        market_id: str,
        market_question: str,
        yes_price_before: float,
        yes_price_after: float,
        spike_pct: float,
        no_price: float,
        confidence: float,
        spread_bps: float,
        bid_depth: float,
        ask_depth: float,
        book_imbalance: float,
        outcome: SignalOutcome,
        trade_id: Optional[str] = None,
    ) -> SignalRecord:
        """Record a signal we detected."""
        record = SignalRecord(
            timestamp=time.time(),
            market_id=market_id,
            market_question=market_question,
            yes_price_before=yes_price_before,
            yes_price_after=yes_price_after,
            spike_pct=spike_pct,
            no_price_at_signal=no_price,
            confidence=confidence,
            spread_bps=spread_bps,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            book_imbalance=book_imbalance,
            outcome=outcome.value,
            trade_id=trade_id,
        )

        self.signals.append(record)
        self.signals_this_session += 1
        self._save_signal(record)

        # Update market profile
        self._update_market_profile(market_id, market_question, signal=True)

        return record

    def record_trade_entry(
        self,
        trade_id: str,
        market_id: str,
        market_question: str,
        signal_spike_pct: float,
        entry_price: float,
        entry_size: float,
        order_urgency: str,
        queue_position: int,
    ) -> TradeRecord:
        """Record a trade entry."""
        record = TradeRecord(
            trade_id=trade_id,
            timestamp=time.time(),
            market_id=market_id,
            market_question=market_question,
            signal_spike_pct=signal_spike_pct,
            entry_price=entry_price,
            entry_size=entry_size,
            order_urgency=order_urgency,
            queue_position=queue_position,
        )

        self.trades.append(record)
        self.trades_this_session += 1
        self._save_trade(record)

        # Update market profile
        self._update_market_profile(market_id, market_question, trade=True)

        return record

    def record_trade_fill(
        self,
        trade_id: str,
        fill_price: float,
        fill_time_seconds: float,
        partial_fill_pct: float = 1.0,
    ):
        """Record that a trade was filled."""
        for trade in reversed(self.trades):
            if trade.trade_id == trade_id:
                trade.fill_price = fill_price
                trade.fill_time_seconds = fill_time_seconds
                trade.partial_fill_pct = partial_fill_pct
                break

    def record_trade_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
    ):
        """Record a trade exit and calculate PnL."""
        for trade in reversed(self.trades):
            if trade.trade_id == trade_id:
                trade.exit_timestamp = time.time()
                trade.exit_price = exit_price
                trade.exit_reason = exit_reason
                trade.hold_time_seconds = trade.exit_timestamp - trade.timestamp

                # Calculate PnL
                if trade.fill_price and trade.fill_price > 0:
                    trade.pnl_pct = (exit_price - trade.fill_price) / trade.fill_price
                    trade.pnl_dollars = trade.pnl_pct * trade.entry_size

                    # Determine outcome
                    if trade.pnl_pct > 0.01:
                        trade.outcome = TradeOutcome.WIN.value
                    elif trade.pnl_pct < -0.01:
                        trade.outcome = TradeOutcome.LOSS.value
                    else:
                        trade.outcome = TradeOutcome.BREAKEVEN.value

                    # Update market win rate
                    self._update_market_win_rate(trade.market_id, trade.outcome)

                break

    def update_signal_outcome(
        self,
        market_id: str,
        signal_timestamp: float,
        yes_price_now: float,
        minutes_elapsed: int,
    ):
        """Update signal with what actually happened later."""
        for signal in reversed(self.signals):
            if signal.market_id == market_id and abs(signal.timestamp - signal_timestamp) < 60:
                if minutes_elapsed <= 5:
                    signal.yes_price_5min_later = yes_price_now
                elif minutes_elapsed <= 15:
                    signal.yes_price_15min_later = yes_price_now
                else:
                    signal.yes_price_1hr_later = yes_price_now

                # Check if it reverted
                if signal.yes_price_after > signal.yes_price_before:
                    # YES spiked up, reversion = price came back down
                    signal.did_revert = yes_price_now < signal.yes_price_after - 0.05

                break

    def _update_market_profile(
        self,
        market_id: str,
        question: str,
        signal: bool = False,
        trade: bool = False,
    ):
        """Update or create market profile."""
        if market_id not in self.markets:
            self.markets[market_id] = MarketProfile(
                market_id=market_id,
                question=question,
                category=self._classify_market(question),
            )

        profile = self.markets[market_id]
        if signal:
            profile.signals_seen += 1
        if trade:
            profile.trades_taken += 1

        self._save_markets()

    def _update_market_win_rate(self, market_id: str, outcome: str):
        """Update win rate for a market."""
        if market_id in self.markets:
            profile = self.markets[market_id]

            # Calculate from all trades in this market
            market_trades = [t for t in self.trades if t.market_id == market_id and t.outcome != "pending"]
            if market_trades:
                wins = sum(1 for t in market_trades if t.outcome == TradeOutcome.WIN.value)
                profile.win_rate = wins / len(market_trades)

            self._save_markets()

    def _classify_market(self, question: str) -> str:
        """Simple keyword-based market classification."""
        q = question.lower()

        if any(w in q for w in ["trump", "biden", "election", "president", "congress", "vote"]):
            return "politics"
        elif any(w in q for w in ["bitcoin", "ethereum", "btc", "eth", "crypto", "price"]):
            return "crypto"
        elif any(w in q for w in ["nfl", "nba", "mlb", "game", "win", "championship", "super bowl"]):
            return "sports"
        elif any(w in q for w in ["movie", "oscar", "grammy", "album", "celebrity"]):
            return "entertainment"
        elif any(w in q for w in ["fed", "rate", "inflation", "gdp", "economy"]):
            return "economics"
        else:
            return "other"

    def get_analytics(self) -> dict:
        """Calculate analytics from collected data."""
        now = time.time()

        # Filter to recent trades (last 30 days)
        recent_trades = [t for t in self.trades if now - t.timestamp < 30 * 24 * 3600]
        closed_trades = [t for t in recent_trades if t.outcome != "pending"]

        # Win rate
        if closed_trades:
            wins = sum(1 for t in closed_trades if t.outcome == TradeOutcome.WIN.value)
            win_rate = wins / len(closed_trades)
        else:
            win_rate = 0.0

        # PnL
        total_pnl = sum(t.pnl_dollars or 0 for t in closed_trades)
        avg_win = 0.0
        avg_loss = 0.0

        winning_trades = [t for t in closed_trades if t.outcome == TradeOutcome.WIN.value]
        losing_trades = [t for t in closed_trades if t.outcome == TradeOutcome.LOSS.value]

        if winning_trades:
            avg_win = sum(t.pnl_dollars or 0 for t in winning_trades) / len(winning_trades)
        if losing_trades:
            avg_loss = sum(t.pnl_dollars or 0 for t in losing_trades) / len(losing_trades)

        # Signal quality
        recent_signals = [s for s in self.signals if now - s.timestamp < 30 * 24 * 3600]
        signals_that_reverted = [s for s in recent_signals if s.did_revert is True]
        reversion_rate = len(signals_that_reverted) / len(recent_signals) if recent_signals else 0.0

        # Fill analysis
        filled_trades = [t for t in recent_trades if t.fill_time_seconds is not None]
        avg_fill_time = sum(t.fill_time_seconds for t in filled_trades) / len(filled_trades) if filled_trades else 0.0

        # By urgency
        by_urgency = {}
        for urgency in ["passive", "moderate", "aggressive"]:
            urgency_trades = [t for t in closed_trades if t.order_urgency == urgency]
            if urgency_trades:
                urgency_wins = sum(1 for t in urgency_trades if t.outcome == TradeOutcome.WIN.value)
                by_urgency[urgency] = {
                    "count": len(urgency_trades),
                    "win_rate": urgency_wins / len(urgency_trades),
                }

        # By spike size
        by_spike = {"small": [], "medium": [], "large": []}
        for t in closed_trades:
            if t.signal_spike_pct < 0.25:
                by_spike["small"].append(t)
            elif t.signal_spike_pct < 0.35:
                by_spike["medium"].append(t)
            else:
                by_spike["large"].append(t)

        spike_analysis = {}
        for size, trades in by_spike.items():
            if trades:
                wins = sum(1 for t in trades if t.outcome == TradeOutcome.WIN.value)
                spike_analysis[size] = {
                    "count": len(trades),
                    "win_rate": wins / len(trades),
                    "avg_pnl": sum(t.pnl_dollars or 0 for t in trades) / len(trades),
                }

        # By category
        by_category = {}
        for category in set(m.category for m in self.markets.values()):
            cat_markets = [m.market_id for m in self.markets.values() if m.category == category]
            cat_trades = [t for t in closed_trades if t.market_id in cat_markets]
            if cat_trades:
                wins = sum(1 for t in cat_trades if t.outcome == TradeOutcome.WIN.value)
                by_category[category] = {
                    "count": len(cat_trades),
                    "win_rate": wins / len(cat_trades),
                }

        return {
            "period": "30d",
            "signals_total": len(recent_signals),
            "signals_traded": sum(1 for s in recent_signals if s.outcome == SignalOutcome.TRADED.value),
            "reversion_rate": reversion_rate,
            "trades_total": len(recent_trades),
            "trades_closed": len(closed_trades),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "avg_fill_time_seconds": avg_fill_time,
            "by_urgency": by_urgency,
            "by_spike_size": spike_analysis,
            "by_category": by_category,
        }

    def print_analytics(self):
        """Print formatted analytics."""
        analytics = self.get_analytics()

        print("\n" + "=" * 60)
        print("ANALYTICS (Last 30 Days)")
        print("=" * 60)

        print(f"\nSignals:")
        print(f"  Total detected: {analytics['signals_total']}")
        print(f"  Traded: {analytics['signals_traded']}")
        print(f"  Reversion rate: {analytics['reversion_rate']:.1%}")

        print(f"\nTrades:")
        print(f"  Total: {analytics['trades_total']}")
        print(f"  Closed: {analytics['trades_closed']}")
        print(f"  Win rate: {analytics['win_rate']:.1%}")
        print(f"  Total PnL: ${analytics['total_pnl']:.2f}")
        print(f"  Avg win: ${analytics['avg_win']:.2f}")
        print(f"  Avg loss: ${analytics['avg_loss']:.2f}")
        print(f"  Avg fill time: {analytics['avg_fill_time_seconds']:.1f}s")

        if analytics['by_urgency']:
            print(f"\nBy Urgency:")
            for urgency, stats in analytics['by_urgency'].items():
                print(f"  {urgency}: {stats['count']} trades, {stats['win_rate']:.1%} win rate")

        if analytics['by_spike_size']:
            print(f"\nBy Spike Size:")
            for size, stats in analytics['by_spike_size'].items():
                print(f"  {size} (n={stats['count']}): {stats['win_rate']:.1%} win rate, ${stats['avg_pnl']:.2f} avg")

        if analytics['by_category']:
            print(f"\nBy Category:")
            for cat, stats in analytics['by_category'].items():
                print(f"  {cat}: {stats['count']} trades, {stats['win_rate']:.1%} win rate")

        print("=" * 60 + "\n")

    def get_parameter_suggestions(self) -> dict:
        """Suggest parameter adjustments based on data."""
        analytics = self.get_analytics()
        suggestions = {}

        # Check if we should adjust spike threshold
        by_spike = analytics.get('by_spike_size', {})
        if by_spike:
            small_wr = by_spike.get('small', {}).get('win_rate', 0)
            large_wr = by_spike.get('large', {}).get('win_rate', 0)

            if small_wr < 0.4 and large_wr > 0.6:
                suggestions['min_spike_threshold'] = {
                    'current': 0.20,
                    'suggested': 0.25,
                    'reason': f"Small spikes win rate ({small_wr:.0%}) much lower than large ({large_wr:.0%})"
                }

        # Check urgency performance
        by_urgency = analytics.get('by_urgency', {})
        if by_urgency:
            passive_wr = by_urgency.get('passive', {}).get('win_rate', 0)
            aggressive_wr = by_urgency.get('aggressive', {}).get('win_rate', 0)

            if aggressive_wr > passive_wr + 0.15:
                suggestions['default_urgency'] = {
                    'current': 'passive',
                    'suggested': 'moderate',
                    'reason': f"Aggressive orders win more ({aggressive_wr:.0%} vs {passive_wr:.0%})"
                }

        # Check category performance
        by_category = analytics.get('by_category', {})
        bad_categories = [cat for cat, stats in by_category.items() if stats['win_rate'] < 0.35]
        if bad_categories:
            suggestions['avoid_categories'] = {
                'categories': bad_categories,
                'reason': f"Low win rate in: {', '.join(bad_categories)}"
            }

        return suggestions
