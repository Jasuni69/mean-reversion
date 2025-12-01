"""Main bot - NO Mean Reversion Trading Bot for Polymarket."""

import asyncio
import signal
import sys
from datetime import datetime

from config import config
from polymarket_client import PolymarketClient
from spike_detector import SpikeDetector
from strategy import MeanReversionStrategy
from position_manager import PositionManager
from orderbook import OrderbookAnalyzer, OrderTracker


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
        self.running = False
        self.scan_interval = 30  # seconds between scans

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
        print("=" * 60 + "\n")

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
        self.running = False
        await self.client.close()

    async def run_loop(self):
        """Main bot loop."""
        iteration = 0

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

                # 4. Scan for spike opportunities
                signals = await self.detector.scan_markets(markets)

                if signals:
                    print(f"  Found {len(signals)} spike signals!")

                    for sig in signals:
                        # Skip if we can't open more positions
                        if not self.positions.can_open_position(sig.token_id_no):
                            continue

                        # Get NO orderbook for smart order placement
                        no_orderbook = await self.client.get_orderbook(sig.token_id_no)

                        # Evaluate signal with orderbook data
                        decision = self.strategy.evaluate(sig, no_orderbook)

                        if decision and decision.size > 0:
                            print(f"\n  TRADE: {decision.reason}")
                            print(f"    Market: {sig.market.question[:50]}...")

                            if decision.order_params:
                                print(f"    Urgency: {decision.order_params.urgency.value}")
                                print(f"    Queue position: ~{decision.order_params.expected_queue_position}")

                            # Place order
                            order_id = await self.client.place_order(
                                token_id=decision.token_id,
                                side=decision.side,
                                size=decision.size,
                                price=decision.limit_price,
                            )

                            if order_id:
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
                        elif decision:
                            print(f"  Skip: {decision.reason}")

                # 5. Update and check existing positions
                await self.positions.update_positions()
                exits = await self.positions.check_exits()

                for token_id in exits:
                    # In production, you'd place a sell order here
                    self.positions.close_position(token_id)

                # 6. Print status
                self.positions.print_status()
                self._print_order_status()

                # Cleanup old tracked orders
                self.order_tracker.cleanup_old_orders()

                # Wait before next scan
                await asyncio.sleep(self.scan_interval)

            except Exception as e:
                print(f"Error in main loop: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(10)

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
