"""Parser- und Embed-Tests gegen ein gespeichertes Abbild der Event-Seite."""

import json
import sys
import unittest
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notifier  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PAGE_URL = "https://www.twomoons.ch/events/magic-the-gathering-events/"
FIXTURE = (REPO / "tests/fixtures/magic_events.html").read_text(encoding="utf-8")
CONFIG = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
MAGIC = next(category for category in CONFIG["categories"] if category["key"] == "magic")


def parse():
    return notifier.parse_events(FIXTURE, PAGE_URL)


class ParserTest(unittest.TestCase):
    def test_parses_all_cards(self):
        events = parse()
        self.assertEqual(
            [event.title for event in events],
            ["MTG The Hobbit Draft", "MTG Modern SUL District", "MTG Modern Weekly", "Commander Abend"],
        )

    def test_card_fields(self):
        event = parse()[0]
        self.assertEqual(event.date_text, "Freitag, 21. November 2025, 19:00 Uhr")
        self.assertEqual(event.location_name, "TwoMoons - Zürichstrasse 137")
        self.assertEqual(event.location_address, "Zürichstrasse 137 8600 Dübendorf")
        self.assertEqual(event.seats, "12 Plätze verfügbar")
        self.assertEqual(event.booking_url, "https://www.twomoons.ch/events/anmeldung/?slotId=98765")
        self.assertEqual(event.image_url, "https://www.twomoons.ch/media/events/hobbit-draft.jpg")
        self.assertEqual(event.summary, "Draft mit Karten aus dem Herrn-der-Ringe-Universum.")

    def test_modal_labels_are_parsed_generically(self):
        event = parse()[0]
        self.assertEqual(event.details["Format"], "Draft")
        self.assertEqual(event.details["Entry Fee"], "CHF 18")
        self.assertEqual(event.details["SUL"], "League")
        self.assertEqual(event.details["Tournament System"], "Swiss Rounds")
        self.assertEqual(event.details["Prizepool"], "Es wird einen Backdraft von Rares und Mythics geben")
        self.assertNotIn("Magic", event.details)

    def test_bold_labels_inside_modal_are_parsed(self):
        event = parse()[1]
        self.assertEqual(event.details["Format"], "Modern")
        self.assertEqual(event.details["Entry Fee"], "CHF 20")
        self.assertEqual(event.details["SUL"], "District")
        self.assertEqual(
            event.details["Tournament System"],
            "Swiss Rounds with top 4/8 (9-16 players/17+ players)",
        )

    def test_repeated_label_keeps_both_values(self):
        event = parse()[1]
        self.assertEqual(
            event.details["Prizepool"],
            "Moons · Entry Fee - Costs go into the prizepool. "
            "They will be distributed among the players with more wins than losses.",
        )

    def test_heading_with_prose_becomes_a_field(self):
        event = parse()[2]
        self.assertEqual(event.details["Eintritt"], "CHF 15.00")
        self.assertEqual(event.details["SUL"], "League (Infos zu League)")
        self.assertEqual(
            event.details["Preispool"],
            "Pro Person gehen 14 CHF als Moons in den Preispool. "
            "Ab 7 erreichten Punkten erhältst du Moons.",
        )

    def test_unlabelled_prose_is_kept_as_notes(self):
        event = parse()[2]
        self.assertEqual(
            event.notes,
            [
                "Weekly Magic the Gathering Turnier im TwoMoons - Zürichstrasse 137a "
                "direkt am Bahnhof Stettbach.",
                "4 Runden werden gespielt, 45 Minuten pro Runde.",
            ],
        )

    def test_date_line_is_not_mistaken_for_a_field(self):
        # "Mi., 23.09.26, 19:00 - 22:30" sieht aus wie "Label: Wert", ist aber ein Datum.
        lines = [notifier.Line(text="Mi., 23.09.26, 19:00 - 22:30", heading=False, block=0)]
        details, notes = notifier.parse_details(lines)
        self.assertEqual(details, {})
        self.assertEqual(notes, ["Mi., 23.09.26, 19:00 - 22:30"])

    def test_duplicate_cards_are_collapsed(self):
        doubled = FIXTURE.replace("</body>", FIXTURE.split("<body>")[1].split("</body>")[0] + "</body>")
        self.assertEqual(len(notifier.parse_events(doubled, PAGE_URL)), len(parse()))

    def test_modal_buttons_are_ignored(self):
        event = parse()[2]
        self.assertNotIn("Schliessen", " ".join(event.notes))
        self.assertNotIn("Schliessen", " ".join(event.details.values()))

    def test_event_id_falls_back_to_title_and_date(self):
        event = parse()[3]
        self.assertEqual(event.booking_url, "")
        self.assertEqual(event.event_id, "Commander Abend|Mittwoch, 26. November 2025, 18:30 Uhr")


DETAIL_PAGE = """
<html><body>
  <nav><a href="/">Startseite</a><a href="/events/">Events</a></nav>
  <main>
    <h1>MTG Modern Weekly</h1>
    <p>Weekly Magic the Gathering Turnier im TwoMoons.</p>
    <p><b>Eintritt:</b> CHF 15.00<br><b>SUL:</b> League</p>
    <h3>Preispool</h3>
    <p>Pro Person gehen 14 CHF als Moons in den Preispool.</p>
    <button>In den Warenkorb</button>
  </main>
  <footer>Impressum | AGB</footer>
</body></html>
"""


class DetailPageTest(unittest.TestCase):
    def test_details_are_loaded_from_the_event_page(self):
        event = notifier.Event(event_id="x", title="MTG Modern Weekly", booking_url="https://example.invalid/e")
        with mock.patch.object(notifier, "fetch_html", return_value=DETAIL_PAGE):
            notifier.fetch_event_details(event, {}, [".cms-page", "main"])

        self.assertEqual(event.details["Eintritt"], "CHF 15.00")
        self.assertEqual(event.details["SUL"], "League")
        self.assertEqual(event.details["Preispool"], "Pro Person gehen 14 CHF als Moons in den Preispool.")
        self.assertEqual(event.notes, ["Weekly Magic the Gathering Turnier im TwoMoons."])

    def test_shop_price_fills_in_a_missing_entry_fee(self):
        page = """
        <html><body>
          <main><p><b>SUL:</b> League</p></main>
          <div class="product-detail-price">CHF 15.00</div>
        </body></html>
        """
        event = notifier.Event(event_id="x", title="T", booking_url="https://example.invalid/e")
        with mock.patch.object(notifier, "fetch_html", return_value=page):
            notifier.fetch_event_details(event, {}, ["main"], [".product-detail-price"])
        self.assertEqual(event.details["Eintritt"], "CHF 15.00")

    def test_stated_entry_fee_beats_the_shop_price(self):
        page = """
        <html><body>
          <main><p><b>Entry Fee:</b> CHF 55</p></main>
          <div class="product-detail-price">CHF 15.00</div>
        </body></html>
        """
        event = notifier.Event(event_id="x", title="T", booking_url="https://example.invalid/e")
        with mock.patch.object(notifier, "fetch_html", return_value=page):
            notifier.fetch_event_details(event, {}, ["main"], [".product-detail-price"])
        self.assertEqual(event.details, {"Entry Fee": "CHF 55"})

    def test_html_comments_are_not_content(self):
        page = """
        <html><body><main>
          <!-- @deprecated tag:v6.8.0 - block will be moved into buy-widget.html.twig -->
          <p><b>Format:</b> Modern</p>
        </main></body></html>
        """
        event = notifier.Event(event_id="x", title="Test", booking_url="https://example.invalid/e")
        with mock.patch.object(notifier, "fetch_html", return_value=page):
            notifier.fetch_event_details(event, {}, ["main"])
        self.assertEqual(event.details, {"Format": "Modern"})
        self.assertEqual(event.notes, [])

    def test_navigation_and_footer_are_ignored(self):
        event = notifier.Event(event_id="x", title="MTG Modern Weekly", booking_url="https://example.invalid/e")
        with mock.patch.object(notifier, "fetch_html", return_value=DETAIL_PAGE):
            notifier.fetch_event_details(event, {}, ["main"])

        everything = " ".join([*event.notes, *event.details.values()])
        for unwanted in ("Startseite", "Impressum", "AGB", "Warenkorb"):
            self.assertNotIn(unwanted, everything)


class EmbedTest(unittest.TestCase):
    def test_hobbit_embed_matches_target_format(self):
        embed = notifier.build_embed(parse()[0], MAGIC, CONFIG["locations"])
        expected = "\n".join(
            [
                "**Datum:** Freitag, 21. November 2025, 19:00 Uhr",
                "**Format:** Draft",
                "**Eintritt:** CHF 18",
                "**Ort:** [TwoMoons - Zürichstrasse 137]"
                "(https://www.twomoons.ch/twomoons/standort-oeffnungszeiten/stettbach/) direkt am Bahnhof Stettbach",
                "**SUL:** League",
                "**Turniersystem:** Swiss Rounds",
                "**Preispool:** Es wird einen Backdraft von Rares und Mythics geben",
                "**Includes:** 3 Booster",
                "**Plätze:** 12 verfügbar",
                "",
                "Draft mit Karten aus dem Herrn-der-Ringe-Universum.",
                "Magic: The Gathering Draft-Abend in Stettbach.",
                "",
                "**Link:** [Zur Buchung](https://www.twomoons.ch/events/anmeldung/?slotId=98765)",
            ]
        )
        self.assertEqual(embed["description"], expected)
        self.assertEqual(embed["title"], "MTG The Hobbit Draft")
        self.assertEqual(embed["thumbnail"]["url"], "https://www.twomoons.ch/media/events/hobbit-draft.jpg")

    def test_modern_sul_embed_contains_all_modal_fields(self):
        embed = notifier.build_embed(parse()[1], MAGIC, CONFIG["locations"])
        expected = "\n".join(
            [
                "**Datum:** Sa., 19.09.26, 11:00 - 20:00",
                "**Format:** Modern",
                "**Eintritt:** CHF 20",
                "**Ort:** [TwoMoons Stettbach]"
                "(https://www.twomoons.ch/twomoons/standort-oeffnungszeiten/stettbach/) direkt am Bahnhof Stettbach",
                "**SUL:** District",
                "**Turniersystem:** Swiss Rounds with top 4/8 (9-16 players/17+ players)",
                "**Preispool:** Moons · Entry Fee - Costs go into the prizepool. "
                "They will be distributed among the players with more wins than losses.",
                "**Plätze:** 61 verfügbar",
                "",
                "**Link:** [Zur Buchung](https://www.twomoons.ch/events/anmeldung/?slotId=54321)",
            ]
        )
        self.assertEqual(embed["description"], expected)
        # "Place" steht schon als Ort auf der Karte und darf nicht doppelt erscheinen.
        self.assertNotIn("**Place:**", embed["description"])

    def test_modern_weekly_embed_keeps_fields_and_prose(self):
        embed = notifier.build_embed(parse()[2], MAGIC, CONFIG["locations"])
        expected = "\n".join(
            [
                "**Datum:** Mi., 23.09.26, 19:00 - 22:30",
                "**Eintritt:** CHF 15.00",
                "**Ort:** [TwoMoons Stettbach]"
                "(https://www.twomoons.ch/twomoons/standort-oeffnungszeiten/stettbach/) direkt am Bahnhof Stettbach",
                "**SUL:** League (Infos zu League)",
                "**Preispool:** Pro Person gehen 14 CHF als Moons in den Preispool. "
                "Ab 7 erreichten Punkten erhältst du Moons.",
                "**Plätze:** 24 verfügbar",
                "",
                "Weekly Magic the Gathering Turnier im TwoMoons - Zürichstrasse 137a "
                "direkt am Bahnhof Stettbach.",
                "4 Runden werden gespielt, 45 Minuten pro Runde.",
                "",
                "**Link:** [Zur Buchung](https://www.twomoons.ch/mtg-weekly-entry-modern?slotId=019f193b)",
            ]
        )
        self.assertEqual(embed["description"], expected)

    def test_event_without_booking_link_uses_category_page(self):
        embed = notifier.build_embed(parse()[3], MAGIC, CONFIG["locations"])
        self.assertIn(f"**Link:** [Zu den Events]({MAGIC['url']})", embed["description"])
        self.assertIn("weinfelden/) Marktstrasse 3 8570 Weinfelden", embed["description"])
        self.assertIn("**Mitbringen:** eigenes Deck", embed["description"])
        self.assertEqual(embed["url"], MAGIC["url"])


FROZEN_NOW = datetime(2026, 9, 17, 10, 0, tzinfo=notifier.LOCAL_ZONE)


class StateTest(unittest.TestCase):
    def setUp(self):
        # Die Fixture-Termine liegen im September 2026; "jetzt" muss davor liegen,
        # sonst fallen sie aus dem 7-Tage-Fenster.
        patcher = mock.patch.object(notifier, "local_now", return_value=FROZEN_NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_category(self, state, html=FIXTURE, **flags):
        defaults = {
            "dry_run": False,
            "post_existing": False,
            "reset": False,
            "limit": 0,
            "category": None,
            "verbose": False,
        }
        args = Namespace(**{**defaults, **flags})
        with mock.patch.object(notifier, "fetch_html", return_value=html), mock.patch.object(
            notifier, "post_embed", return_value="msg-1"
        ) as post, mock.patch.object(notifier, "edit_embed", return_value=True) as edit, mock.patch.object(
            notifier.time, "sleep"
        ):
            posted = notifier.process_category(MAGIC, CONFIG, state, args)
        self.edit = edit
        return posted, post

    def test_first_run_seeds_without_posting(self):
        state = {"categories": {}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            posted, post = self.run_category(state)
        self.assertEqual(posted, 0)
        post.assert_not_called()
        self.assertEqual(len(state["categories"]["magic"]["seen"]), 4)
        self.assertTrue(state["categories"]["magic"]["initialized"])

    def test_second_run_posts_only_new_events(self):
        state = {"categories": {}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            self.run_category(state)
            del state["categories"]["magic"]["seen"]["Commander Abend|Mittwoch, 26. November 2025, 18:30 Uhr"]
            posted, post = self.run_category(state)
        self.assertEqual(posted, 1)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[1]["title"], "Commander Abend")

    def test_post_existing_posts_everything_on_first_run(self):
        state = {"categories": {}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            posted, post = self.run_category(state, post_existing=True)
        self.assertEqual(posted, 4)
        self.assertEqual(post.call_count, 4)
        self.assertEqual(
            state["categories"]["magic"]["seen"][parse()[0].event_id]["message_id"], "msg-1"
        )

    def test_name_and_avatar_are_sent_with_the_post(self):
        payloads = []

        def capture(method, url, payload, timeout=30):
            payloads.append(payload)
            response = mock.Mock(status_code=200)
            response.json.return_value = {"id": "msg-1"}
            return response

        with mock.patch.object(notifier, "discord_request", side_effect=capture):
            notifier.post_embed("https://example.invalid/hook", {"title": "T"}, "TwoMoons Events", "https://example.invalid/avatar.png")

        self.assertEqual(payloads[0]["username"], "TwoMoons Events")
        self.assertEqual(payloads[0]["avatar_url"], "https://example.invalid/avatar.png")

    def test_missing_webhook_keeps_events_new(self):
        state = {"categories": {"magic": {"seen": {}, "initialized": True}}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": ""}):
            posted, post = self.run_category(state)
        self.assertEqual(posted, 0)
        post.assert_not_called()
        # Nichts als bekannt markieren, sonst wären die Events nach dem Nachtragen
        # des Secrets für immer verloren.
        self.assertEqual(state["categories"]["magic"]["seen"], {})

    def test_limit_keeps_unposted_events_new(self):
        state = {"categories": {"magic": {"seen": {}, "initialized": True}}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            posted, post = self.run_category(state, limit=1)
        self.assertEqual(posted, 1)
        self.assertEqual(list(state["categories"]["magic"]["seen"]), [parse()[0].event_id])

    def test_changed_seats_edit_the_existing_message(self):
        state = {"categories": {}}
        changed = FIXTURE.replace("61 Pl&auml;tze verf&uuml;gbar", "47 Pl&auml;tze verf&uuml;gbar")
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            self.run_category(state, post_existing=True)
            posted, post = self.run_category(state, html=changed)

        # Kein neuer Post — die bestehende Nachricht wird bearbeitet.
        self.assertEqual(posted, 0)
        post.assert_not_called()
        self.assertEqual(self.edit.call_count, 1)
        webhook, message_id, embed = self.edit.call_args.args
        self.assertEqual(message_id, "msg-1")
        self.assertIn("**Plätze:** 47 verfügbar", embed["description"])

    def test_changed_colour_updates_the_existing_message(self):
        # Farbe und Fusszeile stehen nicht im Text — sie müssen trotzdem nachgeführt werden.
        state = {"categories": {}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            self.run_category(state, post_existing=True)
            recoloured = json.loads(json.dumps(CONFIG))
            recoloured["categories"][0]["color"] = "#123456"
            args = Namespace(
                dry_run=False, post_existing=False, reset=False, limit=0, category=None, verbose=False
            )
            with mock.patch.object(notifier, "fetch_html", return_value=FIXTURE), mock.patch.object(
                notifier, "post_embed", return_value="msg-1"
            ) as post, mock.patch.object(notifier, "edit_embed", return_value=True) as edit, mock.patch.object(
                notifier.time, "sleep"
            ):
                notifier.process_category(recoloured["categories"][0], recoloured, state, args)

        post.assert_not_called()
        self.assertEqual(edit.call_count, 4)
        self.assertEqual(edit.call_args.args[2]["color"], 0x123456)

    def test_unchanged_events_are_not_edited(self):
        state = {"categories": {}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            self.run_category(state, post_existing=True)
            posted, post = self.run_category(state)
        self.assertEqual(posted, 0)
        post.assert_not_called()
        self.edit.assert_not_called()

    def test_seeded_events_without_message_id_are_not_edited(self):
        state = {"categories": {}}
        changed = FIXTURE.replace("61 Pl&auml;tze verf&uuml;gbar", "47 Pl&auml;tze verf&uuml;gbar")
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            self.run_category(state)  # stiller Erstlauf, keine Message-IDs
            self.run_category(state, html=changed)
        self.edit.assert_not_called()

    def test_reset_with_post_existing_reposts_into_new_channel(self):
        state = {"categories": {}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            self.run_category(state, post_existing=True)
            posted, post = self.run_category(state, reset=True, post_existing=True)
        self.assertEqual(posted, 4)
        self.assertEqual(post.call_count, 4)
        self.edit.assert_not_called()

    def test_reset_without_post_existing_seeds_silently(self):
        state = {"categories": {}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            self.run_category(state, post_existing=True)
            posted, post = self.run_category(state, reset=True)
        self.assertEqual(posted, 0)
        post.assert_not_called()
        self.assertEqual(len(state["categories"]["magic"]["seen"]), 4)

    def test_reset_in_dry_run_keeps_state(self):
        state = {"categories": {}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            self.run_category(state, post_existing=True)
            before = dict(state["categories"]["magic"]["seen"])
            self.run_category(state, reset=True, dry_run=True)
        self.assertEqual(state["categories"]["magic"]["seen"], before)

    def cleanup_state(self, date_text):
        return {
            "categories": {
                "magic": {
                    "initialized": True,
                    "seen": {
                        "slot-vorbei": {
                            "title": "Altes Event",
                            "date": date_text,
                            "message_id": "msg-alt",
                            "digest": "x",
                        }
                    },
                }
            }
        }

    def run_cleanup(self, state, **flags):
        defaults = {
            "dry_run": False,
            "post_existing": False,
            "reset": False,
            "limit": 0,
            "category": None,
            "verbose": False,
        }
        args = Namespace(**{**defaults, **flags})
        with mock.patch.object(notifier, "fetch_html", return_value=FIXTURE), mock.patch.object(
            notifier, "post_embed", return_value="msg-1"
        ), mock.patch.object(notifier, "edit_embed", return_value=True), mock.patch.object(
            notifier, "delete_message", return_value=True
        ) as delete, mock.patch.object(notifier.time, "sleep"):
            notifier.process_category(MAGIC, CONFIG, state, args)
        return delete

    def test_past_event_is_deleted_after_the_grace_period(self):
        state = self.cleanup_state("Fr., 01.01.20, 18:00 - 22:00")
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            delete = self.run_cleanup(state)
        delete.assert_called_once()
        self.assertEqual(delete.call_args.args[1], "msg-alt")
        self.assertNotIn("slot-vorbei", state["categories"]["magic"]["seen"])

    def test_future_event_is_kept(self):
        state = self.cleanup_state("Fr., 31.12.99, 18:00 - 22:00")
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            delete = self.run_cleanup(state)
        delete.assert_not_called()
        self.assertIn("slot-vorbei", state["categories"]["magic"]["seen"])

    def test_event_still_listed_is_never_deleted(self):
        # Steht das Event trotz vergangenem Datum noch auf der Seite, bleibt es.
        listed_id = parse()[0].event_id
        state = {
            "categories": {
                "magic": {
                    "initialized": True,
                    "seen": {
                        listed_id: {
                            "title": "Noch gelistet",
                            "date": "Fr., 01.01.20, 18:00 - 22:00",
                            "message_id": "msg-alt",
                            "digest": notifier.embed_digest(
                                notifier.build_embed(parse()[0], MAGIC, CONFIG["locations"])
                            ),
                        }
                    },
                }
            }
        }
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            delete = self.run_cleanup(state)
        delete.assert_not_called()
        self.assertIn(listed_id, state["categories"]["magic"]["seen"])

    def test_dry_run_deletes_nothing(self):
        state = self.cleanup_state("Fr., 01.01.20, 18:00 - 22:00")
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            delete = self.run_cleanup(state, dry_run=True)
        delete.assert_not_called()
        self.assertIn("slot-vorbei", state["categories"]["magic"]["seen"])

    def test_failing_category_does_not_block_others(self):
        state = {"categories": {}}
        args = Namespace(
            dry_run=False, post_existing=False, reset=False, limit=0, category=None, verbose=False
        )
        with mock.patch.object(notifier, "fetch_html", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                notifier.process_category(MAGIC, CONFIG, state, args)


MAGIC_ID = MAGIC["event_category_id"]


def slot(slot_id, title, start, end, booking=True, seats="20 Plätze verfügbar"):
    return {
        "id": slot_id,
        "calendarId": "kalender-magic",
        "start": start,
        "end": end,
        "title": title,
        "location": "TwoMoons Stettbach / Zürichstrasse 137a 8600 Dübendorf",
        "body": " ",
        "attendees": [seats],
        "raw": {"bookingUrl": f"https://www.twomoons.ch/detail/p?slotId={slot_id}&only=1" if booking else ""},
    }


API_SLOTS = [
    slot("54321", "MTG Modern SUL District", "2026-09-19T11:00:00", "2026-09-19T20:00:00"),
    slot("weekly-a", "MTG Modern Weekly", "2026-09-21T19:00:00", "2026-09-21T22:30:00"),
    slot("019f193b", "MTG Modern Weekly", "2026-09-23T19:00:00", "2026-09-23T22:30:00"),
    slot("weekly-b", "MTG Modern Weekly", "2026-09-30T19:00:00", "2026-09-30T22:30:00"),
]


class FetchTest(unittest.TestCase):
    def test_missing_page_is_not_retried(self):
        response = mock.Mock(status_code=404)
        response.raise_for_status.side_effect = notifier.requests.HTTPError("404", response=response)
        with mock.patch.object(notifier.requests, "get", return_value=response) as get, mock.patch.object(
            notifier.time, "sleep"
        ) as sleep:
            with self.assertRaises(RuntimeError):
                notifier.fetch_html("https://example.invalid/weg", {"retries": 3})
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    def test_server_errors_are_retried(self):
        response = mock.Mock(status_code=503)
        response.raise_for_status.side_effect = notifier.requests.HTTPError("503", response=response)
        with mock.patch.object(notifier.requests, "get", return_value=response) as get, mock.patch.object(
            notifier.time, "sleep"
        ):
            with self.assertRaises(RuntimeError):
                notifier.fetch_html("https://example.invalid/kaputt", {"retries": 3})
        self.assertEqual(get.call_count, 3)


class DateWindowTest(unittest.TestCase):
    def test_start_and_end_of_a_single_day(self):
        text = "Mo., 05.10.26, 19:00 - 22:30"
        self.assertEqual(notifier.parse_event_start(text), datetime(2026, 10, 5, 19, 0, tzinfo=notifier.LOCAL_ZONE))
        self.assertEqual(notifier.parse_event_end(text), datetime(2026, 10, 5, 22, 30, tzinfo=notifier.LOCAL_ZONE))

    def test_start_and_end_of_a_multi_day_event(self):
        text = "Fr., 23.10.26, 18:00 - So., 25.10.26, 18:00"
        self.assertEqual(notifier.parse_event_start(text).date().isoformat(), "2026-10-23")
        self.assertEqual(notifier.parse_event_end(text).date().isoformat(), "2026-10-25")

    def test_slot_date_matches_the_website_format(self):
        start = datetime(2026, 10, 12, 18, 30)
        self.assertEqual(notifier.format_slot_date(start, datetime(2026, 10, 12, 22, 30)), "Mo., 12.10.26, 18:30 - 22:30")
        self.assertEqual(
            notifier.format_slot_date(datetime(2026, 10, 23, 18, 0), datetime(2026, 10, 25, 18, 0)),
            "Fr., 23.10.26, 18:00 - So., 25.10.26, 18:00",
        )

    def test_window_covers_the_next_seven_days(self):
        def at(text):
            return notifier.Event(event_id="x", title="T", date_text=text)

        self.assertTrue(notifier.in_window(at("Mi., 23.09.26, 19:00 - 22:30"), 7, FROZEN_NOW))
        self.assertTrue(notifier.in_window(at("Do., 24.09.26, 19:00 - 22:30"), 7, FROZEN_NOW))
        self.assertFalse(notifier.in_window(at("Fr., 25.09.26, 19:00 - 22:30"), 7, FROZEN_NOW))
        self.assertFalse(notifier.in_window(at("Mi., 16.09.26, 19:00 - 22:30"), 7, FROZEN_NOW))  # vorbei
        self.assertTrue(notifier.in_window(at("Freitag, 21. November 2025"), 7, FROZEN_NOW))  # unlesbar


class EventApiTest(unittest.TestCase):
    def test_slot_becomes_an_event_like_a_card(self):
        event = notifier.event_from_slot(API_SLOTS[1])
        self.assertEqual(event.event_id, "weekly-a")
        self.assertEqual(event.date_text, "Mo., 21.09.26, 19:00 - 22:30")
        self.assertEqual(event.location_name, "TwoMoons Stettbach")
        self.assertEqual(event.location_address, "Zürichstrasse 137a 8600 Dübendorf")
        self.assertEqual(event.seats, "20 Plätze verfügbar")
        self.assertTrue(event.booking_url.endswith("slotId=weekly-a&only=1"))

    def test_merge_prefers_cards_and_lets_siblings_inherit(self):
        merged = notifier.merge_with_slots(parse(), API_SLOTS)
        by_id = {event.event_id: event for event in merged}

        # Steht ein Termin als Karte auf der Seite, kommt die Karte mit ihren Angaben.
        self.assertEqual(by_id["54321"].details["Format"], "Modern")
        # Weitere Termine desselben Events übernehmen Bild und Angaben der Karte.
        sibling = by_id["weekly-b"]
        self.assertEqual(sibling.image_url, "https://www.twomoons.ch/media/events/modern-weekly.jpg")
        self.assertEqual(sibling.details["SUL"], "League (Infos zu League)")
        # Karten ohne Termin in der API gehen nicht verloren.
        self.assertIn("MTG The Hobbit Draft", {event.title for event in merged})
        self.assertIn("Commander Abend", {event.title for event in merged})
        self.assertEqual(len(merged), 6)

    def test_slots_are_grouped_by_category(self):
        calendars = [{"id": "kalender-magic", "categoryId": MAGIC_ID, "category": "Magic"}]
        responses = [json.dumps(API_SLOTS), json.dumps(calendars)]
        config = {"events_api": {"enabled": True, "slots_url": "s/{start}/{end}", "calendars_url": "c/{start}/{end}"}}
        with mock.patch.object(notifier, "local_now", return_value=FROZEN_NOW), mock.patch.object(
            notifier, "fetch_html", side_effect=responses
        ) as fetch:
            grouped = notifier.fetch_event_slots(config)
        self.assertEqual(len(grouped[MAGIC_ID]), 4)
        # Die Kalender-Zuordnung muss bis zum spätesten Termin reichen.
        self.assertEqual(fetch.call_args_list[1].args[0], "c/2026-09-17/2026-09-30")

    def test_unusable_api_falls_back_to_the_overview(self):
        config = {"events_api": {"enabled": True, "slots_url": "s/{start}/{end}", "calendars_url": "c/{start}/{end}"}}
        with mock.patch.object(notifier, "fetch_html", return_value="<html>Wartung</html>"):
            self.assertIsNone(notifier.fetch_event_slots(config))

    def test_old_state_keys_are_mapped_to_slot_ids(self):
        seen = {
            "https://www.twomoons.ch/mtg-weekly-entry-modern?slotId=weekly-a": {"title": "MTG Modern Weekly", "date": "x"},
            "Commander Night|Fr., 09.10.26, 18:00 - 22:00": {"title": "Commander Night", "date": "Fr., 09.10.26, 18:00 - 22:00"},
        }
        night = notifier.Event(event_id="slot-night", title="Commander Night", date_text="Fr., 09.10.26, 18:00 - 22:00")
        notifier.align_event_ids([night], seen)
        self.assertIn("weekly-a", seen)
        self.assertEqual(night.event_id, "Commander Night|Fr., 09.10.26, 18:00 - 22:00")


class WindowStateTest(unittest.TestCase):
    def run_at(self, state, now, **flags):
        defaults = {
            "dry_run": False,
            "post_existing": False,
            "reset": False,
            "limit": 0,
            "category": None,
            "verbose": False,
        }
        args = Namespace(**{**defaults, **flags})
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}), mock.patch.object(
            notifier, "local_now", return_value=now
        ), mock.patch.object(notifier, "fetch_html", return_value=FIXTURE), mock.patch.object(
            notifier, "fetch_event_details"
        ), mock.patch.object(
            notifier, "post_embed", side_effect=lambda hook, embed, *rest: f"msg-{embed['title']}-{embed['description'][:20]}"
        ) as post, mock.patch.object(notifier, "edit_embed", return_value=True) as edit, mock.patch.object(
            notifier, "delete_message", return_value=True
        ) as delete, mock.patch.object(notifier.time, "sleep"):
            notifier.process_category(MAGIC, CONFIG, state, args, None, {MAGIC_ID: API_SLOTS})
        return post, edit, delete

    def test_only_the_next_seven_days_are_posted(self):
        state = {"categories": {}}
        post, _, _ = self.run_at(state, FROZEN_NOW, post_existing=True)
        titles = sorted(call.args[1]["title"] for call in post.call_args_list)
        seen = state["categories"]["magic"]["seen"]
        # 19.09., 21.09., 23.09. plus zwei Karten ohne lesbares Datum — nicht der 30.09.
        self.assertEqual(post.call_count, 5)
        self.assertNotIn("weekly-b", seen)
        self.assertIn("weekly-a", seen)
        self.assertEqual(titles.count("MTG Modern Weekly"), 2)

    def test_later_event_is_posted_once_it_enters_the_window(self):
        state = {"categories": {}}
        self.run_at(state, FROZEN_NOW, post_existing=True)
        a_week_later = datetime(2026, 9, 24, 10, 0, tzinfo=notifier.LOCAL_ZONE)
        post, _, delete = self.run_at(state, a_week_later)
        self.assertEqual([call.args[1]["title"] for call in post.call_args_list], ["MTG Modern Weekly"])
        self.assertIn("weekly-b", state["categories"]["magic"]["seen"])
        delete.assert_not_called()

    def test_silent_first_run_only_remembers_the_window(self):
        state = {"categories": {}}
        post, _, _ = self.run_at(state, FROZEN_NOW)
        post.assert_not_called()
        seen = state["categories"]["magic"]["seen"]
        self.assertIn("weekly-a", seen)
        self.assertNotIn("weekly-b", seen)

    def test_posted_event_beyond_the_window_stays_and_is_updated(self):
        state = {"categories": {}}
        self.run_at(state, FROZEN_NOW, post_existing=True)
        seen = state["categories"]["magic"]["seen"]
        seen["weekly-b"] = {"title": "MTG Modern Weekly", "date": "Mi., 30.09.26, 19:00 - 22:30", "message_id": "alt", "digest": "x"}
        _, edit, delete = self.run_at(state, FROZEN_NOW)
        delete.assert_not_called()
        self.assertIn("alt", [call.args[1] for call in edit.call_args_list])


if __name__ == "__main__":
    unittest.main(verbosity=2)
