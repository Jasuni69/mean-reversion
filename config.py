import os
import sys
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class TradingConfig:
    """Configuration for the mean reversion bot."""

    # Polymarket API credentials
    private_key: str = os.getenv("PRIVATE_KEY", "")
    api_key: str = os.getenv("API_KEY", "")
    api_secret: str = os.getenv("API_SECRET", "")
    api_passphrase: str = os.getenv("API_PASSPHRASE", "")
    chain_id: int = int(os.getenv("CHAIN_ID", "137"))

    # Dry run mode - no actual trades placed
    dry_run: bool = "--dry-run" in sys.argv or os.getenv("DRY_RUN", "false").lower() == "true"

    # Strategy parameters
    min_spike_threshold: float = float(os.getenv("MIN_SPIKE_THRESHOLD", "0.20"))
    lookback_seconds: int = 300  # 5 minutes to detect spike

    # Position sizing
    max_position_size: float = float(os.getenv("MAX_POSITION_SIZE", "100"))
    min_liquidity: float = float(os.getenv("MIN_LIQUIDITY", "1000"))

    # Risk management
    max_open_positions: int = 5
    stop_loss_pct: float = 0.30  # Exit if NO drops 30%
    take_profit_pct: float = 0.15  # Take profit at 15% gain

    # API endpoints
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"

    def validate(self) -> bool:
        """Check if required credentials are set."""
        return all([
            self.private_key,
            self.api_key,
            self.api_secret,
            self.api_passphrase
        ])


config = TradingConfig()
