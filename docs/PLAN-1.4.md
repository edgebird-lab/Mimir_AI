# Mimir 1.4 – Funktionsprüfung und Verbesserungsplan

**Status: Entwurf.** Punkte mit *(noch zu prüfen)* brauchen einen laufenden Mimir und werden nach dem
zweiten Testdurchgang bestätigt oder gestrichen. Umgesetzt wird erst, wenn Robin entschieden hat;
in diesem Durchgang sind nur kleine, eindeutige Fehler behoben worden (Liste am Ende).

## Grundlage

Geprüft aus Sicht von Studierenden und Forschenden (nicht Entwicklern), unter Windows 11 nativ:

| | |
|---|---|
| Installation | Release-Installer v1.1.2 nach `%LOCALAPPDATA%\Mimir-demo`, Programmcode auf den Stand von `main` gebracht |
| Hardware | Intel Arc iGPU (geteilter Speicher, ~18 GB), 34 GB RAM, keine Admin-Rechte |
| Modell | `gemma-4-E4B-it-Q4_K_M.gguf` (5 GB), Kontext 16 384, Embeddings `nomic-embed-text-v1.5` |
| Geprüft | Chat (BWL/Wirtschaftsinformatik), Recherche mit Quellen, Bibliothek (2 selbst erstellte Beispieldokumente), Wissens-Wiki, Läufe, Einstellungen, Darstellung hell/dunkel, Beenden |

Gemessen (llama-server-Log, Vulkan auf der iGPU):

| Vorgang | Zeit | Anmerkung |
|---|---|---|
| Modell laden | ~14 s | nach dem Start; vorher antwortet llama-server mit 503 |
| Chatfrage ohne Werkzeug | 122 s | 1 898 Prompt-Tokens (fast nur Systemprompt + Werkzeugliste) = 16,6 s; 899 Tokens Ausgabe inkl. Gedankengang |
| Chatfrage mit Literatursuche | 145 s | zwei Modellaufrufe, dazwischen OpenAlex |
| Recherche (7 Quellen, ~900 Wörter) | 4,5 min | inkl. Wiki-Pflege mit Selbstprüfung |
| Hochladen + Indexieren (PDF 3 S. + MD) | 24 s | Embeddings auf der CPU |
| Erzeugung | 13 tok/s allein, 7–9,5 tok/s | wenn ein zweites Programm die iGPU nutzt (hier: Ollama) |
| Prompt-Verarbeitung | 115–165 tok/s | |

---

## Muss für 1.4

### M1 Websuche liefert unter Windows nichts
- **Problem:** Die Recherche meldet „7 wissenschaftliche + 0 Web-Quellen gefunden“. Ursache: der native
  `webfetch`-Dienst sucht über `html.duckduckgo.com`; DuckDuckGo antwortet mit HTTP 202 und einer
  Bot-Erkennungsseite („anomaly“, „challenge“), der Parser findet 0 Treffer, die Oberfläche sagt
  nichts. Im Chat liefert `web_search` damit stillschweigend leere Ergebnisse.
- **Vorschlag:** (a) 202/Challenge erkennen und als Fehler melden („Websuche gerade nicht verfügbar“)
  statt leerer Liste; (b) eine robustere Quelle: SearXNG-Instanz im WSL-Modus nutzen, wenn vorhanden,
  sonst eine Such-API mit freiem Kontingent (z. B. Brave, Mojeek) per Schlüssel in den Einstellungen,
  oder bewusst nur OpenAlex/Crossref/Semantic Scholar und die Websuche aus der Recherche nehmen.
- **Aufwand:** S für (a), M für (b). **Nutzen:** hoch – die README verspricht Websuche. **Risiko:**
  externe API = neuer Datenabfluss nach außen; muss in Doku und Egress-Regeln sichtbar sein.

### M2 Fähigkeiten (Skills) laden unter Windows nie
- **Problem:** `skills/registry.sig` und `skills/_keys/owner.pub` werden nicht ausgeliefert, also
  verwirft `SkillLibrary._verify_registry_sig` das ganze Register. Zusätzlich bricht `git archive`
  im Installer-Build mit `core.autocrlf=true` die Hashes: alle 7 `skill.py` der Installation haben
  CRLF, ihr SHA-256 passt nicht zu `registry.json`, mit LF passt er (geprüft). Ohne Sandbox (nativ ohne
  WSL) könnten die Skills ohnehin nicht laufen.
- **Vorschlag:** `.gitattributes` mit `skills/** -text` (oder `eol=lf`) und `*.sh eol=lf`; Hashes beim
  Laden über LF-normalisierte Bytes bilden; Release-Signatur (Schlüssel des Projekts, nicht des
  Nutzers) mitliefern oder klar dokumentieren, dass Nutzer selbst signieren. Nativ ohne Sandbox die
  Skill-Werkzeuge gar nicht erst anbieten (siehe M3).
- **Aufwand:** M. **Nutzen:** mittel (Selbstverbesserung ist ein Kernversprechen). **Risiko:** die
  Signaturkette ist sicherheitskritisch – Änderung nur mit Test, der eine manipulierte Datei ablehnt.

### M3 Kleine Modelle bekommen zu viele und falsche Werkzeuge
- **Problem:** Der Chat bietet dem Modell alle 20 registrierten Werkzeuge an, darunter
  `webhook_post_allowlist`, `post_social`, `workspace_export_patch/media`, `stage_skill_candidate`
  und `run_skill_in_sandbox` (ohne Sandbox nutzlos), teils nur mit der Beschreibung „Mimir primitive
  X“. Das kostet ~1 800 Tokens pro Aufruf (16 s auf der iGPU, bei jeder Frage) und verleitet kleine
  Modelle zu falschen Aufrufen.
- **Vorschlag:** Werkzeugsatz pro Lauf-Art: Chat = Dokumente, Literatur, Web, Projektdateien lesen,
  Datei speichern, Gedächtnis. Sandbox-/Zone-W-/Outward-Werkzeuge nur, wenn die Sandbox erreichbar ist
  bzw. der Betreiber sie aktiviert hat. Werkzeugbeschreibungen kürzen.
- **Aufwand:** S–M. **Nutzen:** hoch (schnellere und treffsicherere Antworten). **Risiko:** gering;
  Broker/Policy bleiben unverändert, es wird nur weniger angeboten.

### M4 Nach Werkzeugfehlern bleibt die Antwort leer
- **Problem (bekannt, Code bestätigt):** Nach jedem Werkzeugschritt schickt `agent.run_events` nur
  „Continue using ONLY the tool results above …“. Kleine Modelle antworten darauf nach einem
  `DENIED`/Fehler häufig mit nichts. Bisher zeigte die Oberfläche dann eine leere Blase (jetzt
  wenigstens ein Hinweis, siehe Liste unten). *(noch zu prüfen: Häufigkeit mit gemma-4-E4B)*
- **Vorschlag:** Folgeprompt auf Deutsch/sprachneutral und mit Auftrag („Beantworte jetzt die
  ursprüngliche Frage; wenn ein Werkzeug fehlschlug, antworte ohne es und sag das kurz“); ist die
  Antwort nach Werkzeugen leer, einmal ohne Werkzeuge nachfragen. Fehlertexte an das Modell knapp und
  handlungsleitend formulieren.
- **Aufwand:** S. **Nutzen:** hoch. **Risiko:** gering; Prompt-Injection-Regeln (Daten ≠ Anweisung)
  müssen im Folgeprompt erhalten bleiben.

### M5 Chat nennt gefundene Quellen nicht
- **Problem:** Auf „Welche Studien gibt es zur Akzeptanz von ERP-Systemen in KMU? Nenne zwei bis drei
  Quellen mit Autor und Jahr.“ rief das Modell die Literatursuche korrekt auf, antwortete aber ohne
  einen einzigen Autor oder ein Jahr. Das Werkzeugergebnis geht als Python-`repr` mit
  `Tainted(...)`-Objekten an das Modell, nicht als lesbare Liste.
- **Vorschlag:** Ergebnisse von `academic_search`/`corpus_search` als kompakte, nummerierte Liste
  (Autor, Jahr, Titel, DOI bzw. Dokument + Seite) übergeben und unter der Chat-Antwort deterministisch
  einen Block „Quellen“ aus genau diesen Metadaten anzeigen – wie im Recherche-Bericht, nicht vom
  Modell geschrieben.
- **Aufwand:** M. **Nutzen:** sehr hoch für die Zielgruppe (überprüfbare Quellen sind das
  Kernversprechen). **Risiko:** gering; Abstracts bleiben als nicht vertrauenswürdig markiert.

### M6 Dokumentansicht zerlegt Formeln
- **Problem (bekannt, Code bestätigt):** `renderMarkdownView` lässt erst markdown-it laufen und danach
  KaTeX-Auto-Render. markdown-it frisst dabei `\(`, `\\`, `\,` und `_` in Formeln – derselbe Fehler, der
  im Chat mit `CHAT_MATH_RE` bereits gelöst ist. Betrifft Zusammenfassungen, Probeklausuren und
  Thesis-Texte mit Formeln. *(noch zu prüfen: an einer echten Zusammenfassung mit Formeln)*
- **Vorschlag:** Formelschutz aus `renderChatMd` als gemeinsame Funktion auch im Dokument-Viewer
  verwenden (Formeln vor markdown-it herausnehmen, nach DOMPurify als KaTeX einsetzen).
- **Aufwand:** S. **Nutzen:** hoch für Mathe-/BWL-Skripte. **Risiko:** gering, Mock-Harness hat
  bereits LaTeX-Prüfungen für den Chat, die man übernehmen kann.

### M7 Modellauswahl passt nicht zum Produkt und zur iGPU
- **Problem:** Der Katalog in `control_daemon.CATALOG` ist auf Coding ausgerichtet (erste
  Empfehlung „Qwen2.5-Coder-14B … stark im Coding“, Texte mit „SWE-bench“), enthält das tatsächlich
  installierte gemma-4-E4B nicht und wählt beim ersten Start Llama 3.1 8B. Auf einer iGPU zeigt
  „Grafikspeicher (VRAM) 17.9 GB“ geteilten Arbeitsspeicher; „passt“ steht dann auch bei 9-GB-Modellen,
  obwohl laut Kommentar in `mimir_win.py` der Spitzenverbrauch ~2× Dateigröße beträgt und die
  Antwortzeit schon mit 5 GB bei 2 Minuten liegt.
- **Vorschlag:** Katalog für deutsches wissenschaftliches Schreiben (Instruct-Modelle mit gutem Deutsch
  und Werkzeugaufrufen, je Größenklasse eins), Beschreibung in Nutzersprache („schnell, für
  Laptops“ / „gründlich, braucht starke Grafikkarte“), geteilten Speicher erkennen und anzeigen
  („geteilter Speicher – Antworten dauern 1–3 Minuten“), Empfehlung an gemessener Geschwindigkeit
  statt nur an VRAM ausrichten.
- **Aufwand:** M. **Nutzen:** hoch beim ersten Eindruck. **Risiko:** Modell-Lizenzen und Download-Größen
  prüfen; Benchmarks auf AMD/NVIDIA/Intel nötig.

### M8 Erster Start ist für Laien eine Blackbox
- **Problem:** Die Konsole beim Einrichten spricht Englisch („starting supervisor (Vulkan inference +
  embeddings) …“), die Desktop-Verknüpfung startet unsichtbar (`-WindowStyle Hidden`). Schlägt ein
  Download fehl, sieht man nichts. Der Browser öffnet sich, bevor das Modell geladen ist; die erste
  Frage lief bisher in eine leere Antwort (llama-server 503 „Loading model“ wurde nicht erkannt – jetzt
  behoben, siehe unten). Die Statusanzeige zeigt „bereit“, auch wenn das Modell noch lädt.
- **Vorschlag:** Statusanzeige an `/health` von llama-server koppeln („Modell wird geladen …“,
  „bereit“, „Modell nicht erreichbar“); Senden bis dahin sperren. Einrichtungsfortschritt (Downloads,
  GPU-Erkennung) als deutsche Seite im Browser statt Konsole; Fehler dort mit nächstem Schritt.
- **Aufwand:** M. **Nutzen:** hoch. **Risiko:** gering.

### M9 PDF-Export unter Windows angeboten, aber nicht möglich
- **Problem:** docproc kann unter Windows kein PDF (`weasyprint`/GTK fehlt, `convert.py` sagt das
  selbst), der Export-Dialog bietet „PDF“ trotzdem an. *(noch zu prüfen: Fehlermeldung in der
  Oberfläche – laut Code ein englischer `alert`)*
- **Vorschlag:** Exportformate von docproc `/health` abfragen und nur die verfügbaren anbieten; als
  PDF-Weg unter Windows „HTML exportieren und im Browser drucken“ erklären oder DOCX→PDF über ein
  mitgeliefertes Werkzeug prüfen.
- **Aufwand:** S. **Nutzen:** mittel–hoch (Abgabe als PDF ist üblich). **Risiko:** gering.

---

## Sollte

### S1 Lange Läufe: Zeit, Fortsetzen, Aufräumen
- **Problem:** Thesis-Läufe dauern auf einer iGPU sehr lange *(noch zu prüfen: Dauer eines Laufs mit
  ~22 Seiten)*; die Oberfläche nennt keine Schätzung. Ein gestoppter Thesis-Lauf meldet „erneuter
  Start setzt fort“, einen Knopf dafür gibt es nicht (man muss das Thema exakt gleich neu eintippen).
  Jede Chatnachricht ist ein eigener Lauf; die Liste „Läufe“ füllt sich mit Chats, Löschen/Filtern gibt
  es nicht, angezeigt werden nur die letzten 60.
- **Vorschlag:** Fortschritt („Abschnitt 7 von 22, ~40 min übrig“ aus gemessener tok/s), Knopf
  „Fortsetzen“ an gestoppten Thesis-Läufen, Filter (Chats ausblenden, nur laufende), Löschen alter Läufe.
- **Aufwand:** M. **Nutzen:** hoch für die Thesis. **Risiko:** gering.

### S2 Zusammenfassung/Probeklausur fragen per Browser-Dialog
- **Problem:** Beide Knöpfe öffnen ein natives `prompt()` („Thema … (leer = ganzes Dokument)“), ohne
  Optionen (Anzahl Fragen ist fest 8), passt nicht zum Design und ist auf Mobilgeräten unpraktisch.
- **Vorschlag:** kleiner Dialog in der Oberfläche mit Thema, Umfang/Anzahl Fragen, Schwierigkeitsgrad.
- **Aufwand:** S–M. **Nutzen:** mittel. **Risiko:** gering.

### S3 Antwortzeit auf schwacher Hardware
- **Problem:** 2 Minuten pro Chatfrage, davon ein großer Teil Gedankengang (899 Tokens Ausgabe für
  eine kurze Erklärung) und 16 s für die immer gleiche Werkzeugliste (siehe M3).
- **Vorschlag:** Schalter „Schnell antworten (ohne Gedankengang)“ (`enable_thinking: false`), Prompt-
  Cache für den festen Systemprompt (`cache_prompt`), kleinere Standard-Kontextgröße auf iGPUs, Hinweis
  in der Oberfläche, wenn ein anderes Programm die GPU belegt (gemessen: 13 → 7 tok/s).
- **Aufwand:** S–M. **Nutzen:** hoch. **Risiko:** Antwortqualität ohne Gedankengang prüfen.

### S4 Chat und eigene Dokumente
- **Problem:** Der Chat nutzt hochgeladene Dokumente nur, wenn das Modell von sich aus
  `corpus_search` aufruft *(noch zu prüfen: Frage zum hochgeladenen Skript)*. Unterhaltungen lassen
  sich nicht umbenennen oder löschen („Leeren“ löscht nur Nachrichten).
- **Vorschlag:** Schalter „Meine Dokumente einbeziehen“ im Chat (dann immer Retrieval mit
  Seitenangaben), Unterhaltungen umbenennen/löschen.
- **Aufwand:** M. **Nutzen:** hoch. **Risiko:** gering.

### S5 Dateinamen verlieren Umlaute
- **Problem:** `Academic._slug` macht aus „Geschäftsprozessen“ `gesch-ftsprozessen`
  (`recherche-digitalisierung-von-gesch-ftsprozessen-im-mittel.md`).
- **Vorschlag:** ä→ae, ö→oe, ü→ue, ß→ss vor dem Slug. Achtung: die Thesis-Wiederaufnahme hängt am Slug
  (`run_id = "t_" + slug`) – alten Slug als Fallback suchen, sonst verlieren laufende Arbeiten ihren
  Stand.
- **Aufwand:** S. **Nutzen:** mittel. **Risiko:** Wiederaufnahme (siehe oben).

### S6 Links in Dokumenten anklickbar
- **Problem:** Im Chat sind Links bewusst nur Text (keine Ein-Klick-Exfiltration über präparierte
  URLs). Der Dokument-Viewer und Wiki-Inhalte aus Dokumenten zeigen Modell- und Quellentext dagegen mit
  klickbaren Links.
- **Vorschlag:** gleiche Regel im Viewer, Ausnahme nur für DOIs aus den Metadaten des
  Literaturverzeichnisses.
- **Aufwand:** S. **Nutzen:** Sicherheit/Konsistenz. **Risiko:** gering.

### S7 Freigaben und Autonomie
- **Problem:** Das Backend kennt vier Autonomie-Stufen, die Oberfläche bietet drei (Stufe 2
  „Autonom+“ fehlt; ist sie gesetzt, zeigt die Auswahl nichts an). Freigabe-Karten zeigen technische
  Argumente (Pfad, Inhalt) ungekürzt *(noch zu prüfen: Freigabe einer Datei im Chat)*.
- **Vorschlag:** Stufe 2 entweder anbieten oder im Backend entfernen; bei „Datei speichern“ Vorschau
  mit Dateiname und den ersten Zeilen statt Rohargumenten.
- **Aufwand:** S. **Nutzen:** mittel. **Risiko:** Autonomie ist sicherheitsrelevant – Tests in
  `test_critical_hitl.py` erweitern.

### S8 Rote Tests unter Windows
- **Problem:** `test_gateway.py` scheitert unter Windows an `socket.AF_UNIX`. `test_wiki.py` scheitert
  an zwei Prüfungen zur gleichzeitigen Bearbeitung: die Konflikterkennung vergleicht Zeitstempel
  (`updated`), und `time.time()` hat unter Windows nur 15,6 ms Auflösung (geprüft für Python 3.10 und
  das mitgelieferte 3.12.8). Zwei Schreibvorgänge im selben Takt sind nicht unterscheidbar; im
  Echtbetrieb liegen zwischen Lesen und Schreiben Sekunden (LLM-Aufruf), daher selten – aber nicht
  ausgeschlossen.
- **Vorschlag:** Gateway-Test unter Windows überspringen; im Wiki einen Versionszähler statt des
  Zeitstempels für die optimistische Sperre verwenden.
- **Aufwand:** S. **Nutzen:** Verlässlichkeit, grüne Tests unter Windows. **Risiko:** gering
  (Schema-Migration einer Spalte).

---

## Später

- **L1 Einrichtungsassistent im Browser** statt Konsolenfenster, inkl. Modellwahl mit Größe/
  Geschwindigkeit und Download-Fortschritt (baut auf M8 auf). Aufwand L.
- **L2 Deinstallation fragt nach Daten:** Modelle (5–18 GB) und Daten bleiben nach dem Deinstallieren
  liegen (jetzt in INSTALL.md dokumentiert); Inno-Setup könnte fragen, ob sie gelöscht werden sollen.
  Aufwand S.
- **L3 Installer schlanker:** die Installation enthält Linux-Dateien (`.github`, `docker-compose.yml`,
  `install.sh`, `sandbox/`, `scripts/`), die nativ nie gebraucht werden. Aufwand S.
- **L4 Kodierung explizit:** `Academic._write` öffnet Dateien ohne `encoding=` und verlässt sich auf
  `PYTHONUTF8=1` aus dem Startskript. Aufwand S.
- **L5 Coding/Autopilot:** Backend (Zone W, Ziele) ist noch da, aber ohne Oberfläche; entweder
  entfernen (weniger Angriffsfläche, kleinere Prompts) oder als „Experimentell“ zurückbringen.
  Aufwand M–L.
- **L6 Läufe als Verlauf im Chat:** wiedergeöffnete Unterhaltungen zeigen nur die Antworttexte, nicht
  die Arbeitsschritte und Quellen. Aufwand M.

---

## In diesem Durchgang bereits behoben

| Commit | Was |
|---|---|
| `c18f93b` | Systemprompt schickte Nutzer in nicht mehr vorhandene Tabs („Coding“, „Ziele · Plan · Autopilot“, „📚 Bibliothek“) |
| `2763841` | Alle Unterhaltungen hießen „Chat“ – jetzt nach der ersten Frage benannt |
| `0d69f20` | Oberfläche: deutsche Werkzeugnamen und Gründe, fehlgeschlagene Läufe sichtbar, Stopp-Knöpfe für Chat und Läufe, Lauf-Protokoll mit gerendertem Text in richtiger Reihenfolge, Startzeit in „Läufe“, Projektdateien nach Upload aktualisiert |
| `a8dc816` | Literaturverzeichnis lief in Viewer und Export als ein Absatz durch |
| `bd8caf7` | Windows: pandoc lag nicht im PATH → DOCX-Export und Dokumentimport scheiterten still |
| `df2ef0d` | Wissens-Wiki zeigte Markdown-Rohtext (Sternchen) |
| `d24c638` | HTTP-Fehler von llama-server (503 beim Laden, 400 bei zu langem Kontext) führten zu leeren Antworten |
