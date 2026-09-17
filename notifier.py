#!/usr/bin/env python3
"""Erkennt neue Events auf twomoons.ch und postet sie per Discord-Webhook."""

from __future__ import annotations

import argparse
import hashlib
import itertools
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

# Auszeichnung, die eine Zeile nicht umbricht ("<b>Format:</b> Draft" ist eine Zeile).
INLINE_TAGS = {"a", "b", "code", "em", "font", "i", "label", "small", "span", "strong", "sub", "sup", "u"}
EMPHASIS_TAGS = {"b", "strong"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "dt", "th", "legend", "caption"}

# Bedienelemente und Seitenrahmen ("Schliessen", Navigation, Footer) sind kein Inhalt.
SKIP_TAGS = {
    "aside",
    "button",
    "footer",
    "form",
    "header",
    "input",
    "nav",
    "script",
    "select",
    "style",
    "svg",
    "textarea",
}
SKIP_CLASSES = {
    "modal-footer",
    "btn",
    "btn-close",
    "close",
    "visually-hidden",
    "sr-only",
    "breadcrumb",
    "cookie-permission",
    "offcanvas",
}

# Diese Angaben stehen schon auf der Karte — aus dem Modal nicht doppelt anzeigen.
CARD_LABELS = {
    "ort",
    "place",
    "location",
    "veranstaltungsort",
    "adresse",
    "address",
    "datum",
    "date",
    "zeit",
    "time",
    "uhrzeit",
    "wann",
    "wo",
}


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
    notes: list[str] = field(default_factory=list)

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


@dataclass
class Line:
    text: str
    heading: bool
    block: int


def is_skippable(node: Tag) -> bool:
    if node.name in SKIP_TAGS:
        return True
    classes = set(node.get("class") or [])
    return bool(classes & SKIP_CLASSES)


def extract_lines(root: Tag) -> list[Line]:
    """Sichtbare Zeilen eines Elements.

    Inline-Auszeichnung bleibt in derselben Zeile, damit `<b>Format:</b> Draft`
    als eine Zeile "Format: Draft" ankommt; `<br>` und Block-Elemente trennen.
    Eine Zeile gilt als Überschrift, wenn ihr gesamter Text aus einem Titel-Tag
    oder einer Fett-Auszeichnung stammt ("Preispool" über dem Fliesstext).
    """
    lines: list[Line] = []
    buffer: list[tuple[str, bool]] = []
    blocks = itertools.count()
    block = next(blocks)

    def flush() -> None:
        pieces = [(text, emphasised) for text, emphasised in buffer if clean_text(text)]
        buffer.clear()
        text = clean_text(" ".join(piece for piece, _ in pieces))
        if text:
            heading = bool(pieces) and all(flag for _, flag in pieces)
            lines.append(Line(text=text, heading=heading, block=block))

    def walk(node: Tag, emphasis: int) -> None:
        nonlocal block
        for child in node.children:
            if isinstance(child, NavigableString):
                buffer.append((str(child), emphasis > 0))
            elif isinstance(child, Tag):
                if is_skippable(child):
                    continue
                if child.name == "br":
                    flush()
                elif child.name in INLINE_TAGS:
                    walk(child, emphasis + (1 if child.name in EMPHASIS_TAGS else 0))
                else:
                    # Ein Block-Element beginnt einen neuen Absatz — eine Überschrift
                    # darf ihren Wert nur aus dem unmittelbar folgenden Absatz ziehen.
                    flush()
                    block = next(blocks)
                    walk(child, emphasis + (1 if child.name in HEADING_TAGS else 0))
                    flush()
                    block = next(blocks)

    walk(root, 0)
    flush()
    return lines


def is_usable_label(label: str) -> bool:
    if not label or len(label) > 30 or len(label.split()) > 3:
        return False
    # Ziffern im Label heisst fast immer Datum oder Uhrzeit ("Mi., 23.09.26, 19:00").
    return not any(char.isdigit() for char in label) and label.lower() not in LABEL_DENYLIST


def add_detail(found: dict[str, str], label: str, value: str) -> None:
    previous = found.get(label)
    if previous is None:
        found[label] = value
    elif value not in previous:
        # Manche Events führen dasselbe Label zweimal (z. B. zwei Prizepool-Zeilen).
        found[label] = f"{previous} · {value}"


def parse_details(lines: list[Line], title: str = "") -> tuple[dict[str, str], list[str]]:
    """Trennt "Label: Wert" und "Überschrift + Fliesstext" vom restlichen Text.

    Liefert (Felder, übriger Fliesstext) — sprachunabhängig, ohne feste Labels,
    damit auch andere Kategorien mit anderen Feldern funktionieren. `title`
    unterdrückt die Titelzeile der Detailseite, die sonst als Label gälte.
    """
    found: dict[str, str] = {}
    notes: list[str] = []
    normalised_title = clean_text(title).casefold()
    index = 0

    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.text or len(line.text) > 500:
            continue
        if normalised_title and line.text.casefold() == normalised_title:
            continue

        match = LABEL_RE.match(line.text)
        if match and is_usable_label(match.group(1).strip()):
            add_detail(found, match.group(1).strip(), match.group(2).strip())
            continue

        # Überschrift ("Preispool") — der folgende Fliesstext ist ihr Wert.
        label = line.text.rstrip(":").strip()
        if (line.heading or line.text.endswith(":")) and is_usable_label(label):
            value: list[str] = []
            value_block: int | None = None
            while index < len(lines):
                following = lines[index]
                if following.heading or LABEL_RE.match(following.text):
                    break
                if value_block is None:
                    value_block = following.block
                elif following.block != value_block:
                    break
                value.append(following.text)
                index += 1
            if value:
                add_detail(found, label, " ".join(value))
                continue

        notes.append(line.text)

    return found, notes


def modal_candidate_ids(card: Tag) -> list[str]:
    ids: list[str] = []
    for element in card.find_all(True):
        for attribute in ("data-bs-target", "data-target", "href"):
            value = str(element.get(attribute) or "")
            if value.startswith("#") and len(value) > 1:
                modal_id = value[1:]
                if modal_id not in ids:
                    ids.append(modal_id)
    return ids


def find_modal(soup: BeautifulSoup, card: Tag, slot_id: str = "") -> Tag | None:
    nested = card.find("div", class_="modal")
    if isinstance(nested, Tag):
        return nested

    for modal_id in modal_candidate_ids(card):
        modal = soup.find(id=modal_id)
        if isinstance(modal, Tag):
            return modal

    if slot_id:
        modal = soup.find(id=re.compile(rf"\b{re.escape(slot_id)}\b"))
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


def fetch_event_details(event: Event, request_config: dict[str, Any], selectors: list[str]) -> None:
    """Holt die Detailseite eines Events — nicht jede Karte hat ein Modal."""
    html = fetch_html(event.booking_url, request_config)
    soup = BeautifulSoup(html, "html.parser")

    container: Tag | None = None
    for selector in selectors:
        container = soup.select_one(selector)
        if container is not None:
            break
    if container is None:
        container = soup.body or soup

    lines = extract_lines(container)
    event.details, event.notes = parse_details(lines, event.title)
    LOG.debug("Detailseite '%s': Felder=%s Notizen=%s", event.title, event.details, event.notes[:3])
    if not event.details:
        LOG.debug("  keine Felder erkannt, Zeilen: %s", [line.text for line in lines][:20])


def parse_events(html: str, page_url: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")

    cards: list[Tag] = []
    for container in soup.select("div.netzp-events6-list"):
        cards.extend(container.select("div.events-card"))
    if not cards:
        cards = soup.select("div.events-card")

    if not cards and LOG.isEnabledFor(logging.DEBUG):
        classes = {
            css_class
            for tag in soup.find_all(True)
            for css_class in (tag.get("class") or [])
            if "event" in css_class.lower()
        }
        LOG.debug("Keine Event-Karten gefunden; Klassen mit 'event': %s", sorted(classes)[:20])

    events: list[Event] = []
    seen_ids: set[str] = set()
    for card in cards:
        event = parse_card(soup, card, page_url)
        # Dieselbe Karte taucht bei verschachtelten Listen mehrfach auf.
        if event is not None and event.event_id not in seen_ids:
            seen_ids.add(event.event_id)
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

    slot_match = re.search(r"slotId=(\w+)", booking_url)
    slot_id = slot_match.group(1) if slot_match else ""

    details: dict[str, str] = {}
    notes: list[str] = []
    modal = find_modal(soup, card, slot_id)
    if modal is not None:
        body = modal.select_one(".modal-body") or modal
        modal_lines = extract_lines(body)
        details, notes = parse_details(modal_lines, title)
        LOG.debug("Modal '%s' für '%s': Felder=%s", modal.get("id"), title, details)
    else:
        LOG.debug("Kein Modal für '%s' — Detailseite wird nachgeladen", title)

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
        notes=notes,
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


def embed_digest(embed: dict[str, Any]) -> str:
    """Kurzer Fingerabdruck des Inhalts — ändert er sich, wird die Nachricht aktualisiert."""
    return hashlib.sha1(embed.get("description", "").encode("utf-8")).hexdigest()[:16]


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
        if key not in used_keys and key.lower() not in CARD_LABELS:
            lines.append(f"**{key}:** {value}")

    if event.seats:
        seats_number = re.match(r"(\d+)", event.seats)
        seats_text = f"{seats_number.group(1)} verfügbar" if seats_number else event.seats
        lines.append(f"**Plätze:** {seats_text}")

    text_parts: list[str] = []
    for part in [event.summary, *event.notes]:
        if part and part.lower() not in " ".join(text_parts).lower():
            text_parts.append(part)
    free_text = "\n".join(text_parts)
    if free_text:
        lines.append(f"\n{free_text if len(free_text) <= 600 else free_text[:597] + '...'}")

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


def discord_request(method: str, url: str, payload: dict[str, Any], timeout: int = 30) -> requests.Response:
    for attempt in range(1, 4):
        response = requests.request(method, url, json=payload, timeout=timeout)
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
        return response
    raise RuntimeError(f"Discord-Anfrage nach mehreren Versuchen fehlgeschlagen: {method} {url}")


def post_embed(webhook_url: str, embed: dict[str, Any], username: str) -> str:
    """Postet das Embed und liefert die Message-ID (für spätere Updates)."""
    payload: dict[str, Any] = {"embeds": [embed]}
    if username:
        payload["username"] = username

    separator = "&" if "?" in webhook_url else "?"
    response = discord_request("POST", f"{webhook_url}{separator}wait=true", payload)
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Discord lehnte den Post ab ({response.status_code}): {response.text[:300]}")

    try:
        return str(response.json().get("id", ""))
    except ValueError:
        return ""


def edit_embed(webhook_url: str, message_id: str, embed: dict[str, Any]) -> bool:
    """Aktualisiert eine bereits gepostete Nachricht. False = Nachricht gibt es nicht mehr."""
    base = webhook_url.split("?")[0].rstrip("/")
    response = discord_request("PATCH", f"{base}/messages/{message_id}", {"embeds": [embed]})
    if response.status_code == 404:
        LOG.warning("Nachricht %s existiert nicht mehr — wird nicht weiter aktualisiert", message_id)
        return False
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Discord lehnte das Update ab ({response.status_code}): {response.text[:300]}")
    return True


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
    report: list[str] | None = None,
) -> int:
    key = category["key"]
    LOG.info("[%s] Hole %s", key, category["url"])

    request_config = config.get("request", {})
    html = fetch_html(category["url"], request_config)
    events = parse_events(html, category["url"])
    LOG.info("[%s] %s Event(s) auf der Seite gefunden", key, len(events))

    detail_config = config.get("detail_pages", {})
    if detail_config.get("enabled", True):
        selectors = detail_config.get("selectors", ["main"])
        pause = float(detail_config.get("delay_between_requests", 1.0))
        for event in events:
            if event.details or not event.booking_url:
                continue
            try:
                fetch_event_details(event, request_config, selectors)
                time.sleep(pause)
            except Exception as error:  # ohne Detailseite bleibt wenigstens die Karte
                LOG.warning("[%s] Detailseite für '%s' nicht lesbar: %s", key, event.title, error)

    if not events:
        LOG.warning("[%s] Keine Events geparst — HTML-Struktur womöglich geändert", key)

    category_state = state.setdefault("categories", {}).setdefault(key, {})
    seen: dict[str, Any] = category_state.setdefault("seen", {})

    if args.reset:
        LOG.warning("[%s] Reset: %s gemerkte(s) Event(s) werden vergessen", key, len(seen))
        if not args.dry_run:
            seen.clear()
            category_state["initialized"] = False

    first_run = not category_state.get("initialized", False)

    new_events = [event for event in events if event.event_id not in seen]
    LOG.info("[%s] %s davon neu", key, len(new_events))

    webhook_url = os.environ.get(category["webhook_env"], "").strip()
    should_post = bool(new_events) and (not first_run or args.post_existing)

    if first_run and not args.post_existing:
        LOG.info("[%s] Erster Lauf — %s Event(s) werden nur als bekannt gespeichert", key, len(new_events))
    elif should_post and not webhook_url and not args.dry_run:
        LOG.error("[%s] %s nicht gesetzt — es wird nichts gepostet", key, category["webhook_env"])
        should_post = False

    if report is not None:
        for event in events:
            fields = ", ".join(event.details) or "KEINE FELDER"
            report.append(f"[{key}] {event.title} -> {fields}")

    discord_config = config.get("discord", {})
    delay = float(discord_config.get("delay_between_posts", 1.5))
    username = discord_config.get("username", "")
    locations = config.get("locations", {})

    posted: list[tuple[Event, str]] = []
    if should_post:
        limit = args.limit or int(discord_config.get("max_posts_per_run", 25))

        for event in new_events[:limit]:
            embed = build_embed(event, category, locations)
            message_id = ""
            if args.dry_run:
                LOG.info("[%s] DRY-RUN Embed:\n%s", key, json.dumps(embed, indent=2, ensure_ascii=False))
            else:
                message_id = post_embed(webhook_url, embed, username)
                LOG.info("[%s] Gepostet: %s", key, event.title)
                time.sleep(delay)
            posted.append((event, message_id))

        if len(new_events) > limit:
            LOG.warning(
                "[%s] %s weitere neue Events werden erst im nächsten Lauf gepostet",
                key,
                len(new_events) - limit,
            )

    # Bereits gepostete Events: geänderte Angaben (z. B. freie Plätze) ins bestehende
    # Embed nachtragen, statt dieselbe Veranstaltung nochmals zu posten.
    if discord_config.get("update_existing", True) and webhook_url and not first_run:
        for event in events:
            record = seen.get(event.event_id)
            if not isinstance(record, dict):
                continue
            message_id = record.get("message_id", "")
            embed = build_embed(event, category, locations)
            digest = embed_digest(embed)
            if not message_id or record.get("digest") == digest:
                continue
            if args.dry_run:
                LOG.info("[%s] DRY-RUN Update: %s", key, event.title)
                continue
            if edit_embed(webhook_url, message_id, embed):
                LOG.info("[%s] Aktualisiert: %s (%s)", key, event.title, event.seats or "Angaben geändert")
                record["digest"] = digest
            else:
                record["message_id"] = ""
            time.sleep(delay)

    if args.dry_run:
        LOG.info("[%s] DRY-RUN — state.json bleibt unverändert", key)
        return len(posted)

    # Nur der Erstlauf merkt sich alles ungesehen; sonst gilt ein Event erst als bekannt,
    # wenn es wirklich gepostet wurde — sonst ginge es bei fehlendem Webhook oder
    # erreichtem Limit stillschweigend verloren.
    if first_run and not args.post_existing:
        remembered = [(event, "") for event in events]
    else:
        remembered = posted

    for event, message_id in remembered:
        seen[event.event_id] = {
            "title": event.title,
            "date": event.date_text,
            "message_id": message_id,
            "digest": embed_digest(build_embed(event, category, locations)),
        }
    category_state["initialized"] = True
    category_state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return len(posted)


def select_categories(config: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Aktiv ist, wofür ein Webhook-Secret hinterlegt ist.

    Damit genügt es, das Secret im Repo zu setzen, um eine Kategorie in Betrieb
    zu nehmen — `"enabled": false` in der config.json schaltet sie bei Bedarf
    trotzdem ab.
    """
    categories = config.get("categories", [])

    if args.category:
        wanted = set(args.category)
        selected = [category for category in categories if category["key"] in wanted]
        missing = wanted - {category["key"] for category in selected}
        if missing:
            LOG.error("Unbekannte Kategorie(n): %s", ", ".join(sorted(missing)))
        return selected

    if args.include_inactive:
        return list(categories)

    selected = []
    for category in categories:
        if not category.get("enabled", True):
            LOG.info("[%s] In config.json abgeschaltet", category["key"])
        elif os.environ.get(category["webhook_env"], "").strip():
            selected.append(category)
        else:
            LOG.info("[%s] Kein %s hinterlegt — übersprungen", category["key"], category["webhook_env"])
    return selected


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
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Gemerkte Events der gewählten Kategorien vergessen (z. B. beim Kanalwechsel)",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Auch Kategorien ohne hinterlegtes Webhook-Secret verarbeiten (für Testläufe)",
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
    report: list[str] | None = [] if args.dry_run else None
    for category in categories:
        try:
            total_posted += process_category(category, config, state, args, report)
        except Exception as error:  # eine kaputte Kategorie darf die anderen nicht stoppen
            failed.append(category["key"])
            LOG.exception("[%s] Fehler: %s", category["key"], error)

    if not args.dry_run:
        save_state(args.state, state)

    if report:
        LOG.info("Übersicht des Probelaufs:\n%s", "\n".join(report))

    LOG.info(
        "Fertig: %s Event(s) gepostet, %s/%s Kategorie(n) ok",
        total_posted,
        len(categories) - len(failed),
        len(categories),
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
