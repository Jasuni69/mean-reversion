"""Generate Polymarket API credentials from your private key."""

import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient

load_dotenv()


def main():
    host = "https://clob.polymarket.com"
    key = os.getenv("PRIVATE_KEY")
    chain_id = 137  # Polygon Mainnet

    if not key:
        print("ERROR: PRIVATE_KEY not found in .env file")
        return

    print("Deriving API credentials from your private key...")
    print("(This signs a message with your wallet - no gas required)\n")

    client = ClobClient(host, key=key, chain_id=chain_id)

    try:
        creds = client.create_or_derive_api_creds()

        print("Success! Add these to your .env file:\n")
        print(f"API_KEY={creds.api_key}")
        print(f"API_SECRET={creds.api_secret}")
        print(f"API_PASSPHRASE={creds.api_passphrase}")

    except Exception as e:
        print(f"Error: {e}")
        print("\nIf you're using an email/Magic wallet, you may need")
        print("to export your key from https://reveal.magic.link/polymarket")


if __name__ == "__main__":
    main()
