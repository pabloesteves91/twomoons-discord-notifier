# TwoMoons Discord Notifier

Prüft einmal täglich die Event-Seiten von [twomoons.ch](https://www.twomoons.ch) und postet
**neue** Events pro Themenkategorie in einen eigenen Discord-Kanal — per Webhook, ohne Bot,
ohne eigenen Server. Der tägliche Lauf passiert komplett über GitHub Actions.

Aktuell ist nur **Magic: The Gathering** aktiv. Die übrigen Kategorien (Pokémon, Star Wars
Unlimited, Yu-Gi-Oh, Lorcana, One Piece, Flesh & Blood, Riftbound, Unterhaltung) sind in
`config.json` bereits vorbereitet und lassen sich später mit einem Handgriff dazuschalten
(siehe [Schritt 4](#4-weitere-kategorien-aktivieren)).

## Wie es funktioniert

1. Für jede aktive Kategorie wird die Übersichtsseite geladen und jede Event-Karte geparst
   (Titel, Datum, Ort, freie Plätze, Buchungslink, Bild, Kurzbeschreibung sowie alle
   Zusatzinfos aus dem Detail-Modal, z. B. Format, Eintritt, SUL, Turniersystem, Preispool).
2. Jedes Event bekommt eine ID: den Buchungslink, oder — falls das Event keinen eigenen Link
   hat — `Titel|Datum`.
3. Alle bereits gesehenen IDs stehen in `state.json`. Nur Events, deren ID dort **noch nicht**
   steht, werden nach Discord gepostet.
4. Danach wird `state.json` vom Workflow automatisch zurück ins Repo committet, damit der
   Verlauf erhalten bleibt.

Beim allerersten Lauf einer Kategorie wird **nichts** gepostet — der aktuelle Stand wird nur
als „bekannt“ gespeichert, damit dein Kanal nicht mit dem gesamten Programm geflutet wird.
Wenn du das bewusst möchtest, gibt es dafür die Option `post_existing` (siehe Schritt 5).

So sieht eine Nachricht im Kanal aus:

> **MTG The Hobbit Draft**
> **Datum:** Freitag, 21. November 2025, 19:00 Uhr
> **Format:** Draft
> **Eintritt:** CHF 18
> **Ort:** [TwoMoons - Zürichstrasse 137](https://www.twomoons.ch/twomoons/standort-oeffnungszeiten/stettbach/) direkt am Bahnhof Stettbach
> **SUL:** League
> **Turniersystem:** Swiss Rounds
> **Preispool:** Es wird einen Backdraft von Rares und Mythics geben
> **Link:** [Zur Buchung](https://www.twomoons.ch/events/anmeldung/?slotId=…)

Nur Felder, die auf der Seite tatsächlich vorhanden sind, werden angezeigt. Die Labels werden
generisch nach dem Muster `Label: Wert` gelesen — taucht bei einer anderen Kategorie ein
zusätzliches Feld auf, landet es automatisch mit im Embed.

---

## 1. Discord-Webhook pro Kanal erstellen

Für jede Kategorie brauchst du einen Webhook in dem Kanal, in dem die Events landen sollen:

1. In Discord mit der Maus über den Ziel-Kanal fahren → **Zahnrad-Symbol** (Kanal bearbeiten).
2. Links auf **Integrationen** klicken.
3. **Webhooks** → **Neuer Webhook**.
4. Name und Avatar nach Wunsch setzen (z. B. „TwoMoons Magic“) und **Änderungen speichern**.
5. **Webhook-URL kopieren** — die brauchst du gleich in Schritt 2.

Wiederhole das später pro Kanal/Kategorie, die du dazuschalten willst.

> **Wichtig:** Die Webhook-URL ist ein Geheimnis. Wer sie hat, kann in deinen Kanal posten.
> Sie gehört **nicht** in `config.json` oder in einen Commit, sondern ausschliesslich in die
> GitHub Secrets. Falls eine URL doch einmal öffentlich wurde: in denselben Webhook-Einstellungen
> auf **Webhook löschen** bzw. neu erstellen und das Secret aktualisieren.

## 2. Webhook-URLs als GitHub Secrets hinterlegen

1. Im GitHub-Repo auf **Settings** → **Secrets and variables** → **Actions**.
2. **New repository secret**.
3. **Name** = der Name aus `config.json` (Feld `webhook_env`), **Secret** = die kopierte URL.
4. **Add secret**.

Zuordnung Kategorie → Secret-Name (so, wie es in `config.json` hinterlegt ist):

| Kategorie            | Secret-Name                   | Status        |
| -------------------- | ----------------------------- | ------------- |
| Magic: The Gathering | `DISCORD_WEBHOOK_MAGIC`       | aktiv         |
| Pokémon              | `DISCORD_WEBHOOK_POKEMON`     | vorbereitet   |
| Star Wars Unlimited  | `DISCORD_WEBHOOK_STAR_WARS`   | vorbereitet   |
| Yu-Gi-Oh!            | `DISCORD_WEBHOOK_YUGIOH`      | vorbereitet   |
| Lorcana              | `DISCORD_WEBHOOK_LORCANA`     | vorbereitet   |
| One Piece            | `DISCORD_WEBHOOK_ONE_PIECE`   | vorbereitet   |
| Flesh & Blood        | `DISCORD_WEBHOOK_FLESH_BLOOD` | vorbereitet   |
| Riftbound            | `DISCORD_WEBHOOK_RIFTBOUND`   | vorbereitet   |
| Unterhaltung         | `DISCORD_WEBHOOK_UNTERHALTUNG`| vorbereitet   |

Du musst nur Secrets für die Kategorien anlegen, die du auch aktivierst. Fehlt ein Secret zu
einer aktiven Kategorie, protokolliert der Lauf das und überspringt sie — die anderen laufen
normal weiter.

## 3. Kategorien in `config.json`

Kategorien sind frei konfigurierbar, nichts ist im Code fest verdrahtet:

```json
{
  "key": "magic",
  "name": "Magic: The Gathering",
  "url": "https://www.twomoons.ch/events/magic-the-gathering-events/",
  "webhook_env": "DISCORD_WEBHOOK_MAGIC",
  "color": "#E67E22",
  "enabled": true
}
```

| Feld          | Bedeutung                                                             |
| ------------- | --------------------------------------------------------------------- |
| `key`         | Kurzname, auch für `--category` / das Workflow-Feld `categories`       |
| `name`        | Anzeigename in der Embed-Fusszeile                                     |
| `url`         | Übersichtsseite; zugleich Fallback-Link, wenn ein Event keinen hat     |
| `webhook_env` | **Name** der Umgebungsvariable — nie die URL selbst                    |
| `color`       | Farbbalken des Embeds (Hex)                                            |
| `enabled`     | `false` = wird beim täglichen Lauf übersprungen                        |

Ausserdem in `config.json`:

- `locations` — Zuordnung Filiale → Standortseite und Zusatztext („direkt am Bahnhof Stettbach“).
- `discord.delay_between_posts` — Pause zwischen zwei Nachrichten (Rate-Limit-freundlich).
- `discord.max_posts_per_run` — Obergrenze pro Kategorie und Lauf, als Spam-Bremse.

## 4. Weitere Kategorien aktivieren

Wenn die anderen TCGs dazukommen sollen:

1. Discord-Webhook für den Kanal erstellen (Schritt 1).
2. Secret unter dem passenden Namen aus der Tabelle anlegen (Schritt 2).
3. In `config.json` bei der Kategorie `"enabled": false` auf `"enabled": true` ändern und committen.

Beim nächsten Lauf wird diese Kategorie zuerst still eingelesen (kein Spam) und ab dem
darauffolgenden Lauf werden neue Events gepostet.

## 5. Workflow manuell testen (ohne auf den Cron zu warten)

1. Im Repo auf **Actions** → links **TwoMoons Event Notifier**.
2. Rechts **Run workflow** — es erscheinen vier Felder:

| Feld            | Bedeutung                                                                        |
| --------------- | -------------------------------------------------------------------------------- |
| `dry_run`       | `true` = nichts posten, nichts speichern; die fertigen Embeds landen nur im Log   |
| `post_existing` | `true` = auch beim ersten Lauf alle aktuell gelisteten Events posten              |
| `categories`    | leer = alle aktiven; sonst z. B. `magic` oder `magic,pokemon`                     |
| `limit`         | max. Anzahl Posts pro Kategorie (`0` = Wert aus `config.json`)                    |

3. **Run workflow** klicken, den Lauf öffnen und den Schritt **Notifier ausführen** aufklappen.

Empfohlene Reihenfolge beim ersten Mal:

- **Probelauf:** `dry_run = true`, `categories = magic` → im Log siehst du jedes Embed als JSON,
  ohne dass etwas in Discord landet.
- **Echter Testpost:** `dry_run = false`, `post_existing = true`, `categories = magic` →
  die aktuell gelisteten Magic-Events werden einmalig gepostet, danach läuft alles im
  Normalmodus (nur noch Neues).

## 6. Zeitplan

Der Cron steht auf `0 7 * * *` (UTC), also **09:00 Sommerzeit / 08:00 Winterzeit**. GitHub-Cron
kennt keine Zeitzonen mit Sommerzeitumstellung, daher die Verschiebung im Winter. Wenn du
ganzjährig 09:00 Uhr treffen willst, trag in `.github/workflows/notify.yml` beide Zeiten ein —
doppelte Läufe posten nichts doppelt, weil `state.json` das verhindert:

```yaml
schedule:
  - cron: "0 7 * * *"
  - cron: "0 8 * * *"
```

## 7. `state.json`

Wird vom Workflow automatisch erzeugt und committet — du musst sie nicht anfassen. Aufbau:

```json
{
  "categories": {
    "magic": {
      "seen": { "<Event-ID>": { "title": "…", "date": "…" } },
      "initialized": true,
      "last_run": "2026-09-17T07:00:04Z"
    }
  }
}
```

- Einen Eintrag aus `seen` löschen ⇒ das Event gilt wieder als neu und wird erneut gepostet.
- Den ganzen Kategorie-Block löschen ⇒ die Kategorie startet wieder mit einem stillen Erstlauf.

## 8. Lokal ausführen (optional)

```bash
pip install -r requirements.txt

# Probelauf ohne Discord und ohne state.json zu verändern
python notifier.py --category magic --dry-run

# Echter Lauf
export DISCORD_WEBHOOK_MAGIC="https://discord.com/api/webhooks/…"
python notifier.py --category magic
```

Weitere Optionen: `--post-existing`, `--limit N`, `--config`, `--state`, `--verbose`.

Tests (prüfen Parser, Embed-Format und die state-Logik gegen ein gespeichertes Seitenabbild):

```bash
python -m unittest discover -s tests
```

## 9. Wenn etwas nicht klappt

| Symptom im Log                                    | Ursache / Lösung                                                              |
| ------------------------------------------------- | ----------------------------------------------------------------------------- |
| `Keine Events geparst`                            | Seitenstruktur hat sich geändert — Selektoren in `notifier.py` prüfen          |
| `DISCORD_WEBHOOK_… nicht gesetzt`                 | Secret fehlt oder ist anders benannt als `webhook_env` in `config.json`        |
| `Discord lehnte den Post ab (401/404)`            | Webhook gelöscht oder URL falsch kopiert — neu erstellen und Secret ersetzen   |
| `Discord Rate-Limit`                              | Wird automatisch abgewartet; bei Häufung `delay_between_posts` erhöhen         |
| Workflow committet `state.json` nicht             | Unter Settings → Actions → General: *Read and write permissions* aktivieren    |

Ein Fehler in einer Kategorie stoppt die anderen nie — jede wird einzeln abgearbeitet und
protokolliert.
