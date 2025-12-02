"""Polymarket API client for market data and trading."""

import asyncio
import json
import time
from typing import Optional
from dataclasses import dataclass
import aiohttp

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

from config import config


@dataclass
class Market:
    """Represents a Polymarket market."""

    condition_id: str
    question: str
    token_id_yes: str
    token_id_no: str
    price_yes: float
    price_no: float
    volume_24h: float
    liquidity: float
    end_date: Optional[str] = None


@dataclass
class PriceSnapshot:
    """Price snapshot at a point in time."""

    token_id: str
    price: float
    timestamp: float


class PolymarketClient:
    """Client for interacting with Polymarket."""

    def __init__(self):
        self.clob_client: Optional[ClobClient] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._price_history: dict[str, list[PriceSnapshot]] = {}

    async def connect(self):
        """Initialize connections."""
        if not config.validate():
            raise ValueError("Missing API credentials. Check your .env file.")

        self.clob_client = ClobClient(
            config.clob_host,
            key=config.private_key,
            chain_id=config.chain_id,
            creds={
                "apiKey": config.api_key,
                "secret": config.api_secret,
                "passphrase": config.api_passphrase,
            },
        )

        self.session = aiohttp.ClientSession()
        print("Connected to Polymarket")

    async def close(self):
        """Close connections."""
        if self.session:
            await self.session.close()

    async def get_active_markets(self) -> list[Market]:
        """Fetch active markets from Gamma API."""
        url = f"{config.gamma_host}/markets"
        params = {
            "closed": "false",
            "limit": 100,
        }

        async with self.session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to fetch markets: {resp.status}")

            data = await resp.json()
            markets = []

            for item in data:
                try:
                    # Parse clobTokenIds - can be JSON string or list
                    token_ids = item.get("clobTokenIds")
                    if isinstance(token_ids, str):
                        token_ids = json.loads(token_ids)
                    if not token_ids or len(token_ids) < 2:
                        continue

                    # Parse outcomePrices - can be JSON string or list
                    prices = item.get("outcomePrices", "[0.5, 0.5]")
                    if isinstance(prices, str):
                        prices = json.loads(prices)

                    markets.append(
                        Market(
                            condition_id=item["conditionId"],
                            question=item.get("question", ""),
                            token_id_yes=token_ids[0],
                            token_id_no=token_ids[1],
                            price_yes=float(prices[0]),
                            price_no=float(prices[1]),
                            volume_24h=float(item.get("volume24hr", 0)),
                            liquidity=float(item.get("liquidity", 0)),
                            end_date=item.get("endDate"),
                        )
                    )
                except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
                    continue

            return markets

    async def get_orderbook(self, token_id: str) -> dict:
        """Get orderbook for a token."""
        try:
            book = self.clob_client.get_order_book(token_id)
            # Convert OrderBookSummary object to dict with float prices
            return {
                "bids": [{"price": float(b.price), "size": float(b.size)} for b in (book.bids or [])],
                "asks": [{"price": float(a.price), "size": float(a.size)} for a in (book.asks or [])],
            }
        except Exception as e:
            print(f"Error fetching orderbook: {e}")
            return {"bids": [], "asks": []}

    async def get_price(self, token_id: str) -> float:
        """Get current mid price for a token."""
        book = await self.get_orderbook(token_id)

        bids = book.get("bids", [])
        asks = book.get("asks", [])

        if bids and asks:
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            return (best_bid + best_ask) / 2
        elif bids:
            return float(bids[0]["price"])
        elif asks:
            return float(asks[0]["price"])

        return 0.5  # Default to 50%

    def record_price(self, token_id: str, price: float):
        """Record price for tracking spikes."""
        now = time.time()
        snapshot = PriceSnapshot(token_id=token_id, price=price, timestamp=now)

        if token_id not in self._price_history:
            self._price_history[token_id] = []

        self._price_history[token_id].append(snapshot)

        # Keep only recent history
        cutoff = now - config.lookback_seconds
        self._price_history[token_id] = [
            s for s in self._price_history[token_id] if s.timestamp > cutoff
        ]

    def get_price_change(self, token_id: str) -> Optional[float]:
        """Calculate price change over lookback period."""
        history = self._price_history.get(token_id, [])

        if len(history) < 2:
            return None

        oldest_price = history[0].price
        current_price = history[-1].price

        if oldest_price == 0:
            return None

        return (current_price - oldest_price) / oldest_price

    async def place_order(
        self,
        token_id: str,
        side: str,
        size: float,
        price: float,
    ) -> Optional[str]:
        """Place a limit order."""
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY if side == "buy" else "SELL",
            )

            signed_order = self.clob_client.create_order(order_args)
            response = self.clob_client.post_order(signed_order, OrderType.GTC)

            if response.get("success"):
                return response.get("orderID")
            else:
                print(f"Order failed: {response}")
                return None

        except Exception as e:
            print(f"Error placing order: {e}")
            return None

    async def get_positions(self) -> list[dict]:
        """Get current open positions."""
        try:
            return self.clob_client.get_positions() or []
        except Exception as e:
            print(f"Error fetching positions: {e}")
            return []

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        try:
            self.clob_client.cancel(order_id)
            return True
        except Exception as e:
            print(f"Error cancelling order: {e}")
            return False
