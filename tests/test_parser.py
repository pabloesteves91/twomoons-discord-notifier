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
        self.assertEqual([event.title for event in events], ["MTG The Hobbit Draft", "Commander Abend"])

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

    def test_event_id_falls_back_to_title_and_date(self):
        event = parse()[1]
        self.assertEqual(event.booking_url, "")
        self.assertEqual(event.event_id, "Commander Abend|Mittwoch, 26. November 2025, 18:30 Uhr")


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
                "**Plätze:** 12 Plätze verfügbar",
                "",
                "Draft mit Karten aus dem Herrn-der-Ringe-Universum.",
                "",
                "**Link:** [Zur Buchung](https://www.twomoons.ch/events/anmeldung/?slotId=98765)",
            ]
        )
        self.assertEqual(embed["description"], expected)
        self.assertEqual(embed["title"], "MTG The Hobbit Draft")
        self.assertEqual(embed["thumbnail"]["url"], "https://www.twomoons.ch/media/events/hobbit-draft.jpg")

    def test_event_without_booking_link_uses_category_page(self):
        embed = notifier.build_embed(parse()[1], MAGIC, CONFIG["locations"])
        self.assertIn(f"**Link:** [Zu den Events]({MAGIC['url']})", embed["description"])
        self.assertIn("weinfelden/) Marktstrasse 3 8570 Weinfelden", embed["description"])
        self.assertIn("**Mitbringen:** eigenes Deck", embed["description"])
        self.assertEqual(embed["url"], MAGIC["url"])


class StateTest(unittest.TestCase):
    def run_category(self, state, **flags):
        defaults = {"dry_run": False, "post_existing": False, "limit": 0, "category": None, "verbose": False}
        args = Namespace(**{**defaults, **flags})
        with mock.patch.object(notifier, "fetch_html", return_value=FIXTURE), mock.patch.object(
            notifier, "post_embed"
        ) as post, mock.patch.object(notifier.time, "sleep"):
            posted = notifier.process_category(MAGIC, CONFIG, state, args)
        return posted, post

    def test_first_run_seeds_without_posting(self):
        state = {"categories": {}}
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_MAGIC": "https://example.invalid/hook"}):
            posted, post = self.run_category(state)
        self.assertEqual(posted, 0)
        post.assert_not_called()
        self.assertEqual(len(state["categories"]["magic"]["seen"]), 2)
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
        self.assertEqual(posted, 2)
        self.assertEqual(post.call_count, 2)

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

    def test_failing_category_does_not_block_others(self):
        state = {"categories": {}}
        args = Namespace(dry_run=False, post_existing=False, limit=0, category=None, verbose=False)
        with mock.patch.object(notifier, "fetch_html", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                notifier.process_category(MAGIC, CONFIG, state, args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
