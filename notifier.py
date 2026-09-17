#!/usr/bin/env python3
"""Erkennt neue Events auf twomoons.ch und postet sie per Discord-Webhook."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

LOG = logging.getLogger("twomoons")

DEFAULT_CONFIG = Path("config.json")
DEFAULT_STATE = Path("state.json")

# Reihenfolge der Felder im Embed. Pro Eintrag: Anzeige-Label + erkannte Varianten
# (klein geschrieben) aus dem Modal, deutsch wie englisch.
FIELD_ORDER: list[tuple[str, tuple[str, ...]]] = [
    ("Format", ("format",)),
    ("Eintritt", ("eintritt", "entry fee", "entryfee", "entry", "startgeld", "fee")),
    ("SUL", ("sul", "sul level", "sul-level")),
    ("Turniersystem", ("turniersystem", "tournament system", "system", "modus")),
    ("Preispool", ("preispool", "prizepool", "prize pool", "preise", "prizes")),
    ("Includes", ("includes", "inklusive", "beinhaltet", "enthält")),
]

# Zeilen wie "Magic: The Gathering" sehen aus wie "Label: Wert", sind aber keine.
LABEL_DENYLIST = {
    "magic",
    "pokemon",
    "pokémon",
    "yu-gi-oh",
    "one piece",
    "lorcana",
    "riftbound",
    "achtung",
    "hinweis",
    "info",
}

LABEL_RE = re.compile(r"^\s*([^:\n]{2,30}?)\s*:\s*(.+?)\s*$")


@dataclass
class Event:
    event_id: str
    title: str
    date_text: str = ""
    location_name: str = ""
    location_address: str = ""
    seats: str = ""
    booking_url: str = ""
    image_url: str = ""
    summary: str = ""
    details: dict[str, str] = field(default_factory=dict)

    @property
    def has_booking_link(self) -> bool:
        return bool(self.booking_url)


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def is_inside(container: Tag, node: Tag) -> bool:
    return any(parent is container for parent in node.parents)


def has_class(node: Any, css_class: str) -> bool:
    return isinstance(node, Tag) and css_class in (node.get("class") or [])


def find_icon(card: Tag, icon_class: str) -> Tag | None:
    return card.find(class_=icon_class)


def value_after_icon(card: Tag, icon_class: str, css_class: str = "ms-1") -> Tag | None:
    """Element mit `css_class`, das dem Icon folgt (erst Geschwister, dann Karte)."""
    icon = find_icon(card, icon_class)
    if icon is None:
        return None

    for sibling in icon.next_siblings:
        if has_class(sibling, css_class):
            return sibling
        if isinstance(sibling, Tag) and sibling.find(class_=css_class):
            return sibling.find(class_=css_class)

    candidate = icon.find_next(class_=css_class)
    if candidate is not None and is_inside(card, candidate):
        return candidate
    return None


def text_after(node: Tag) -> str:
    """Freier Text-Node direkt nach `node` (z. B. die Adresse nach dem Ortsnamen)."""
    parts: list[str] = []
    for sibling in node.next_siblings:
        if isinstance(sibling, NavigableString):
            parts.append(str(sibling))
            continue
        if isinstance(sibling, Tag):
            if sibling.name == "br":
                continue
            break
    return clean_text("".join(parts)).lstrip("|").strip()


def parse_label_lines(text: str) -> dict[str, str]:
    """Generisches "Label: Wert" pro Zeile — unabhängig von festen Labels."""
    found: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = clean_text(raw_line)
        if not line or len(line) > 400:
            continue
        match = LABEL_RE.match(line)
        if not match:
            continue
        label, value = match.group(1).strip(), match.group(2).strip()
        if not value or label.lower() in LABEL_DENYLIST or len(label.split()) > 3:
            continue
        found.setdefault(label, value)
    return found


def find_modal(soup: BeautifulSoup, card: Tag) -> Tag | None:
    nested = card.find("div", class_="modal")
    if nested is not None:
        return nested

    for attribute in ("data-bs-target", "data-target", "href"):
        trigger = card.find(attrs={attribute: re.compile(r"^#")})
        if trigger is None:
            continue
        modal_id = str(trigger.get(attribute)).lstrip("#")
        if not modal_id:
            continue
        modal = soup.find(id=modal_id)
        if isinstance(modal, Tag):
            return modal
    return None


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #


def fetch_html(url: str, request_config: dict[str, Any]) -> str:
    headers = {
        "User-Agent": request_config.get("user_agent", "twomoons-discord-notifier/1.0"),
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.6",
    }
    retries = int(request_config.get("retries", 3))
    timeout = int(request_config.get("timeout", 30))

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as error:
            last_error = error
            wait = 2**attempt
            LOG.warning("Abruf fehlgeschlagen (%s/%s) für %s: %s", attempt, retries, url, error)
            if attempt < retries:
                time.sleep(wait)
    raise RuntimeError(f"Konnte {url} nicht laden: {last_error}")


def parse_events(html: str, page_url: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")

    cards: list[Tag] = []
    for container in soup.select("div.netzp-events6-list"):
        cards.extend(container.select("div.events-card"))
    if not cards:
        cards = soup.select("div.events-card")

    events: list[Event] = []
    for card in cards:
        event = parse_card(soup, card, page_url)
        if event is not None:
            events.append(event)
    return events


def parse_card(soup: BeautifulSoup, card: Tag, page_url: str) -> Event | None:
    title_node = card.select_one(".netzp-events-title")
    title = clean_text(title_node.get_text() if title_node else "")
    if not title:
        return None

    date_node = value_after_icon(card, "icon-calendar")
    date_text = clean_text(date_node.get_text() if date_node else "")

    location_node = value_after_icon(card, "icon-marker")
    location_name = clean_text(location_node.get_text() if location_node else "")
    location_address = text_after(location_node) if location_node else ""

    seats_node = value_after_icon(card, "icon-checkmark-circle")
    seats = clean_text(seats_node.get_text() if seats_node else "")

    booking_anchor = card.select_one('a[href*="slotId="]')
    booking_url = urljoin(page_url, str(booking_anchor.get("href"))) if booking_anchor else ""

    image = card.find("img")
    image_src = ""
    if isinstance(image, Tag):
        image_src = str(image.get("src") or image.get("data-src") or "")
    image_url = urljoin(page_url, image_src) if image_src else ""

    summary_node = card.select_one(".card-text.lead")
    summary = clean_text(summary_node.get_text(" ") if summary_node else "")

    details: dict[str, str] = {}
    modal = find_modal(soup, card)
    if modal is not None:
        body = modal.select_one(".modal-body") or modal
        details = parse_label_lines(body.get_text("\n"))

    event_id = booking_url or f"{title}|{date_text}"

    return Event(
        event_id=event_id,
        title=title,
        date_text=date_text,
        location_name=location_name,
        location_address=location_address,
        seats=seats,
        booking_url=booking_url,
        image_url=image_url,
        summary=summary,
        details=details,
    )


# --------------------------------------------------------------------------- #
# Discord-Embed
# --------------------------------------------------------------------------- #


def resolve_location(event: Event, locations: dict[str, Any]) -> tuple[str, str]:
    """Liefert (Standort-URL, Zusatztext) für den Ort des Events."""
    haystack = f"{event.location_name} {event.location_address}".lower()
    for branch in locations.get("branches", []):
        if any(str(token).lower() in haystack for token in branch.get("match", [])):
            return branch.get("url", ""), clean_text(branch.get("note", "")) or event.location_address
    return locations.get("default_url", ""), event.location_address


def pick_detail(details: dict[str, str], variants: tuple[str, ...]) -> tuple[str, str] | None:
    for key, value in details.items():
        if key.lower() in variants:
            return key, value
    return None


def color_to_int(color: str) -> int:
    try:
        return int(str(color).lstrip("#"), 16)
    except ValueError:
        return 0x5865F2


def build_embed(event: Event, category: dict[str, Any], locations: dict[str, Any]) -> dict[str, Any]:
    used_keys: set[str] = set()
    lines: list[str] = []

    if event.date_text:
        lines.append(f"**Datum:** {event.date_text}")

    ordered: list[tuple[str, str]] = []
    for label, variants in FIELD_ORDER:
        hit = pick_detail(event.details, variants)
        if hit is None:
            continue
        used_keys.add(hit[0])
        ordered.append((label, hit[1]))

    def emit(labels: set[str]) -> None:
        for label, value in ordered:
            if label in labels:
                lines.append(f"**{label}:** {value}")

    emit({"Format", "Eintritt"})

    if event.location_name:
        location_url, extra = resolve_location(event, locations)
        location_line = (
            f"**Ort:** [{event.location_name}]({location_url})" if location_url else f"**Ort:** {event.location_name}"
        )
        if extra:
            location_line += f" {extra}"
        lines.append(location_line)

    emit({"SUL", "Turniersystem", "Preispool", "Includes"})

    # Felder, die im Modal stehen, aber in FIELD_ORDER nicht vorkommen (andere Kategorien).
    for key, value in event.details.items():
        if key not in used_keys:
            lines.append(f"**{key}:** {value}")

    if event.seats:
        lines.append(f"**Plätze:** {event.seats}")

    if event.summary:
        summary = event.summary if len(event.summary) <= 300 else event.summary[:297] + "..."
        lines.append(f"\n{summary}")

    if event.has_booking_link:
        lines.append(f"\n**Link:** [Zur Buchung]({event.booking_url})")
    else:
        lines.append(f"\n**Link:** [Zu den Events]({category['url']})")

    embed: dict[str, Any] = {
        "title": event.title[:256],
        "url": event.booking_url or category["url"],
        "description": "\n".join(lines)[:4096],
        "color": color_to_int(category.get("color", "#5865F2")),
        "footer": {"text": f"{category['name']} · twomoons.ch"},
    }
    if event.image_url:
        embed["thumbnail"] = {"url": event.image_url}
    return embed


def post_embed(
    webhook_url: str,
    embed: dict[str, Any],
    username: str,
    timeout: int = 30,
) -> None:
    payload = {"embeds": [embed]}
    if username:
        payload["username"] = username

    for attempt in range(1, 4):
        response = requests.post(webhook_url, json=payload, timeout=timeout)
        if response.status_code in (200, 204):
            return
        if response.status_code == 429:
            retry_after = 2.0
            try:
                retry_after = float(response.json().get("retry_after", 2.0))
            except (ValueError, AttributeError, TypeError):
                pass
            LOG.warning("Discord Rate-Limit, warte %.1fs", retry_after)
            time.sleep(retry_after + 0.5)
            continue
        if 500 <= response.status_code < 600:
            LOG.warning("Discord antwortete %s, Versuch %s/3", response.status_code, attempt)
            time.sleep(2**attempt)
            continue
        raise RuntimeError(f"Discord lehnte den Post ab ({response.status_code}): {response.text[:300]}")

    raise RuntimeError("Discord-Post nach mehreren Versuchen fehlgeschlagen")


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        LOG.error("%s ist kein gültiges JSON: %s", path, error)
        raise


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Ablauf
# --------------------------------------------------------------------------- #


def process_category(
    category: dict[str, Any],
    config: dict[str, Any],
    state: dict[str, Any],
    args: argparse.Namespace,
) -> int:
    key = category["key"]
    LOG.info("[%s] Hole %s", key, category["url"])

    html = fetch_html(category["url"], config.get("request", {}))
    events = parse_events(html, category["url"])
    LOG.info("[%s] %s Event(s) auf der Seite gefunden", key, len(events))

    if not events:
        LOG.warning("[%s] Keine Events geparst — HTML-Struktur womöglich geändert", key)

    category_state = state.setdefault("categories", {}).setdefault(key, {})
    seen: dict[str, Any] = category_state.setdefault("seen", {})
    first_run = not category_state.get("initialized", False)

    new_events = [event for event in events if event.event_id not in seen]
    LOG.info("[%s] %s davon neu", key, len(new_events))

    webhook_url = os.environ.get(category["webhook_env"], "").strip()
    should_post = bool(new_events) and (not first_run or args.post_existing)

    if first_run and not args.post_existing:
        LOG.info("[%s] Erster Lauf — %s Event(s) werden nur als bekannt gespeichert", key, len(new_events))
    elif should_post and not webhook_url:
        LOG.error("[%s] %s nicht gesetzt — es wird nichts gepostet", key, category["webhook_env"])
        should_post = False

    posted = 0
    if should_post:
        limit = args.limit or int(config.get("discord", {}).get("max_posts_per_run", 25))
        delay = float(config.get("discord", {}).get("delay_between_posts", 1.5))
        username = config.get("discord", {}).get("username", "")

        for event in new_events[:limit]:
            embed = build_embed(event, category, config.get("locations", {}))
            if args.dry_run:
                LOG.info("[%s] DRY-RUN Embed:\n%s", key, json.dumps(embed, indent=2, ensure_ascii=False))
            else:
                post_embed(webhook_url, embed, username)
                LOG.info("[%s] Gepostet: %s", key, event.title)
                time.sleep(delay)
            posted += 1

        if len(new_events) > limit:
            LOG.warning(
                "[%s] %s weitere neue Events werden erst im nächsten Lauf gepostet",
                key,
                len(new_events) - limit,
            )

    if args.dry_run:
        LOG.info("[%s] DRY-RUN — state.json bleibt unverändert", key)
        return posted

    for event in events:
        seen[event.event_id] = {"title": event.title, "date": event.date_text}
    category_state["initialized"] = True
    category_state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return posted


def select_categories(config: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    categories = config.get("categories", [])
    if args.category:
        wanted = set(args.category)
        selected = [category for category in categories if category["key"] in wanted]
        missing = wanted - {category["key"] for category in selected}
        if missing:
            LOG.error("Unbekannte Kategorie(n): %s", ", ".join(sorted(missing)))
        return selected
    return [category for category in categories if category.get("enabled", True)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TwoMoons Event-Notifier für Discord")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--category", action="append", help="Nur diese Kategorie (mehrfach möglich)")
    parser.add_argument("--dry-run", action="store_true", help="Nichts posten, nichts speichern")
    parser.add_argument(
        "--post-existing",
        action="store_true",
        help="Auch beim allerersten Lauf alle aktuellen Events posten",
    )
    parser.add_argument("--limit", type=int, default=0, help="Maximale Anzahl Posts pro Kategorie")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_json(args.config, {})
    if not config:
        LOG.error("Konfiguration %s fehlt oder ist leer", args.config)
        return 1

    state = load_json(args.state, {"categories": {}})
    categories = select_categories(config, args)
    if not categories:
        LOG.error("Keine aktiven Kategorien in %s", args.config)
        return 1

    total_posted = 0
    failed: list[str] = []
    for category in categories:
        try:
            total_posted += process_category(category, config, state, args)
        except Exception as error:  # eine kaputte Kategorie darf die anderen nicht stoppen
            failed.append(category["key"])
            LOG.exception("[%s] Fehler: %s", category["key"], error)

    if not args.dry_run:
        save_state(args.state, state)

    LOG.info(
        "Fertig: %s Event(s) gepostet, %s/%s Kategorie(n) ok",
        total_posted,
        len(categories) - len(failed),
        len(categories),
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
