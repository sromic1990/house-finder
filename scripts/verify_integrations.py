"""Verify the Gmail + Digitransit integrations are wired correctly.

Reads credentials from .env / environment (never prints them). Usage:
    python scripts/verify_integrations.py            # both
    python scripts/verify_integrations.py --routing  # Digitransit only
    python scripts/verify_integrations.py --email     # Gmail only
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from core.config import load_config           # noqa: E402
from core.notify import SMTPMailer, render_email  # noqa: E402
from core.routing import transit_minutes       # noqa: E402


def check_routing() -> bool:
    print("\n== Digitransit routing ==")
    if not os.getenv("DIGITRANSIT_KEY"):
        print("  ✗ DIGITRANSIT_KEY not set in .env")
        return False
    # Myllypuro (a real listing location) -> center + airport
    mins = transit_minutes(60.2245, 25.0760)
    if not mins:
        print("  ✗ No result — key rejected or API/query mismatch (see routing.py)")
        return False
    print(f"  ✓ transit minutes from Myllypuro: {mins}")
    print("    (center & airport in minutes — travel_time filter is live)")
    return True


def check_email() -> bool:
    print("\n== Gmail SMTP ==")
    mailer = SMTPMailer.from_env()
    if mailer is None:
        print("  ✗ GMAIL_USER / GMAIL_APP_PASSWORD not set in .env")
        return False
    cfg = load_config()
    to = cfg.get("notify", {}).get("email", {}).get("to") or mailer.user
    try:
        html = ("<h2>🏠 House-finder test</h2><p>If you can read this, "
                "top-5 notifications are working.</p>")
        mailer.send("🏠 House-finder: test email", html, to)
        print(f"  ✓ Test email sent to {to}")
        return True
    except Exception as exc:
        print(f"  ✗ Send failed: {exc}")
        print("    Common cause: use a 16-char App Password (2FA required), "
              "not your normal password.")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--routing", action="store_true")
    ap.add_argument("--email", action="store_true")
    args = ap.parse_args()
    do_all = not (args.routing or args.email)

    ok = True
    if args.routing or do_all:
        ok &= check_routing()
    if args.email or do_all:
        ok &= check_email()
    print("\n" + ("All checked integrations OK ✓" if ok else "Some checks failed ✗"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
