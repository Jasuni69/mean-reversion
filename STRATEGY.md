# NO Mean Reversion Strategy: Notes & Caveats

## Why This Works (In Theory)

The edge isn't just "80% of markets end in NO." It's the structure behind that stat:

- **YES spikes are almost always a liquidity imbalance, not real conviction.** Someone sweeps the book, price jumps, but there's no new information.
- **Baseline + absolute jump logic isolates whale-driven inefficiency** instead of betting the market's full narrative.
- **Sitting on the bid instead of discounting** means better EV and faster fills without burning slippage.
- **Tracking true YES bid instead of deriving from NO** is the difference between an actual quant strategy and a paint-by-numbers script.

This bot is solving a microstructure problem—reacting to orderbook events, not just price.

## The Edge is Real but Fragile

Liquidity imbalances do create predictable reversions, but you're competing against other participants who see the same thing. The moment a spike happens, anyone watching the orderbook can react. Your fill quality depends entirely on latency and queue position.

## Size Matters in the Wrong Direction

The trades that look most attractive (big spikes, thin books) are exactly where you'll have the hardest time getting filled at good prices. You might paper trade this and see 80% win rate, then run it live and realize you're only getting filled on the trades that keep moving against you.

## The "80% Resolve NO" Stat is Backwards-Looking

It describes historical market types, not a physical law. A sports match or election with genuine uncertainty doesn't care about base rates from other market categories.

## Orderbook Positioning is the Actual Alpha

The bot places limit orders, but the real implementation question is:
- Where in the queue?
- How aggressive on price?
- When to cancel?

That's where microstructure knowledge pays off—and where a simple script becomes a real system.

## Risk of Ruin is the Constraint

Five max positions at $100 each sounds conservative until you hit a correlated event where YES was actually right across multiple markets. Consider:
- Kelly criterion for position sizing
- Correlation limits across similar markets
- Maximum portfolio exposure caps

## Bottom Line

This is a reasonable starting framework, but the gap between "interesting strategy" and "profitable in production" is mostly in execution details that aren't in this code yet.
