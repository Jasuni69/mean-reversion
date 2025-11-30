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

                # 3. Scan for spike opportunities
                signals = await self.detector.scan_markets(markets)

                if signals:
                    print(f"  Found {len(signals)} spike signals!")

                    for sig in signals:
                        # Skip if we can't open more positions
                        if not self.positions.can_open_position(sig.token_id_no):
                            continue

                        # Evaluate signal
                        decision = self.strategy.evaluate(sig)

                        if decision and decision.size > 0:
                            print(f"\n  TRADE: {decision.reason}")
                            print(f"    Market: {sig.market.question[:50]}...")

                            # Place order
                            order_id = await self.client.place_order(
                                token_id=decision.token_id,
                                side=decision.side,
                                size=decision.size,
                                price=decision.limit_price,
                            )

                            if order_id:
                                self.positions.add_position(
                                    token_id=decision.token_id,
                                    market_question=sig.market.question,
                                    entry_price=decision.limit_price,
                                    size=decision.size,
                                    order_id=order_id,
                                )
                        elif decision:
                            print(f"  Skip: {decision.reason}")

                # 4. Update and check existing positions
                await self.positions.update_positions()
                exits = await self.positions.check_exits()

                for token_id in exits:
                    # In production, you'd place a sell order here
                    self.positions.close_position(token_id)

                # 5. Print status
                self.positions.print_status()

                # Wait before next scan
                await asyncio.sleep(self.scan_interval)

            except Exception as e:
                print(f"Error in main loop: {e}")
                await asyncio.sleep(10)


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
