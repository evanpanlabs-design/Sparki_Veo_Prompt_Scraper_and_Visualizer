"""
X/Twitter Authentication Setup for twikit.

Performs first-time login and saves cookies for subsequent scraper runs.

Usage:
    python scripts/auth_setup.py
    python scripts/auth_setup.py --cookies outputs/cookies.json
"""

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

import httpx
from twikit import Client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COOKIES = PROJECT_ROOT / "outputs" / "cookies.json"
DEFAULT_PROXY = "http://127.0.0.1:7897"


async def auth_setup(cookies_path: Path, proxy: str | None = DEFAULT_PROXY):
    """Interactive login flow that saves cookies."""
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
    client = Client("en-US", proxy=proxy, timeout=httpx.Timeout(30.0, read=60.0))

    print("=== X/Twitter Authentication Setup ===")
    print("Your credentials are used only for this login and are not stored.")
    print("Only the session cookies will be saved.\n")

    username = input("Username: ").strip()
    if not username:
        print("Username cannot be empty.")
        sys.exit(1)

    email = input("Email (optional, press Enter to skip): ").strip() or None
    password = getpass.getpass("Password: ")
    if not password:
        print("Password cannot be empty.")
        sys.exit(1)

    print("\nLogging in...")
    try:
        await client.login(
            auth_info_1=username,
            auth_info_2=email,
            password=password,
        )
    except Exception as e:
        error_msg = str(e).lower()
        if "challenge" in error_msg or "verification" in error_msg:
            print("\nX is requesting additional verification.")
            print("Check your email or phone for a verification code.")
            # twikit may handle 2FA automatically in some cases
            # If it raises, the user may need to retry
        print(f"Login failed: {e}")
        sys.exit(1)

    # Save cookies
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    client.save_cookies(str(cookies_path))

    print(f"\nLogin successful! Cookies saved to {cookies_path}")
    print("You can now run the scraper without re-authenticating.")

    # Quick verification
    print("\nVerifying session...")
    try:
        user = await client.user()
        print(f"Logged in as: @{user.screen_name} (ID: {user.id})")
    except Exception as e:
        print(f"Verification warning: {e}")
        print("Cookies were saved, but session verification failed. Try running the scraper to confirm.")


def main():
    parser = argparse.ArgumentParser(description="Set up X/Twitter authentication for the scraper")
    parser.add_argument("--cookies", type=str, default=str(DEFAULT_COOKIES), help="Path to save cookies.json")
    parser.add_argument("--proxy", type=str, default=DEFAULT_PROXY, help=f"HTTP proxy URL (default: {DEFAULT_PROXY}, set to '' to disable)")
    args = parser.parse_args()

    asyncio.run(auth_setup(cookies_path=Path(args.cookies), proxy=args.proxy if args.proxy else None))


if __name__ == "__main__":
    main()
