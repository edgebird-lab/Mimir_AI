# Mimir-Weboberfläche – Designkonzept

Gilt für `orchestrator/mimir/webui/index.html` (eine Datei, kein Framework, kein Build-Schritt).
Zielgruppe sind deutschsprachige Studierende und Forschende, keine Entwickler. Die Oberfläche soll
sich anfühlen wie ein ruhiges Schreib- und Lesewerkzeug, nicht wie eine IDE.

## Leitlinien

1. **Lesen zuerst.** Antworten, Berichte und Quellen sind der eigentliche Inhalt. Sie bekommen eine
   begrenzte Zeilenlänge (~75 Zeichen), großzügigen Zeilenabstand und keine Kästen drumherum.
2. **Ein Akzent.** Petrol (`--accent`) markiert Auswahl, primäre Aktionen und Links. Grün, Bernstein
   und Rot sind ausschließlich Statusfarben (ok, Achtung, Gefahr) – nie Dekoration.
3. **Sicherheit ist sichtbar.** Autonomie-Stufe, Freigaben, nach außen wirkende Aktionen und
   „Beenden“ sind die einzigen Elemente mit Warnfarben und sind dadurch nicht zu übersehen.
4. **Klare Hierarchie.** Hauptbereiche links in der Seitenleiste, geöffnete Dokumente und Läufe als
   Tabs darüber, Inhalte als Seiten mit Überschrift, Erklärsatz und Karten.
5. **Deutsch und verständlich.** Keine Entwicklerbegriffe in Beschriftungen („Projektdateien“ statt
   „Explorer“, „Läufe“ statt „Runs“, Status „wartet auf Freigabe“ statt `WAITING APPROVAL`).
6. **Keine Emoji als Bedienelemente.** Alle Icons sind schlichte Inline-SVGs aus einem Sprite.

## Informationsarchitektur

```
┌ Kopfzeile ───────────────────────────────────────────────────────────────────┐
│ [≡] [Logo] Mimir  (bereit)        Kontext ▬▬ 30 %  [Autonomie ▾] [Freigaben 2] [Beenden] │
├ Seitenleiste ─────┬ Tabs (nur wenn Dokumente/Läufe offen) ─────────────────────┤
│ Chat              │                                                          │
│ Recherche & Bibl. │  Ansicht: Chat · Recherche & Bibliothek · Läufe ·        │
│ Läufe             │           Einstellungen · Dokument · Lauf-Protokoll      │
│ Einstellungen     │                                                          │
│ ─ Projektdateien ─│                              ┌ Postfach (Seitenpanel) ┐  │
│ Dateibaum         │                              │ Freigaben &            │  │
│                   │                              │ Entscheidungen         │  │
└───────────────────┴──────────────────────────────┴────────────────────────┴──┘
```

- Die vier Hauptbereiche sind Buttons `.tab[data-tab]` in `#nav`; geöffnete Dateien und Läufe sind
  `.tab[data-tab]` in `#tabs`. `activate(id)` schaltet beide Gruppen gemeinsam; die Tab-Leiste ist
  leer und damit ausgeblendet (`.tabs:empty`), solange nichts geöffnet ist.
- Das Freigabe-Postfach ist ein Seitenpanel innerhalb des Hauptbereichs; Kopfzeile mit Autonomie und
  Freigaben-Knopf bleibt sichtbar. `Esc` schließt es.

## Farben

Alle Farben sind CSS-Variablen in `:root`. Dunkel wird über `prefers-color-scheme` gesetzt oder
manuell über *Einstellungen › Darstellung* (`<html data-theme="light|dark">`, gespeichert in
`localStorage["mimir-theme"]`, vor dem ersten Zeichnen angewendet).

| Token | Hell | Dunkel | Verwendung |
|---|---|---|---|
| `--bg` | `#f6f3ee` | `#121416` | Arbeitsfläche |
| `--side` | `#efebe4` | `#16191c` | Kopfzeile, Seitenleiste, Panelköpfe |
| `--surface` | `#ffffff` | `#1b1f22` | Karten, Eingabefelder, Dokumentseite |
| `--surface-2` | `#f0ece5` | `#23282c` | gedämpfte Flächen, Code, Kennzahlen |
| `--hover` | `#e8e3da` | `#2a3035` | Hover in Listen und Navigation |
| `--line` | `#e2dcd2` | `#2b3035` | Haarlinien, Kartenränder |
| `--line-strong` | `#958f85` | `#646b72` | Rahmen von Eingabefeldern (≥ 3:1) |
| `--txt` | `#1d2125` | `#e9e7e3` | Fließtext, Überschriften |
| `--txt-2` | `#565b61` | `#a9adb2` | Sekundärtext, Beschriftungen |
| `--txt-3` | `#6b6f74` | `#8d9298` | Platzhalter, Metadaten, Icons |
| `--accent` | `#0d7a63` | `#4fc7a6` | Primäraktionen, Fokus, Links, Zitatlinie |
| `--accent-strong` | `#0a604e` | `#74d6ba` | Akzenttext auf Tönung, Hover |
| `--accent-tint` / `--accent-sel` | `#e1f0ea` / `#d2e8de` | `#173129` / `#1b3a31` | Auswahl, Avatar, Nav aktiv |
| `--ok` / `--ok-tint` | `#1d7a43` / `#e4f2e9` | `#5cc98a` / `#15291e` | erledigt, passt, belegt |
| `--warn` / `--warn-tint` / `--warn-line` | `#8a5300` / `#fbf0d9` / `#e2bd6e` | `#f0bf5a` / `#2a2312` / `#6b5420` | Freigabe nötig, Autonom, Entscheidung |
| `--bad` / `--bad-tint` / `--bad-line` | `#b3261e` / `#fbe9e7` / `#e9aaa3` | `#ff8a80` / `#301817` / `#74302b` | Gefahr, nach außen, Beenden, Fehler |

Hell lehnt sich an olbricht-digital.de an (warmes Off-White, Petrol `#0d7a63`), Dunkel an deren
Dunkelvariante (`#121416`, `#4fc7a6`). Mimir bekommt mit der Feder auf Petrol ein eigenes Zeichen.

**Kontraste (WCAG 2.1 AA, berechnet):** Fließtext 14,6:1 (hell) / 15,0:1 (dunkel); `--txt-2` ≥ 5,8:1;
`--txt-3` ≥ 4,6:1; Akzent als Text ≥ 4,8:1; Statusfarben auf ihrer Tönung ≥ 4,6:1; Eingaberahmen
≥ 3,0:1. Ein automatischer Check über alle Szenen (siehe „Prüfen“) findet keinen Text unter 4,5:1.

## Typografie

- **Text:** Inter (Variable, eingebettet), Fallback `system-ui, Segoe UI, Roboto`.
- **Überschriften:** Plus Jakarta Sans (Variable, eingebettet), `word-spacing: .08em`, weil die
  Schrift sehr schmale Wortzwischenräume hat.
- **Code/Werkzeugnamen:** `ui-monospace, Cascadia Mono, SF Mono, Menlo, Consolas`.

| Rolle | Größe / Zeilenhöhe | Gewicht |
|---|---|---|
| Seitentitel (`.page-head h2`) | 25 px / 1,2 | 700 |
| Dokument-H1 (`.md-body h1`) | 32 px / 1,2 | 700 |
| Kartentitel (`.card h3`) | 16 px / 1,3 | 650 |
| Chat-Antwort (`.answer.md`) | 15 px / 1,7, max. 46 rem breit | 400 |
| Dokument-Fließtext (`.md-body`) | 16 px / 1,75, max. 44 rem breit | 400 |
| UI-Text | 14 px / 1,5 | 400–600 |
| Beschriftungen, Metadaten | 12,5 px | 500–650 |

Schriften: Latin- und Latin-Ext-Subsets (deckt Deutsch, Anführungszeichen „…“, €, Gedankenstriche und
osteuropäische Autorennamen ab). `vendor/fonts.css` ist ~280 KiB, wird wie KaTeX vom Webserver ins
Vendor-Bundle eingesetzt (`webserver.py::_vendor_bundle`, optional – fehlt die Datei, greift der
Systemschrift-Stack). CSP bleibt unverändert (`font-src data:`). Lizenzen: OFL 1.1, siehe
`vendor/OFL-*.txt` und `NOTICE`.

## Abstände, Radien, Schatten

- **Raster 4 px:** 4 · 8 · 12 · 16 · 24 · 32 · 40 · 48. Karteninnenabstand 20/22 px, Kartenabstand
  16 px, Abschnittsabstand 40 px, Seitenrand 36–40 px (schmal 28 bzw. 18 px).
- **Radien:** `--r-sm` 6 px (Chips, Code), `--r` 10 px (Buttons, Felder, Listenzeilen),
  `--r-lg` 14 px (Karten, Freigaben, Panels), `--r-pill` (Plaketten, Status).
- **Schatten:** nur drei Stufen (`--shadow-sm` für Karten/Buttons, `--shadow` für die Eingabe,
  `--shadow-lg` für das Postfach). Im Dunkelmodus tragen Ränder statt Schatten.

## Komponenten

| Komponente | Klassen | Regeln |
|---|---|---|
| Schaltflächen | `.btn`, `.btn-primary`, `.btn-danger`, `.btn-ghost`, `.btn-sm`, `.icon-btn` | Pro Bereich höchstens eine Primäraktion. Gefahr nur als Kontur. Icon-Buttons immer mit `aria-label`. |
| Felder | `input`, `select`, `textarea`, `.field > span` | Sichtbare Beschriftung über dem Feld; Platzhalter nur als Beispiel („z. B. …“). |
| Karten / Seiten | `.page`, `.page-head`, `.card`, `.section`, `.grid2`, `.note` | Seite = Titel + ein Erklärsatz + Karten; Hinweise als `.note` mit Info-Icon. |
| Status | `.badge.b-<status>` | Deutscher Text aus `RUNSTATUS`, Punkt davor, „läuft“ pulsiert. |
| Chat | `.msg.user` (rechts, Karte), `.msg.assistant` (Feder-Avatar, kein Kasten), `button.send.stop` | Reihenfolge in der Antwort: Gedankengang → Arbeitsschritte/Freigaben → Antworttext. Während eine Antwort läuft, wird „Senden“ zu „Stoppen“. Bleibt die Antwort leer oder schlägt der Lauf fehl, steht ein Hinweis unter der Antwort statt einer leeren Blase. |
| Gedankengang | `details.think` | Zugeklappt eine kompakte Pille „Gedankengang“, offen max. 320 px hoch. |
| Arbeitsschritte | `.timeline .tl`, `.tn`, `.td`, `.why` | Deutsche Beschriftung aus `TOOLS` (z. B. „Suche in Fachliteratur (OpenAlex)“) plus wichtigstes Argument, Rohname nur als Tooltip; ✓/✗ farbig, Ablehnungsgrund auf Deutsch (`reasonDe`) in eigener Zeile. Unbekannte Werkzeuge: „Werkzeug „name““. |
| Zitate | `blockquote` in `.answer.md`/`.md-body` | Akzentlinie links, leicht abgesetzte Fläche, normale Schrift. |
| Freigabe | `.approval` (im Chat), `.apr` (Postfach) | Bernstein; „Freigeben“ primär, „Ablehnen“ rote Kontur. |
| Nach außen | `.apr.outward` + `.warnbanner` | Roter 2-px-Rahmen, rote Warnleiste; „Ablehnen“ steht zuerst und ist primär. |
| Entscheidung | `.apr.dec`, `.opt`, `.recbadge`, `.critbadge` | Empfohlene Option mit Akzentrahmen und primärem Knopf. |
| Autonomie | `.autonomy` + `#autonomy` | Sicher: neutral + grünes Schild. Autonom: Bernstein. Voll autonom: roter 2-px-Rahmen + Warndreieck (per `:has(option:checked)`, ohne JS). |
| Lauf-Protokoll | `.run-head`, `.rh-acts`, `.runlog`, `.ev`, `.ev.cp`, `.ev.nh`, `.ev.fail` | Kopf mit Art, Titel (nur das Thema), Startzeit, Status-Plakette und „Lauf stoppen“ (solange der Lauf nicht beendet ist). Generierter Text als Markdown in einer scrollbaren Karte (max. 26 rem) an der Stelle im Verlauf, an der er beginnt. Ergebnisse als Karte, „Mensch nötig“/Entscheidung als Bernstein-Karte, Laufende „fehlgeschlagen“ als rote Karte mit verständlichem Fehlertext (`errDe`). |
| Dokument | `.viewbar`, `.vseg`, `.md-body` | Papierfläche, Segment-Umschalter Ansicht/Quelltext, Download als Icon. |
| Bereitschaft | `#status`, `.st-warn`, `.st-bad` | Kopfzeilen-Status aus `/api/health`: „Modell wird geladen …“ (Bernstein, Senden gesperrt), „Sprachmodell nicht erreichbar“ (Rot), sonst „bereit“; während eines Chat-Laufs „Mimir arbeitet…“/„wartet auf Freigabe“. |
| Quellen | `.srcs`, `.srch`, `.sn` | Unter der Antwort, aus dem Ereignis `sources` (Suchmetadaten, nicht Modelltext). Zitiert die Antwort [n], nur diese; gleiche Dokumentseiten zusammengefasst; Adressen als Text. |
| Chat-Optionen | `.composer-opts`, `#optDocs`, `#optFast` | Zwei Schalter über der Eingabe, im Browser gemerkt; „Meine Dokumente einbeziehen“ nur aktiv, wenn Dokumente hochgeladen sind. |
| Dialog | `dialog.dlg`, `askDialog()` | Eigener modaler Dialog für Eingaben (Zusammenfassung, Probeklausur, Umbenennen) statt `prompt()`; Esc/Abbrechen schließen. |
| Läufe-Werkzeuge | `.runs-tools`, `.runrow .rm`, `#runsMore` | Filter „Chats ausblenden“ (Standard) und „Nur laufende“, Löschen fertiger Läufe, „Alte Läufe aufräumen“ (> 30 Tage), „Mehr anzeigen“. Lauf-Kopf mit `.eta` (Abschnitt k von n, Restzeit) und „Fortsetzen“/„Erneut starten“ für gestoppte Läufe. |
| Drucken | `#i-printer`, `body.printing` | Druckknopf in der Dokumentansicht; Druckansicht zeigt nur die Dokumentseite (Weg zum PDF, wo docproc kein PDF kann). |
| Wissens-Wiki | `.wiki`, `.wp`, `#wikiPage` | Seitenliste mit Beleg-Genauigkeit (`.acc-hi/mid/lo`); Seite als Markdown wie eine Chat-Antwort (`renderChatMd`), `[[Titel]]` wird danach zu `a.wl`, wenn die Seite existiert. |

## Icons

Inline-SVG-Sprite direkt nach `<body>` (`<symbol id="i-…">`, 24er-Raster, 1,75 px Kontur, runde
Enden), eingesetzt über `<svg class="ico"><use href="#i-name"/></svg>` bzw. `ico('name')` im JS.
Vorhanden: feather, chat, library, runs, settings, bell, power, shield, shield-check, alert, info, printer,
folder, file, refresh, plus, trash, send, x, chevron, download, upload, search, pen, check, play,
split, list, user, book, dot, sidebar, sun, moon, monitor. Die Pfade sind eigens gezeichnet (keine
Fremdlizenz). Der Beendet-Bildschirm trägt das Logo als eigenes SVG, weil der Sprite mit dem Body
verschwindet.

## Läufe-Liste

Spalten: Status-Plakette, Art, Titel (Fehlertext eines fehlgeschlagenen Laufs als Tooltip), Startzeit
(„heute, 14:05“, `fmtTime`), Pfeil. Schmal (< 620 px Container) nur Status, Titel, Pfeil.

## Responsiv

- ≤ 1180 px: Seitenleiste 240 px, Kontext-Zahlen nur noch für Screenreader (Balken bleibt).
- ≤ 980 px: Kontext-Balken 64 px, Unterhaltungsnummer ausgeblendet.
- ≤ 860 px: Beschriftungen „Freigaben“/„Beenden“ und Status nur noch für Screenreader.
- ≤ 760 px: Seitenleiste wird zur Icon-Leiste, Dateibaum ausgeblendet.
- Inhalte in `.page` reagieren per Container Query auf die tatsächliche Breite (Wiki einspaltig,
  Download-Formular gestapelt, Dokument-Aktionen in zweiter Zeile, Läufe ohne Art-Spalte).
- Die Seitenleiste lässt sich über das Icon links oben jederzeit ausblenden (Fokus beim Lesen).

## Barrierefreiheit

- Sichtbarer Fokus überall (`:focus-visible`, 2 px Akzent), Eingabefeld mit Fokusring um die ganze Box.
- Tastatur: Navigation und Knöpfe sind `<button>`; klickbare Zeilen (Dateibaum, Läufe, Tabs, Wiki,
  Upload-Fläche) haben `role="button"`, `tabindex="0"` und reagieren auf Enter/Leertaste; `Esc`
  schließt das Postfach und gibt den Fokus an den Freigaben-Knopf zurück.
- `aria-label` für alle reinen Icon-Knöpfe, `aria-current="page"` in der Navigation,
  `aria-expanded` an Seitenleisten- und Freigaben-Knopf, `role="status"` für den Laufstatus,
  `aria-pressed` im Ansicht/Quelltext-Umschalter.
- Status nie nur über Farbe: Plaketten haben Text, Autonomie-Stufen wechseln auch das Symbol, die
  aktive Navigation hat zusätzlich eine Akzentleiste und fettere Schrift.
- `prefers-reduced-motion` schaltet Animationen ab.

## Sicherheit (unverändert)

- Keine externen Ressourcen: Schriften und Icons sind eingebettet, das Favicon ist eine `data:`-SVG.
- Keine Inline-Event-Handler im Markup; das JS setzt Handler wie bisher per Property/Listener.
- Modellausgaben: markdown-it mit `html:false` → DOMPurify → KaTeX; Links in Chat-Antworten bleiben
  reiner Text mit sichtbarer Adresse. Diese Kette ist durch das Redesign nicht verändert.
- Zusätzlich escaped: Fehlertexte aus `/api/read` sowie Zahlen in der Systemübersicht.

## Pflege-Regeln

- Neue Farben nur als Token in beiden Themen anlegen und Kontrast prüfen.
- Im JS keine `style.cssText`/Inline-Farben mehr – Klassen verwenden (Ausnahmen: Fortschrittsbreite,
  Zählerfarbe des Kontext-Balkens, Upload-Statusfarbe).
- Neue Icons als `<symbol>` im Sprite, gleicher Stil (24er-Raster, Kontur, keine Füllung).
- IDs und Klassen, auf die das JS zugreift, nicht umbenennen.

## Schriften neu erzeugen

`vendor/fonts.css` enthält vier `@font-face`-Regeln (Inter normal Latin + Latin-Ext, Inter kursiv
Latin, Plus Jakarta Sans Latin), jeweils als `url(data:font/woff2;base64,…)`. Quelle sind die
woff2-Dateien aus `@fontsource-variable/inter` und `@fontsource-variable/plus-jakarta-sans`
(`files/inter-latin-wght-normal.woff2`, `inter-latin-ext-wght-normal.woff2`,
`inter-latin-wght-italic.woff2`, `plus-jakarta-sans-latin-wght-normal.woff2`). Die `unicode-range`
je Datei steht in deren `wght.css`. Beim Aktualisieren auch `OFL-*.txt` und `NOTICE` prüfen.

## Prüfen

Die Oberfläche lässt sich ohne laufendes Mimir prüfen: ein Playwright-Harness liefert die echte
`index.html` mit Vendor-Bundle und der echten CSP aus und stellt `/api/*` und die SSE-Events nach
(45 Prüfungen zu Denkblock, Markdown, LaTeX und Sanitizing, dazu Screenshots aller Bereiche in hell
und dunkel bei 1440 × 900 und 900 × 900 sowie ein automatischer Kontrast-Check).
