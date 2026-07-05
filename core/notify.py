"""Top-N entry detection + email notification.

Notify rule (from config.notify.email): when a listing rises into the top N
(default 5) that was NOT in the top N at the previous run, send one email.

Transport: Gmail SMTP using an App Password (works headless inside Docker).
  Set env vars:  GMAIL_USER, GMAIL_APP_PASSWORD
  (Google Account -> Security -> 2-Step Verification -> App passwords)
Swap `SMTPMailer` for any object with .send(subject, html, to) to change transport.
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Protocol


# ---- what changed at the top -------------------------------------------
def new_top_entries(previous_ranks: dict[str, int], ranked, top_n: int):
    """Return ScoredListings that are in the top N now but weren't before."""
    entries = []
    for r in ranked:
        if r.rank <= top_n:
            prev = previous_ranks.get(r.listing.uid)
            if prev is None or prev > top_n:
                entries.append(r)
    return entries


# ---- transport ----------------------------------------------------------
class Mailer(Protocol):
    def send(self, subject: str, html: str, to: str) -> None: ...


@dataclass
class SMTPMailer:
    user: str
    password: str
    host: str = "smtp.gmail.com"
    port: int = 587

    @classmethod
    def from_env(cls) -> Optional["SMTPMailer"]:
        user = os.getenv("GMAIL_USER")
        pw = os.getenv("GMAIL_APP_PASSWORD")
        if not user or not pw:
            return None
        return cls(user=user, password=pw)

    def send(self, subject: str, html: str, to: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.user
        msg["To"] = to
        msg.attach(MIMEText("Open in an HTML-capable client.", "plain"))
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(self.host, self.port) as s:
            s.starttls()
            s.login(self.user, self.password)
            s.sendmail(self.user, [to], msg.as_string())


class ConsoleMailer:
    """Fallback used when SMTP creds aren't set — prints instead of sending."""
    def send(self, subject: str, html: str, to: str) -> None:
        # encode-safe for cp1252 Windows consoles
        line = f"[notify] (no SMTP creds) would email {to}: {subject}"
        print(line.encode("ascii", "replace").decode("ascii"))


def default_mailer() -> Mailer:
    return SMTPMailer.from_env() or ConsoleMailer()


# ---- email body ---------------------------------------------------------
def render_email(entries, board_url: str, top_n: int) -> str:
    cards = []
    for r in entries:
        L = r.listing
        photo = L.photos[0] if L.photos else ""
        img = (f'<img src="{photo}" width="160" style="border-radius:8px;'
               f'object-fit:cover" alt="">') if photo else ""
        facts = " · ".join(x for x in [
            f"€{L.price:,.0f}" if L.price else "",
            f"{L.size_m2:g} m²" if L.size_m2 else "",
            f"{L.rooms:g} rooms" if L.rooms else "",
            L.district or L.city,
        ] if x)
        cards.append(f"""
        <tr><td style="padding:12px 0;border-bottom:1px solid #eee">
          <table><tr>
            <td valign="top">{img}</td>
            <td valign="top" style="padding-left:14px">
              <div style="font-size:13px;color:#888">NEW in top {top_n} — rank #{r.rank} · score {r.score}</div>
              <div style="font-size:17px;font-weight:600;margin:2px 0">
                <a href="{L.url}" style="color:#1a1a1a;text-decoration:none">{L.title}</a></div>
              <div style="color:#555">{facts}</div>
            </td>
          </tr></table>
        </td></tr>""")

    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:620px;margin:auto">
  <h2 style="margin:0 0 4px">🏠 New top-{top_n} listing{'s' if len(entries)>1 else ''}</h2>
  <p style="color:#666;margin:0 0 12px">
    {len(entries)} listing{'s' if len(entries)>1 else ''} just climbed into your top {top_n}.</p>
  <table width="100%">{''.join(cards)}</table>
  <p style="margin-top:18px">
    <a href="{board_url}" style="background:#111;color:#fff;padding:10px 18px;
       border-radius:8px;text-decoration:none">Open the leaderboard →</a></p>
</div>"""


def render_price_drops(entries, board_url: str) -> str:
    rows = []
    for r in entries:
        L = r.listing
        drop = (L.prev_price - L.price) if (L.prev_price and L.price) else 0
        pct = (drop / L.prev_price * 100) if L.prev_price else 0
        photo = L.photos[0] if L.photos else ""
        img = (f'<img src="{photo}" width="150" style="border-radius:8px;'
               f'object-fit:cover" alt="">') if photo else ""
        facts = " · ".join(x for x in [
            f"{L.size_m2:g} m²" if L.size_m2 else "",
            f"{L.rooms:g} r" if L.rooms else "", L.district or L.city] if x)
        rows.append(f"""
        <tr><td style="padding:12px 0;border-bottom:1px solid #eee"><table><tr>
          <td valign="top">{img}</td>
          <td valign="top" style="padding-left:14px">
            <div style="font-size:13px;color:#e03131;font-weight:700">▼ €{drop:,.0f} ({pct:.0f}%) · now #{r.rank}</div>
            <div style="font-size:17px;font-weight:600;margin:2px 0">
              <a href="{L.url}" style="color:#1a1a1a;text-decoration:none">{L.title}</a></div>
            <div style="color:#555"><s>€{L.prev_price:,.0f}</s> → <b>€{L.price:,.0f}</b> · {facts}</div>
          </td></tr></table></td></tr>""")
    plural = "s" if len(entries) > 1 else ""
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:620px;margin:auto">
  <h2 style="margin:0 0 4px">📉 Price drop{plural} on your board</h2>
  <p style="color:#666;margin:0 0 12px">{len(entries)} listing{plural} you qualify for just got cheaper.</p>
  <table width="100%">{''.join(rows)}</table>
  <p style="margin-top:18px"><a href="{board_url}" style="background:#111;color:#fff;
     padding:10px 18px;border-radius:8px;text-decoration:none">Open the leaderboard →</a></p>
</div>"""


def notify_price_drops(dropped_ranked, *, config, mailer=None,
                       board_url="http://localhost:8000") -> list:
    """Email the qualifying (ranked) listings whose price fell this run."""
    ecfg = (config.get("notify", {}) or {}).get("email", {}) or {}
    if not ecfg.get("enabled", False) or not dropped_ranked:
        return []
    to = os.getenv("NOTIFY_TO") or ecfg.get("to")
    if not to:
        return []
    mailer = mailer or default_mailer()
    n = len(dropped_ranked)
    subject = (f"📉 Price drop: {dropped_ranked[0].listing.title}" if n == 1
               else f"📉 {n} price drops on your board")
    try:
        mailer.send(subject, render_price_drops(dropped_ranked, board_url), to)
    except Exception as exc:
        import logging
        logging.getLogger("notify").error("price-drop email failed (continuing): %s", exc)
    return dropped_ranked


def render_viewings(entries, board_url: str) -> str:
    """entries: list of (Listing, [new_viewing_str, ...])."""
    rows = []
    for L, viewings in entries:
        photo = L.photos[0] if L.photos else ""
        img = (f'<img src="{photo}" width="150" style="border-radius:8px;'
               f'object-fit:cover" alt="">') if photo else ""
        facts = " · ".join(x for x in [
            f"{L.size_m2:g} m²" if L.size_m2 else "",
            f"{L.rooms:g} r" if L.rooms else "", L.district or L.city] if x)
        times = "".join(f'<div style="font-size:15px;font-weight:700;color:#1a7f37">'
                        f'📅 {v}</div>' for v in viewings)
        rows.append(f"""
        <tr><td style="padding:12px 0;border-bottom:1px solid #eee"><table><tr>
          <td valign="top">{img}</td>
          <td valign="top" style="padding-left:14px">
            {times}
            <div style="font-size:17px;font-weight:600;margin:2px 0">
              <a href="{L.url}" style="color:#1a1a1a;text-decoration:none">{L.title}</a></div>
            <div style="color:#555">{facts}</div>
          </td></tr></table></td></tr>""")
    plural = "s" if len(entries) > 1 else ""
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:620px;margin:auto">
  <h2 style="margin:0 0 4px">📅 New viewing{plural} announced</h2>
  <p style="color:#666;margin:0 0 12px">{len(entries)} home{plural} on your board just posted a public viewing time.</p>
  <table width="100%">{''.join(rows)}</table>
  <p style="margin-top:18px"><a href="{board_url}" style="background:#111;color:#fff;
     padding:10px 18px;border-radius:8px;text-decoration:none">Open the leaderboard →</a></p>
</div>"""


def notify_viewings(entries, *, config, mailer=None,
                    board_url="http://localhost:8000") -> list:
    """Email board listings that just announced NEW public viewing times.
    entries: list of (Listing, [new_viewing_str, ...])."""
    ecfg = (config.get("notify", {}) or {}).get("email", {}) or {}
    if not ecfg.get("enabled", False) or not entries:
        return []
    to = os.getenv("NOTIFY_TO") or ecfg.get("to")
    if not to:
        return []
    mailer = mailer or default_mailer()
    n = len(entries)
    subject = (f"📅 Viewing: {entries[0][0].title}" if n == 1
               else f"📅 {n} new viewings on your board")
    try:
        mailer.send(subject, render_viewings(entries, board_url), to)
    except Exception as exc:
        import logging
        logging.getLogger("notify").error("viewing email failed (continuing): %s", exc)
    return entries


def notify_new_top_entries(previous_ranks, ranked, *, config, mailer=None,
                           board_url="http://localhost:8000") -> list:
    """Detect + send. Returns the entries that triggered a notification."""
    ecfg = (config.get("notify", {}) or {}).get("email", {}) or {}
    if not ecfg.get("enabled", False):
        return []
    top_n = int(ecfg.get("top_n", 5))
    to = os.getenv("NOTIFY_TO") or ecfg.get("to")   # keep email out of public config
    entries = new_top_entries(previous_ranks, ranked, top_n)
    if not entries or not to:
        return entries if entries else []

    mailer = mailer or default_mailer()
    subject = (f"🏠 New #{entries[0].rank} in your leaderboard: "
               f"{entries[0].listing.title}") if len(entries) == 1 else \
              f"🏠 {len(entries)} new listings in your top {top_n}"
    try:                    # best-effort: a mail hiccup must not abort the run/render
        mailer.send(subject, render_email(entries, board_url, top_n), to)
    except Exception as exc:
        import logging
        logging.getLogger("notify").error("email send failed (continuing): %s", exc)
    return entries
