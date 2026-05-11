"""
Browser-based X authentication using Playwright.

Uses Playwright to:
1. Navigate to X.com and solve any Cloudflare challenges
2. Log in with credentials
3. Export session cookies to outputs/cookies.json

Usage:
    python scripts/browser_auth.py --username PanEvan82430 --password evansparki1011!
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COOKIES = PROJECT_ROOT / "outputs" / "cookies.json"
DEFAULT_PROXY = "http://127.0.0.1:7897"


def save_twikit_cookies(cookies: list) -> bool:
    """
    Convert Playwright cookies to twikit-compatible format and save as JSON.
    Returns True on success.
    """
    twikit_cookies = {}
    for c in cookies:
        name = c["name"]
        value = c["value"]
        domain = c.get("domain", ".x.com")
        path = c.get("path", "/")
        secure = c.get("secure", True)
        expires = c.get("expires", -1)

        # Build the Netscape-style cookie format twikit expects
        twikit_cookies[name] = {
            "value": value,
            "domain": domain,
            "path": path,
            "secure": secure,
            "expires": expires,
        }

    DEFAULT_COOKIES.parent.mkdir(parents=True, exist_ok=True)
    import json
    with open(DEFAULT_COOKIES, "w", encoding="utf-8") as f:
        json.dump(twikit_cookies, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(twikit_cookies)} cookies to {DEFAULT_COOKIES}")
    return True


def run_auth(username: str, password: str, email: str | None, proxy: str | None, cookies_path: Path):
    """Main auth flow using Playwright."""
    proxy_config = {"server": proxy} if proxy else None

    with sync_playwright() as p:
        # Launch Chromium with proxy
        browser = p.chromium.launch(
            headless=False,
            proxy=proxy_config,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )

        # Navigate to X login page
        page = context.new_page()
        print("Navigating to x.com...")
        page.goto("https://x.com/i/flow/login", timeout=60000)

        # Wait for login form to load
        try:
            page.wait_for_selector('input[autocomplete="username"]', timeout=30000)
            print("Login form loaded.")
        except Exception as e:
            print(f"Could not find username field: {e}")
            print(f"Current URL: {page.url}")
            print(f"Page title: {page.title()}")
            # Take a screenshot for debugging
            page.screenshot(path="outputs/login_page.png")
            print("Screenshot saved to outputs/login_page.png")
            browser.close()
            sys.exit(1)

        # Enter username
        page.fill('input[autocomplete="username"]', username)
        page.click('[role="button"]:has-text("Next")')

        # Wait for password field (might need to handle "verify email" step)
        try:
            page.wait_for_selector('input[name="password"]', timeout=10000)
        except Exception:
            # Might be email verification - try email if provided, or prompt
            try:
                page.wait_for_selector('input[autocomplete="username"]', timeout=5000)
                if email:
                    print(f"Using provided email for verification: {email}")
                    page.fill('input[autocomplete="username"]', email)
                else:
                    email = input("X requires email verification. Enter your email: ").strip()
                    page.fill('input[autocomplete="username"]', email)
                page.click('[role="button"]:has-text("Next")')
                page.wait_for_selector('input[name="password"]', timeout=15000)
            except Exception as e:
                print(f"Could not reach password field: {e}")
                page.screenshot(path="outputs/verify_page.png")
                print("Screenshot saved to outputs/verify_page.png")
                browser.close()
                sys.exit(1)

        # Enter password
        page.fill('input[name="password"]', password)
        page.click('[data-testid="LoginForm_Login_Button"]')

        # Wait for successful login
        try:
            page.wait_for_selector('[data-testid="primaryColumn"]', timeout=30000)
            print("Login successful!")
        except Exception as e:
            print(f"Login may have failed: {e}")
            print(f"Current URL: {page.url}")
            page.screenshot(path="outputs/after_login.png")
            print("Screenshot saved to outputs/after_login.png")
            browser.close()
            sys.exit(1)

        # Get all cookies
        cookies = context.cookies()
        print(f"Captured {len(cookies)} cookies")

        # Save cookies in httpx-compatible format (name → plain value string)
        # NOT the nested dict format (name → {value, domain, path...})
        httpx_cookies = {}
        for c in cookies:
            httpx_cookies[c["name"]] = c["value"]

        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(cookies_path, "w", encoding="utf-8") as f:
            json.dump(httpx_cookies, f, ensure_ascii=False, indent=2)

        print(f"Cookies saved to {cookies_path}")

        # Verify by navigating to home page
        page.goto("https://x.com/home", timeout=30000)
        try:
            page.wait_for_selector('[data-testid="primaryColumn"]', timeout=10000)
            print("Verified: Home page loaded successfully with cookies")
        except Exception:
            print("Warning: Could not verify home page load")

        browser.close()
        return True


def main():
    parser = argparse.ArgumentParser(description="Browser-based X authentication")
    parser.add_argument("--username", required=True, help="X username or email")
    parser.add_argument("--password", required=True, help="X password")
    parser.add_argument("--email", type=str, default=None, help="Email for verification if prompted")
    parser.add_argument("--proxy", type=str, default=DEFAULT_PROXY, help=f"HTTP proxy (default: {DEFAULT_PROXY})")
    parser.add_argument("--cookies", type=str, default=str(DEFAULT_COOKIES), help="Output cookies path")
    args = parser.parse_args()

    print("=== Browser-based X Authentication ===")
    print(f"Proxy: {args.proxy}")
    print(f"Output: {args.cookies}")
    print()
    print("A browser window will open. Please log in to X manually.")
    print("If you have 2FA enabled, you'll need to handle that in the browser.")
    print()

    try:
        run_auth(
            username=args.username,
            password=args.password,
            email=args.email,
            proxy=args.proxy,
            cookies_path=Path(args.cookies),
        )
        print("\nAuthentication complete!")
    except Exception as e:
        print(f"\nAuth failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
