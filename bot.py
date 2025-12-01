"""Main bot - NO Mean Reversion Trading Bot for Polymarket."""

import asyncio
import signal
import sys
import time
from datetime import datetime

from config import config
from polymarket_client import PolymarketClient
from spike_detector import SpikeDetector
from strategy import MeanReversionStrategy
from position_manager import PositionManager
from orderbook import OrderbookAnalyzer, OrderTracker
from metrics import MetricsCollector, SignalOutcome


class MeanReversionBot:
    """
    Bot that bets NO when YES prices spike.

    Strategy: ~80% of markets resolve NO. When someone sweeps YES
    and pushes price up >20%, we take the other side and buy NO,
    betting on mean reversion.
    """

    def __init__(self):
        self.client = PolymarketClient()
        self.detector = SpikeDetector(self.client)
        self.strategy = MeanReversionStrategy()
        self.positions = PositionManager(client=self.client)
        self.orderbook_analyzer = OrderbookAnalyzer()
        self.order_tracker = OrderTracker()
        self.metrics = MetricsCollector()
        self.running = False
        self.scan_interval = 30  # seconds between scans

        # Track signals for follow-up price checks
        self._pending_signal_checks: list[dict] = []

    async def start(self):
        """Start the bot."""
        print("\n" + "=" * 60)
        print("NO Mean Reversion Bot")
        print("=" * 60)
        print(f"Strategy: Buy NO when YES spikes >{config.min_spike_threshold:.0%}")
        print(f"Max position: ${config.max_position_size}")
        print(f"Max positions: {config.max_open_positions}")
        print(f"Take profit: {config.take_profit_pct:.0%}")
        print(f"Stop loss: {config.stop_loss_pct:.0%}")
        print("=" * 60)

        # Show historical analytics
        self.metrics.print_analytics()

        # Show parameter suggestions
        suggestions = self.metrics.get_parameter_suggestions()
        if suggestions:
            print("\nParameter Suggestions (based on historical data):")
            for param, info in suggestions.items():
                print(f"  {param}: {info.get('reason', '')}")
            print()

        await self.client.connect()
        self.running = True

        # Set up graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        await self.run_loop()

    async def stop(self):
        """Stop the bot gracefully."""
        print("\nShutting down...")
        self.metrics.print_analytics()
        self.running = False
        await self.client.close()

    async def run_loop(self):
        """Main bot loop."""
        iteration = 0
        last_analytics_print = time.time()

        while self.running:
            try:
                iteration += 1
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"\n[{timestamp}] Scan #{iteration}")

                # 1. Fetch active markets
                markets = await self.client.get_active_markets()
                print(f"  Monitoring {len(markets)} markets")

                # 2. Update baseline prices
                await self.detector.update_baselines(markets)

                # 3. Check and cancel stale orders
                await self._manage_open_orders()

                # 4. Update pending signal checks (see if spikes reverted)
                await self._check_signal_outcomes()

                # 5. Scan for spike opportunities
                signals = await self.detector.scan_markets(markets)

                if signals:
                    print(f"  Found {len(signals)} spike signals!")

                    for sig in signals:
                        # Get NO orderbook for analysis
                        no_orderbook = await self.client.get_orderbook(sig.token_id_no)
                        analysis = self.orderbook_analyzer.analyze(no_orderbook)

                        # Determine outcome before we evaluate
                        outcome = SignalOutcome.TRADED
                        trade_id = None

                        # Skip if we can't open more positions
                        if not self.positions.can_open_position(sig.token_id_no):
                            outcome = SignalOutcome.SKIPPED_MAX_POSITIONS
                        elif analysis.is_thin:
                            # May still trade but note it
                            pass

                        # Evaluate signal with orderbook data
                        decision = self.strategy.evaluate(sig, no_orderbook)

                        if decision and decision.size > 0 and outcome == SignalOutcome.TRADED:
                            print(f"\n  TRADE: {decision.reason}")
                            print(f"    Market: {sig.market.question[:50]}...")

                            urgency_str = "unknown"
                            queue_pos = 0
                            if decision.order_params:
                                urgency_str = decision.order_params.urgency.value
                                queue_pos = decision.order_params.expected_queue_position
                                print(f"    Urgency: {urgency_str}")
                                print(f"    Queue position: ~{queue_pos}")

                            # Place order
                            order_id = await self.client.place_order(
                                token_id=decision.token_id,
                                side=decision.side,
                                size=decision.size,
                                price=decision.limit_price,
                            )

                            if order_id:
                                trade_id = order_id

                                # Track the order
                                self.order_tracker.add_order(
                                    order_id=order_id,
                                    token_id=decision.token_id,
                                    price=decision.limit_price,
                                    size=decision.size,
                                    params=decision.order_params,
                                )

                                self.positions.add_position(
                                    token_id=decision.token_id,
                                    market_question=sig.market.question,
                                    entry_price=decision.limit_price,
                                    size=decision.size,
                                    order_id=order_id,
                                )

                                # Record trade entry
                                self.metrics.record_trade_entry(
                                    trade_id=order_id,
                                    market_id=sig.market.condition_id,
                                    market_question=sig.market.question,
                                    signal_spike_pct=sig.spike_pct,
                                    entry_price=decision.limit_price,
                                    entry_size=decision.size,
                                    order_urgency=urgency_str,
                                    queue_position=queue_pos,
                                )
                            else:
                                outcome = SignalOutcome.MISSED
                        elif decision:
                            # Decision made but size is 0
                            if "too low" in decision.reason:
                                outcome = SignalOutcome.SKIPPED_PRICE_BOUNDS
                            elif "too high" in decision.reason:
                                outcome = SignalOutcome.SKIPPED_PRICE_BOUNDS
                            else:
                                outcome = SignalOutcome.SKIPPED_LOW_CONFIDENCE
                            print(f"  Skip: {decision.reason}")
                        else:
                            outcome = SignalOutcome.SKIPPED_LOW_CONFIDENCE

                        # Record the signal
                        self.metrics.record_signal(
                            market_id=sig.market.condition_id,
                            market_question=sig.market.question,
                            yes_price_before=sig.yes_price_before,
                            yes_price_after=sig.yes_price_after,
                            spike_pct=sig.spike_pct,
                            no_price=sig.no_price,
                            confidence=sig.confidence,
                            spread_bps=analysis.spread_bps,
                            bid_depth=analysis.bid_depth_1pct,
                            ask_depth=analysis.ask_depth_1pct,
                            book_imbalance=analysis.imbalance,
                            outcome=outcome,
                            trade_id=trade_id,
                        )

                        # Queue for follow-up checks
                        self._pending_signal_checks.append({
                            "market_id": sig.market.condition_id,
                            "token_id": sig.market.token_id_yes,
                            "timestamp": sig.timestamp,
                            "checks_remaining": [5, 15, 60],  # minutes
                        })

                # 6. Update and check existing positions
                await self.positions.update_positions()
                exits = await self.positions.check_exits()

                for token_id in exits:
                    pos = self.positions.positions.get(token_id)
                    if pos:
                        # Record the exit
                        self.metrics.record_trade_exit(
                            trade_id=pos.order_id,
                            exit_price=pos.current_price,
                            exit_reason="take_profit" if pos.pnl_pct > 0 else "stop_loss",
                        )
                    self.positions.close_position(token_id)

                # 7. Print status
                self.positions.print_status()
                self._print_order_status()

                # Cleanup old tracked orders
                self.order_tracker.cleanup_old_orders()

                # Print analytics every 30 minutes
                if time.time() - last_analytics_print > 1800:
                    self.metrics.print_analytics()
                    last_analytics_print = time.time()

                # Wait before next scan
                await asyncio.sleep(self.scan_interval)

            except Exception as e:
                print(f"Error in main loop: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(10)

    async def _check_signal_outcomes(self):
        """Check what happened to past signals (did they revert?)."""
        now = time.time()
        still_pending = []

        for check in self._pending_signal_checks:
            if not check["checks_remaining"]:
                continue

            # Get current YES price
            try:
                yes_price = await self.client.get_price(check["token_id"])
            except Exception:
                still_pending.append(check)
                continue

            elapsed_minutes = (now - check["timestamp"]) / 60

            # Check if we've passed any check thresholds
            new_remaining = []
            for check_min in check["checks_remaining"]:
                if elapsed_minutes >= check_min:
                    # Update the signal record
                    self.metrics.update_signal_outcome(
                        market_id=check["market_id"],
                        signal_timestamp=check["timestamp"],
                        yes_price_now=yes_price,
                        minutes_elapsed=check_min,
                    )
                else:
                    new_remaining.append(check_min)

            check["checks_remaining"] = new_remaining

            if new_remaining:
                still_pending.append(check)

        self._pending_signal_checks = still_pending

    async def _manage_open_orders(self):
        """Check open orders and cancel stale ones."""
        open_orders = self.order_tracker.get_open_orders()

        for order in open_orders:
            order_id = order["order_id"]
            token_id = order["token_id"]
            order_price = order["price"]
            order_age = self.order_tracker.get_order_age(order_id)

            # Get current orderbook
            orderbook = await self.client.get_orderbook(token_id)
            analysis = self.orderbook_analyzer.analyze(orderbook)

            # Get original spike info if available
            original_spike = 0.20  # Default assumption
            if order.get("params"):
                # Could store this, for now use default
                pass

            # Check if we should cancel
            should_cancel, reason = self.orderbook_analyzer.should_cancel_order(
                order_price=order_price,
                order_age_seconds=order_age,
                current_analysis=analysis,
                original_spike_pct=original_spike,
            )

            if should_cancel:
                print(f"  Cancelling order {order_id[:8]}...: {reason}")
                success = await self.client.cancel_order(order_id)
                if success:
                    self.order_tracker.cancel_order(order_id)

    def _print_order_status(self):
        """Print status of open orders."""
        open_orders = self.order_tracker.get_open_orders()

        if not open_orders:
            return

        print(f"\nOpen Orders ({len(open_orders)}):")
        for order in open_orders:
            age = self.order_tracker.get_order_age(order["order_id"])
            print(
                f"  {order['order_id'][:8]}... | "
                f"${order['size']:.2f} @ {order['price']:.2f} | "
                f"Age: {age:.0f}s"
            )


async def main():
    """Entry point."""
    if not config.validate():
        print("ERROR: Missing API credentials!")
        print("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)

    bot = MeanReversionBot()
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
