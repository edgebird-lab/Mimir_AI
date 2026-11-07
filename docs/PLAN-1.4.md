# Mimir 1.4 – Funktionsprüfung und Verbesserungsplan

**Status: umgesetzt.** Robin hat den Plan freigegeben; alle Punkte sind umgesetzt (Übersicht direkt
unten, Commits auf main, nicht gepusht). Offen ist nur das Signieren der Skills mit dem
Release-Schlüssel (M2), weil der private Schlüssel nicht auf dem Windows-PC liegt. Die Abschnitte
darunter sind die ursprüngliche Analyse mit Beleg, Vorschlag, Aufwand, Nutzen und Risiko.

## Umsetzungsstand

Geprüft: Mock-Harness (ui_test 45/45, func_test 19/19), alle 19 Python-Testskripte grün unter Windows
(neu: test_agent_chat, test_webserver_api, test_websearch, test_skills_signing, test_academic_misc),
Live-Prüfung mit der Testinstallation (gemma-4-E2B für Funktionstests, gemma-4-E4B für Screenshots).

| Punkt | Stand | Umsetzung | Geprüft |
|---|---|---|---|
| M1 Websuche | erledigt | webfetch fragt DuckDuckGo, dann Wikipedia de/en; ehrlicher User-Agent, Pause je Host, 10-min-Cache, gesperrte Quelle 15 min pausiert, Status je Quelle, 502 wenn alles fehlt; Linux fällt von SearXNG darauf zurück; Recherche nennt die Herkunft | test_websearch; live: „8 wissenschaftliche + 5 Web-Quellen (DuckDuckGo)“ |
| M2 Skills | erledigt bis aufs Signieren | `.gitattributes` (LF), Hash/Signatur über LF-Bytes, zweiter Vertrauensanker release.pub/registry.release.sig, `scripts/sign-release-registry.py`, Prüfung im Installer-Build, Status in den Einstellungen | test_skills_signing (beide Anker, CRLF, Manipulation) |
| M3 Werkzeugliste | erledigt | Chat bietet nur CHAT_TOOLS an; Sandbox-/E-Mail-Werkzeuge nur wenn vorhanden; Labs-Coordinator behält alle | test_agent_chat |
| M4 Antwort nach Werkzeugen | erledigt | Ursache live gefunden: die Frage fehlte nach Werkzeugschritten im Verlauf. Dazu klarer (deutscher) Folgeprompt, „TOOL FAILED … nicht erneut“, einmalige Nachfrage ohne Werkzeuge bei leerer Antwort, Antwort statt „(max steps reached)“ | test_agent_chat; live: Freigabe-Test antwortet jetzt sinnvoll |
| M5 Quellen im Chat | erledigt | nummerierte, lesbare Suchergebnisse für das Modell; Ereignis `sources`; Quellenliste unter der Antwort (nur zitierte, gleiche Seiten zusammengefasst) | test_agent_chat; live mit OpenAlex und eigenen Dokumenten |
| M6 Formeln im Viewer | erledigt | Viewer nutzt die Chat-Kette (Formelschutz vor markdown-it); GFM-Mathe aus DOCX-Import wird zurückgewandelt | Mock (Formel im Dokument); live: 34 KaTeX-Formeln in einer DOCX-Zusammenfassung |
| M7 Modellkatalog | erledigt | Katalog für deutsches Schreiben (Gemma 4, Qwen3), geteilter Speicher erkannt, „passt“ dort konservativ, Geschwindigkeit gemessen, Antwortzeit je Modell geschätzt | live in den Einstellungen und im Assistenten |
| M8 Erster Start | erledigt | `/api/health`; Kopfzeile „Modell wird geladen …“ bis das Modell bereit ist; Einrichtungsassistent im Browser (L1); Downloads geprüft, bevor ein Modell eingetragen wird | test_webserver_api; Assistent mit Testkopie (Modellwahl, .env, Fehleransicht) |
| M9 PDF unter Windows | erledigt | `/api/capabilities` (docproc meldet, was es wirklich kann), Export ohne PDF + „Drucken“, Druckknopf mit Druckansicht | live: Exportliste ohne PDF, Hinweis „Drucken“ |
| S1 Lange Läufe | erledigt | Restzeit „Abschnitt k von n · noch ca. …“, „Fortsetzen“/„Erneut starten“ (`/api/run/restart`), Filter, Löschen, Aufräumen, „Mehr anzeigen“ | test_webserver_api; live: Filter/Löschen; Thesis (~22 Seiten, gemma-4-E2B): 22 Quellen, Gliederung mit 17 Abschnitten und Schreibaufträge nach ~8 min, Abschnitt 1 nach ~1 min; „Lauf stoppen“ nach 13 s wirksam, „Fortsetzen“ setzte mit „Wiederaufnahme“ bei Abschnitt 2/17 fort |
| S2 Dialoge | erledigt | eigener Dialog mit Thema, Umfang bzw. Anzahl/Schwierigkeit | live: Zusammenfassung „kompakt“ mit DOCX |
| S3 Antwortzeit | erledigt, eine Ausnahme | „Schnell antworten“ (ohne Gedankengang), `cache_prompt`, gemessene Geschwindigkeit, Hinweis bei deutlich langsamerer Antwort. **Nicht umgesetzt:** kleinere Kontextgröße auf iGPUs – bringt wenig Tempo, würde aber lange Thesis-Abschnitte kappen | test_agent_chat |
| S4 Dokumente/Unterhaltungen | erledigt | „Meine Dokumente einbeziehen“ (Suche vor dem ersten Modellaufruf, Passagen eingezäunt), Umbenennen/Löschen | live: Antwort mit „[1, S. 3]“; Umbenennen/Löschen |
| S5 Umlaute | erledigt | ae/oe/ue/ss, alter Thesis-Schlüssel als Fallback | test_academic_misc |
| S6 Links | erledigt | im Viewer und Wiki Links als Text, nur nackte DOI-Links klickbar | Mock |
| S7 Freigaben/Autonomie | erledigt | Vorschau bei „Datei speichern“; Stufe 2 nur mit Labs | test_webserver_api; live: Freigabe im Chat und Postfach |
| S8 Tests | erledigt | Wiki mit Versionszähler, Gateway-Test unter Windows übersprungen | 19/19 grün |
| L1 Assistent | erledigt | `windows-native/setup_ui.py` | siehe M8 |
| L2 Deinstallation | erledigt | Rückfrage (Standard „Nein“) | ISCC 6 kompiliert |
| L3 Installer | erledigt | Linux-/Docker-Teile aus dem Staging entfernt | Skript-Parser |
| L4 UTF-8 | erledigt | `encoding="utf-8"` überall | Testsuite |
| L5 Coding/Autopilot | entschieden: aus den Nutzerpfaden entfernt | Endpunkte nur mit `MIMIR_LABS=1`, Backend und Tests bleiben. Begründung: seit dem Fokus auf Recherche keine Oberfläche, braucht die Sandbox, kostet Angriffsfläche und Prompt-Tokens; eine halbe Oberfläche hätte mehr verwirrt als genutzt | test_webserver_api (404 ohne Labs) |
| L6 Verlauf | erledigt | Schritte und Quellen werden mit der Antwort gespeichert und beim Öffnen gezeigt | test_webserver_api |

Zusätzlich behoben: KI-Nutzungserklärung der Thesis nannte ein fest eingetragenes Modell (08e3352);
Umlaute in den Modellbeschreibungen des Assistenten; DOCX-Formeln (fc41d84).

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
