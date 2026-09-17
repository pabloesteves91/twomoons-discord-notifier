"""Parser- und Embed-Tests gegen ein gespeichertes Abbild der Event-Seite."""

import json
import sys
import unittest
from argparse import Namespace
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


class StateTest(unittest.TestCase):
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

    def test_failing_category_does_not_block_others(self):
        state = {"categories": {}}
        args = Namespace(
            dry_run=False, post_existing=False, reset=False, limit=0, category=None, verbose=False
        )
        with mock.patch.object(notifier, "fetch_html", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                notifier.process_category(MAGIC, CONFIG, state, args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
