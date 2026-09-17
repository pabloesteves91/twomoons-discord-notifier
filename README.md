# TwoMoons Discord Notifier

Prüft einmal täglich die Event-Seiten von [twomoons.ch](https://www.twomoons.ch) und postet
**neue** Events pro Themenkategorie in einen eigenen Discord-Kanal — per Webhook, ohne Bot,
ohne eigenen Server. Der tägliche Lauf passiert komplett über GitHub Actions.

**Eine Kategorie läuft, sobald ihr Webhook-Secret im Repo hinterlegt ist** — mehr ist nicht
zu tun. Alle neun Kategorien (Magic, Pokémon, Star Wars Unlimited, Yu-Gi-Oh, Lorcana,
One Piece, Flesh & Blood, Riftbound, Unterhaltung) stehen in `config.json` bereit; ohne
Secret werden sie übersprungen und nur im Log erwähnt.

## Wie es funktioniert

1. Für jede aktive Kategorie wird die Übersichtsseite geladen und jede Event-Karte geparst
   (Titel, Datum, Ort, freie Plätze, Buchungslink, Bild, Kurzbeschreibung).
2. Die Zusatzinfos — Format, Eintritt, SUL, Turniersystem, Preispool und was sonst noch
   dabeisteht — stehen je nach Event entweder in einem Detail-Modal oder erst auf der
   Detailseite des Events; fehlt das Modal, wird diese Seite nachgeladen. Nennt ein Turnier
   keinen Eintritt, gilt der Ticketpreis der Seite. Gelesen wird nach dem Muster
   "Label: Wert" sowie "Überschrift + Text", also ohne feste Feldnamen — deshalb erscheinen
   auch Angaben wie `MAX Teilnehmer` oder `Decklist` automatisch im Embed.
3. Jedes Event bekommt eine ID: den Buchungslink, oder — falls das Event keinen eigenen Link
   hat — `Titel|Datum`.
4. Alle bereits gesehenen IDs stehen in `state.json`. Nur Events, deren ID dort **noch nicht**
   steht, werden nach Discord gepostet.
5. Für bereits gepostete Events wird geprüft, ob sich etwas geändert hat (typisch: die freien
   Plätze). Wenn ja, wird **die bestehende Discord-Nachricht bearbeitet** — es wird nichts
   doppelt gepostet. Dafür merkt sich `state.json` zu jedem Event die Message-ID.
6. Ist ein Event vorbei und von der Übersichtsseite verschwunden, wird seine Nachricht
   24 Stunden später aus dem Kanal **gelöscht** und der Eintrag aus `state.json` entfernt.
   Solange ein Event noch gelistet ist, bleibt es unangetastet.
7. Danach wird `state.json` vom Workflow automatisch zurück ins Repo committet, damit der
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

| Kategorie            | Secret-Name                    |
| -------------------- | ------------------------------ |
| Magic: The Gathering | `DISCORD_WEBHOOK_MAGIC`        |
| Pokémon              | `DISCORD_WEBHOOK_POKEMON`      |
| Star Wars Unlimited  | `DISCORD_WEBHOOK_STAR_WARS`    |
| Yu-Gi-Oh!            | `DISCORD_WEBHOOK_YUGIOH`       |
| Lorcana              | `DISCORD_WEBHOOK_LORCANA`      |
| One Piece            | `DISCORD_WEBHOOK_ONE_PIECE`    |
| Flesh & Blood        | `DISCORD_WEBHOOK_FLESH_BLOOD`  |
| Riftbound            | `DISCORD_WEBHOOK_RIFTBOUND`    |
| Unterhaltung         | `DISCORD_WEBHOOK_UNTERHALTUNG` |

Lege nur die Secrets an, deren Kategorien du haben willst — alle übrigen werden schlicht
übersprungen und im Log erwähnt. Ein Fehler in einer Kategorie hält die anderen nie auf.

## 3. Kategorien in `config.json`

Kategorien sind frei konfigurierbar, nichts ist im Code fest verdrahtet:

```json
{
  "key": "magic",
  "name": "Magic: The Gathering",
  "url": "https://www.twomoons.ch/events/magic-the-gathering-events/",
  "webhook_env": "DISCORD_WEBHOOK_MAGIC",
  "color": "#E67E22"
}
```

| Feld          | Bedeutung                                                             |
| ------------- | --------------------------------------------------------------------- |
| `key`         | Kurzname, auch für `--category` / das Workflow-Feld `categories`       |
| `name`        | Anzeigename in der Embed-Fusszeile                                     |
| `url`         | Übersichtsseite; zugleich Fallback-Link, wenn ein Event keinen hat     |
| `webhook_env` | **Name** der Umgebungsvariable — nie die URL selbst; ist das zugehörige Secret gesetzt, läuft die Kategorie |
| `color`       | Farbbalken des Embeds (Hex)                                            |
| `enabled`     | optional; `false` schaltet eine Kategorie trotz Secret ab              |

Ausserdem in `config.json`:

- `locations` — Zuordnung Filiale → Standortseite und Zusatztext („direkt am Bahnhof Stettbach“).
- `discord.delay_between_posts` — Pause zwischen zwei Nachrichten (Rate-Limit-freundlich).
- `discord.max_posts_per_run` — Obergrenze pro Kategorie und Lauf, als Spam-Bremse.
- `discord.update_existing` — `false` schaltet das tägliche Nachführen der Plätze ab.
- `cleanup.delete_after_hours` — wie lange nach Event-Ende die Nachricht stehen bleibt
  (Standard 24 Stunden); `cleanup.enabled: false` lässt alte Nachrichten für immer stehen.
- `detail_pages.selectors` — welcher Bereich der Detailseite ausgewertet wird (die
  Beschreibung statt Menü und Fusszeile); `price_selectors` bestimmt, woher der
  Ticketpreis als Eintritt kommt.

## 4. Weitere Kategorien aktivieren

Wenn die anderen TCGs dazukommen sollen, sind es nur zwei Schritte:

1. Discord-Webhook für den Kanal erstellen (Schritt 1).
2. Secret unter dem passenden Namen aus der Tabelle anlegen (Schritt 2).

Das war's — `config.json` muss dafür nicht angefasst werden. Beim nächsten Lauf wird die
Kategorie zuerst still eingelesen (kein Spam), ab dem darauffolgenden Lauf werden neue
Events gepostet. Willst du den aktuellen Stand sofort im Kanal haben, starte den Workflow
einmal von Hand mit `post_existing` = true.

## 5. Workflow manuell testen (ohne auf den Cron zu warten)

1. Im Repo auf **Actions** → links **TwoMoons Event Notifier**.
2. Rechts **Run workflow** — es erscheinen diese Felder:

| Feld            | Bedeutung                                                                        |
| --------------- | -------------------------------------------------------------------------------- |
| `dry_run`       | `true` = nichts posten, nichts speichern; die fertigen Embeds landen nur im Log   |
| `post_existing` | `true` = auch beim ersten Lauf alle aktuell gelisteten Events posten              |
| `categories`    | leer = alle aktiven; sonst z. B. `magic` oder `magic,pokemon`                     |
| `limit`         | max. Anzahl Posts pro Kategorie (`0` = Wert aus `config.json`)                    |
| `debug`         | ausführliches Log, zeigt u. a. die im Detail-Modal gefundenen Zeilen              |
| `reset`         | gemerkte Events vergessen — für den Kanal-/Server-Umzug, siehe Schritt 10         |

3. **Run workflow** klicken, den Lauf öffnen und den Schritt **Notifier ausführen** aufklappen.

Empfohlene Reihenfolge beim ersten Mal:

- **Probelauf:** `dry_run = true`, `categories = magic` → im Log siehst du jedes Embed als JSON,
  ohne dass etwas in Discord landet.
- **Echter Testpost:** `dry_run = false`, `post_existing = true`, `categories = magic` →
  die aktuell gelisteten Magic-Events werden einmalig gepostet, danach läuft alles im
  Normalmodus (nur noch Neues).

## 6. Probelauf ohne Knopfdruck

Wer keinen Zugriff auf den „Run workflow"-Knopf hat oder nur schnell sehen will, was der
Notifier erkennt, ändert `debug-run.txt` (eine Zeile genügt) und committet. Das startet
einen Lauf, der **zwingend** im Dry-Run läuft: Er ruft alle Kategorien ab — auch die ohne
Secret —, protokolliert pro Event die erkannten Felder und schliesst mit einer Übersicht,
postet aber nichts und ändert `state.json` nicht.

> **Nicht „Re-run jobs" benutzen.** Ein Re-run wiederholt den Commit, mit dem der Lauf
> ursprünglich gestartet wurde — also alten Code und einen alten `state.json`-Stand, was
> Events erneut posten würde. Der Workflow bricht solche Läufe inzwischen von selbst ab.

## 7. Zeitplan

Der Lauf startet **täglich um 09:00 Schweizer Zeit**, das ganze Jahr über.

GitHub-Cron kennt keine Sommerzeit, deshalb sind zwei Zeiten eingetragen: `0 7 * * *` und
`0 8 * * *` (UTC). Der Workflow prüft als Erstes, wie spät es in `Europe/Zurich` gerade ist,
und bricht ab, wenn es nicht 09:00 ist — pro Tag arbeitet also genau ein Lauf, der andere
endet nach wenigen Sekunden. Willst du eine andere Uhrzeit, verschiebe beide Cron-Zeiten um
denselben Betrag und passe im Job `zeitfenster` die Stunde an.

## 8. `state.json`

Wird vom Workflow automatisch erzeugt und committet — du musst sie nicht anfassen. Aufbau:

```json
{
  "categories": {
    "magic": {
      "seen": {
        "<Event-ID>": {
          "title": "…",
          "date": "…",
          "message_id": "1420…",
          "digest": "9f2c…"
        }
      },
      "initialized": true,
      "last_run": "2026-09-17T07:00:04Z"
    }
  }
}
```

- `message_id` ist die Discord-Nachricht zu diesem Event — nur damit kann der Lauf die
  Plätze später im bestehenden Embed nachführen.
- `digest` ist ein Fingerabdruck des Nachrichteninhalts. Weicht er beim nächsten Lauf ab,
  hat sich etwas geändert und die Nachricht wird aktualisiert.
- Einen Eintrag aus `seen` löschen ⇒ das Event gilt wieder als neu und wird erneut gepostet.
- Den ganzen Kategorie-Block löschen ⇒ die Kategorie startet wieder mit einem stillen Erstlauf.

## 9. Lokal ausführen (optional)

```bash
pip install -r requirements.txt

# Probelauf ohne Discord und ohne state.json zu verändern
python notifier.py --category magic --dry-run

# Echter Lauf
export DISCORD_WEBHOOK_MAGIC="https://discord.com/api/webhooks/…"
python notifier.py --category magic
```

Weitere Optionen: `--post-existing`, `--reset`, `--limit N`, `--include-inactive`
(auch Kategorien ohne Secret verarbeiten), `--config`, `--state`, `--verbose`.

Tests (prüfen Parser, Embed-Format und die state-Logik gegen ein gespeichertes Seitenabbild):

```bash
python -m unittest discover -s tests
```

## 10. Kanal oder Server wechseln

Jede Kategorie hat ihr eigenes Secret und damit ihren eigenen Kanal — die Kanäle dürfen auch
auf verschiedenen Servern liegen. Der Notifier kennt weder Server noch Kanal, er postet an die
URL im jeweiligen Secret. Ein Umzug (z. B. vom Test-Server auf den richtigen) geht so:

1. Im Ziel-Kanal einen Webhook erstellen (Schritt 1) und die URL kopieren.
2. Das bestehende Secret aktualisieren: **Settings → Secrets and variables → Actions →**
   Secret anklicken → **Update**. Kein Code-Deploy nötig.
3. Workflow manuell starten mit `reset` = **true**, `post_existing` = **true** und
   `categories` = der umgezogenen Kategorie.

`reset` löscht die gemerkten Events dieser Kategorie, `post_existing` postet den aktuellen
Stand einmalig in den neuen Kanal — inklusive neuer Message-IDs, damit die Plätze dort wieder
täglich nachgeführt werden. Die alten Nachrichten im vorherigen Kanal bleiben stehen und
werden nicht mehr aktualisiert; lösch sie einfach in Discord.

Ohne `post_existing` wirkt `reset` als stiller Neustart: der aktuelle Stand wird nur als
bekannt gespeichert und erst künftige Events werden gepostet.

## 11. Wenn etwas nicht klappt

| Symptom im Log                                    | Ursache / Lösung                                                              |
| ------------------------------------------------- | ----------------------------------------------------------------------------- |
| `Keine Events geparst`                            | Seitenstruktur hat sich geändert — Selektoren in `notifier.py` prüfen          |
| `DISCORD_WEBHOOK_… nicht gesetzt`                 | Secret fehlt oder ist anders benannt als `webhook_env` in `config.json`        |
| `Discord lehnte den Post ab (401/404)`            | Webhook gelöscht oder URL falsch kopiert — neu erstellen und Secret ersetzen   |
| `Discord Rate-Limit`                              | Wird automatisch abgewartet; bei Häufung `delay_between_posts` erhöhen         |
| Workflow committet `state.json` nicht             | Unter Settings → Actions → General: *Read and write permissions* aktivieren    |

Ein Fehler in einer Kategorie stoppt die anderen nie — jede wird einzeln abgearbeitet und
protokolliert.
