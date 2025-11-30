"""Position and risk management."""

import time
from dataclasses import dataclass, field
from typing import Optional

from config import config
from polymarket_client import PolymarketClient


@dataclass
class Position:
    """Tracks an open position."""

    token_id: str
    market_question: str
    entry_price: float
    size: float
    entry_time: float
    order_id: Optional[str] = None
    current_price: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class PositionManager:
    """Manages open positions and risk."""

    client: PolymarketClient
    positions: dict[str, Position] = field(default_factory=dict)

    def can_open_position(self, token_id: str) -> bool:
        """Check if we can open a new position."""
        # Already have position in this market
        if token_id in self.positions:
            return False

        # Hit max positions
        if len(self.positions) >= config.max_open_positions:
            return False

        return True

    def add_position(
        self,
        token_id: str,
        market_question: str,
        entry_price: float,
        size: float,
        order_id: Optional[str] = None,
    ):
        """Record a new position."""
        self.positions[token_id] = Position(
            token_id=token_id,
            market_question=market_question,
            entry_price=entry_price,
            size=size,
            entry_time=time.time(),
            order_id=order_id,
            current_price=entry_price,
        )
        print(f"Opened position: {market_question[:50]}... @ {entry_price:.2f}")

    async def update_positions(self):
        """Update current prices and PnL for all positions."""
        for token_id, pos in list(self.positions.items()):
            try:
                current_price = await self.client.get_price(token_id)
                pos.current_price = current_price

                if pos.entry_price > 0:
                    pos.pnl_pct = (current_price - pos.entry_price) / pos.entry_price
            except Exception as e:
                print(f"Error updating position {token_id}: {e}")

    async def check_exits(self) -> list[str]:
        """Check if any positions should be closed."""
        to_close = []

        for token_id, pos in self.positions.items():
            # Take profit
            if pos.pnl_pct >= config.take_profit_pct:
                print(
                    f"Take profit triggered: {pos.market_question[:30]}... "
                    f"PnL: {pos.pnl_pct:.1%}"
                )
                to_close.append(token_id)
                continue

            # Stop loss
            if pos.pnl_pct <= -config.stop_loss_pct:
                print(
                    f"Stop loss triggered: {pos.market_question[:30]}... "
                    f"PnL: {pos.pnl_pct:.1%}"
                )
                to_close.append(token_id)
                continue

        return to_close

    def close_position(self, token_id: str) -> Optional[Position]:
        """Remove a position from tracking."""
        if token_id in self.positions:
            pos = self.positions.pop(token_id)
            print(
                f"Closed position: {pos.market_question[:30]}... "
                f"Final PnL: {pos.pnl_pct:.1%}"
            )
            return pos
        return None

    def get_total_exposure(self) -> float:
        """Calculate total capital at risk."""
        return sum(p.size for p in self.positions.values())

    def print_status(self):
        """Print current positions status."""
        if not self.positions:
            print("No open positions")
            return

        print(f"\n{'='*60}")
        print(f"Open Positions ({len(self.positions)}/{config.max_open_positions})")
        print(f"{'='*60}")

        for pos in self.positions.values():
            age_mins = (time.time() - pos.entry_time) / 60
            print(
                f"  {pos.market_question[:40]}...\n"
                f"    Entry: {pos.entry_price:.2f} | "
                f"Current: {pos.current_price:.2f} | "
                f"PnL: {pos.pnl_pct:+.1%} | "
                f"Age: {age_mins:.0f}m"
            )

        print(f"Total exposure: ${self.get_total_exposure():.2f}")
        print(f"{'='*60}\n")
