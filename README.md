# Mimir

**Ein gehärteter, lokaler, selbstverbessernder KI-Agent für Recherche und wissenschaftliches
Schreiben — läuft komplett auf deinem eigenen Rechner.**

Mimir durchsucht das Web und wissenschaftliche Literatur, fasst Quellen fundiert zusammen und
schreibt komplette Bachelor-/Masterarbeiten mit echten, überprüfbaren Zitaten — nicht
erfundenen. Es führt Chats mit einem lokalen Modell auf deiner GPU, gründet Antworten in deinen
eigenen Dokumenten, und kann sich selbst neue Fähigkeiten beibringen — alles ohne dass deine
Daten irgendwohin gesendet werden. Jede Sicherheitsgarantie wird durch **Topologie** erzwungen
— Hypervisor-Isolation, Netzwerk-Abwesenheit, Fähigkeits-Abwesenheit und Human-in-the-Loop —
**niemals**, indem das Modell einfach gebeten wird, sich zu benehmen.

Die Weboberfläche ist auf Deutsch (das Produkt richtet sich an deutsche Nutzer); Codebase und
Dokumentation sind auf Englisch.

![Mimir Chat-Oberfläche](docs/img/01-chat.png)
*Chat mit einem lokalen Modell — Streaming-Antworten und sichtbares Denken bei Reasoning-Modellen.*

---

## Warum Mimir anders ist

Die meisten Agenten-Frameworks vertrauen darauf, dass sich das Modell an die Regeln hält. Mimir
geht vom Gegenteil aus: **das Modell könnte jailbroken sein und bösartigen Code schreiben.**
Trotzdem darf nichts Schlimmes passieren, weil das Modell schlicht nicht die Reichweite dazu hat.

Das Leitprinzip:

> Jede Garantie in die Topologie verlagern (Firecracker-Jail + kein Netz + Fähigkeits-Abwesenheit
> + Taint/Fence + Human-in-the-Loop) — niemals auf die Verweigerung des Modells verlassen.

### Drei Vertrauenszonen

- **Zone A — Inferenz.** Der llama.cpp-GPU-Server. Führt keinerlei nicht vertrauenswürdigen Code
  aus und sieht nie ein Secret. Seine einzige Aufgabe: Tokens in Tokens verwandeln.
- **Zone B — Orchestrator / Web-UI / Worker.** Die Kontrollebene. Hält den Broker, die
  Policy-Engine und das Taint-Tracking. Sie hat **keine** Möglichkeit, beliebige Shell-Befehle
  auszuführen, **kein** Zahlungs-Primitiv und **keinen** Zugriff auf den Docker-Socket.
- **Zone S — Sandbox.** Selbstgeschriebene Skills (Selbstverbesserung) laufen **ausschließlich**
  in wegwerfbaren **Firecracker-microVMs**: kein Netzwerk, keine Secrets, keine Host-Mounts,
  keine GPU. Der einzige Weg nach draußen ist ein **vorab genehmigtes Primitiv**, aufgerufen über
  den **Broker**, der eine Allowlist, eine Taint-Prüfung und Human-in-the-Loop durchsetzt.

Weil **kein Zahlungs-Primitiv existiert**, ist eine Finanztransaktion nicht *zusammensetzbar* —
egal welcher Text ins Modell injiziert wird, es kann keine zusammenbauen. Nach außen wirkende
oder unumkehrbare Aktionen — Posten, Deployen, Installieren, E-Mail senden — erfordern auf jeder
Autonomiestufe unterhalb der höchsten eine menschliche Freigabe. Auf der höchsten Stufe
(**🚀 Voll autonom**) kann der Betreiber dieses Sicherheitsnetz explizit abwählen — eine bewusste,
bestätigte Entscheidung, kein Standard.

---

## Recherche & wissenschaftliches Schreiben

Das Herzstück von Mimir: ein Thema recherchieren und aufschreiben, oder eine komplette
Bachelor-/Masterarbeit entwerfen — mit **echten, überprüfbaren Zitaten**, nicht erfundenen.

- **🔎 Recherchieren:** Thema angeben, Mimir durchsucht **OpenAlex** (wissenschaftliche
  Literatur) und das Web, und schreibt eine fundierte Zusammenfassung mit Zitat-Markern im Text
  `[1]`, `[2]`, … und einem passenden Literaturverzeichnis am Ende.
- **📖 Thesis schreiben:** dieselbe Pipeline hochskaliert — Quellen suchen, Gliederung
  entwerfen, Kapitel für Kapitel schreiben (Wunschlänge bis ca. 44 Seiten wählbar), und ein
  vollständiges Literaturverzeichnis in deinem gewünschten Zitierstil: **APA, Harvard, IEEE,
  Chicago (Autor-Jahr) oder DIN 1505-2**.

Die zentrale Design-Entscheidung: das Literaturverzeichnis wird **niemals dem Modell zum
Erfinden überlassen**. Jeder Eintrag wird aus strukturierten Metadaten (Autor, Jahr, Titel,
Venue, DOI) zusammengesetzt, die von den Such-APIs selbst geliefert werden, und die
Zitat-Marker, die das Modell schreibt, werden gegen genau diese Quellenliste geprüft. Dokumente
werden nach Markdown, DOCX, PDF, HTML, ODT, EPUB und PPTX exportiert. Beide Werkzeuge leben im
Tab **📚 Bibliothek**, direkt neben deinen hochgeladenen Dokumenten (auf die sie ebenfalls
zugreifen können).

![Recherche und wissenschaftliches Schreiben](docs/img/07-research.png)
*Ausgabe des Recherche-Werkzeugs — eine fundierte Zusammenfassung mit Zitat-Markern im Text und
einem echten, aus OpenAlex stammenden Literaturverzeichnis, nicht vom Modell erfunden.*

---

## Funktionen

- **Chat** mit einem lokalen Modell: Streaming-Ausgabe und sichtbares Denken bei
  Reasoning-Modellen.
- **Dokumentbibliothek (RAG):** PDFs, DOCX und PPTX hochladen und Fragen stellen, die in deinen
  eigenen Dokumenten verankert sind, mit Seitenangaben. Mimir kann daraus auch fundierte
  Lernzusammenfassungen und Übungsklausuren erzeugen.
- **Recherche & Thesis-Schreiben:** ein Thema recherchieren oder eine komplette
  wissenschaftliche Arbeit mit echtem, nicht erfundenem Literaturverzeichnis aus OpenAlex und
  Websuche entwerfen — siehe [Recherche & wissenschaftliches Schreiben](#recherche--wissenschaftliches-schreiben)
  oben.
- **Selbstverbesserung:** stößt Mimir an eine Fähigkeitsgrenze, kann es einen neuen
  wiederverwendbaren Skill schreiben, ihn **im Jail gegen ein zurückgehaltenes Orakel testen**
  und zur Prüfung bereitstellen. Ein Mensch muss den Skill prüfen und kryptographisch
  **signieren (ed25519)**, bevor er wiederverwendbar wird. Der Agent kann eigene Skills niemals
  selbst signieren oder freigeben.
- **Modellverwaltung (Tab ⚙ Einstellungen):** Systemspezifikationen einsehen (GPU/VRAM/RAM),
  zwischen installierten GGUF-Modellen wechseln, und neue von HuggingFace herunterladen — mit
  Empfehlungen passend zu deinem VRAM. Ein Ein-Klick-**Beenden**-Button gibt den GPU-Speicher frei.
- **Persistente, wiederverbindbare Runs:** Aufgaben laufen im Hintergrund weiter, auch wenn du
  den Tab schließt. Ein Runs-Board und ein Freigabe-Postfach lassen dich wieder andocken und
  ausstehende Aktionen freigeben.

---

## Screenshots

![Modell- und Systemeinstellungen](docs/img/02-settings.png)
*Einstellungen — Systemspezifikationen einsehen, Modelle wechseln und neue passend zu deinem VRAM
herunterladen.*

![Dokumentbibliothek](docs/img/05-library.png)
*Dokumentbibliothek (RAG) — Fragen stellen, die in deinen eigenen PDFs, DOCX und PPTX verankert
sind, mit Zitaten.*

![Runs-Board](docs/img/06-runs.png)
*Runs-Board — zu Hintergrundaufgaben wieder andocken und ausstehende Aktionen freigeben.*

![Recherche und wissenschaftliches Schreiben](docs/img/07-research.png)
*Recherche & Thesis-Schreiben — fundierte Ausgabe mit echten Zitaten aus OpenAlex, nicht vom
Modell erfunden.*

---

## Voraussetzungen

- **Linux** für das volle Produkt: die microVM-Sandbox nutzt Firecracker + KVM, was nur unter
  Linux funktioniert.
- **Docker mit der nativen Docker Engine** — **nicht** Docker Desktop. Die VM von Docker Desktop
  kann die GPU oder die Host-Sockets, auf die Mimir angewiesen ist, nicht durchreichen. Dein
  Nutzer muss in der `docker`-Gruppe sein.
- **Eine GPU hilft sehr.** Eine **AMD-Radeon-Karte (Vulkan)** mit ~24 GB VRAM betreibt die
  Standard-30–35B-Modelle komfortabel; kleinere Modelle laufen gut mit 8–12 GB. Nur-CPU
  funktioniert, ist aber langsam.
- **~50 GB Speicherplatz** für Modellgewichte, **~30 GB RAM** empfohlen.

### Windows: nativ, GPU jeden Herstellers

Windows betreibt Mimir **nativ — kein Docker, kein WSL nötig.** Chat, Modellverwaltung,
Dokument-RAG und Web-Recherche funktionieren direkt, mit GPU-Beschleunigung auf **AMD, NVIDIA
und Intel** über einen nativen **llama.cpp-Vulkan**-Build (keine CUDA/ROCm-Installation nötig).
Der Ein-Klick-`MimirInstaller.exe` erkennt deine GPU/VRAM und lädt ein passendes Modell herunter.

**Selbstverbesserung** führt nicht vertrauenswürdigen, vom Modell geschriebenen Code aus, den
Mimir nur mit einer **Firecracker-microVM (Linux + KVM)** einhegen kann. Unter Windows ist das
eine **optionale** Funktion: Häkchen im Installer setzen, und Mimir richtet eine **dedizierte,
isolierte WSL2-Distro** ein (deine bestehenden Distros/Daten werden nie angefasst), die die
*echte* Firecracker-Sandbox betreibt. Siehe [windows-native/README.md](windows-native/README.md)
und [windows-native/WSL_SANDBOX.md](windows-native/WSL_SANDBOX.md).

---

## Installation

### Linux (Schnellstart)

```
git clone git@github.com:edgebird-lab/Mimir_AI.git
cd Mimir_AI
./install.sh
```

Danach Mimir über die Desktop-Icons **"Mimir starten"** / **"Mimir beenden"** starten, oder
<http://127.0.0.1:8082> im Browser öffnen.

### Windows (nativ — kein Docker, kein WSL)

**`MimirInstaller.exe`** von der Releases-Seite herunterladen und ausführen. Installiert
pro Benutzer (kein Admin nötig), erkennt GPU und VRAM, lädt ein passendes Modell herunter und
startet Mimir unter <http://127.0.0.1:8082>. Inferenz läuft auf einem nativen
**llama.cpp-Vulkan**-Build, sodass die GPU auf **AMD, NVIDIA und Intel** gleichermaßen genutzt
wird — **kein Docker, kein WSL, keine CUDA/ROCm-Installation**. Siehe
[windows-native/README.md](windows-native/README.md) für die Architektur und den Bau des
Installers.

Die Firecracker-microVM-Sandbox (Selbstverbesserung) bleibt **nur unter Linux** verfügbar; Chat,
Modellverwaltung, Recherche und Dokument-RAG funktionieren unter Windows vollständig. Das ältere
Docker-basierte `install.ps1` (unter `windows/`) bleibt nur für Nutzer erhalten, die bewusst den
WSL2/Docker-Weg wollen.

> **Hinweis zu Antivirus / SmartScreen.** Der Windows-Installer und die `.exe` sind **nicht
> code-signiert** (Code-Signing-Zertifikate kosten Geld). Windows Defender SmartScreen zeigt
> daher eine Warnung wie *"Der Computer wurde durch Windows geschützt"*, und dein Antivirus
> fragt eventuell nach. Das ist bei jedem unsignierten Open-Source-Installer zu erwarten. Klicke
> auf **Weitere Informationen → Trotzdem ausführen** (oder erlaube es in deinem Antivirus). Der
> gesamte Quellcode ist zur Prüfung offen, du kannst also genau nachvollziehen, was du ausführst.

Eine vollständige Schritt-für-Schritt-Anleitung für beide Plattformen — inklusive
Voraussetzungen, was der Installer tut, erster Start und Fehlerbehebung — findest du in
**[INSTALL.md](INSTALL.md)**.

---

## Mitwirken

Einen Fehler gefunden oder eine Idee? Issue oder Pull Request auf GitHub eröffnen:
[edgebird-lab/Mimir_AI](https://github.com/edgebird-lab/Mimir_AI).

## Lizenz

Mimir steht unter der **Apache License, Version 2.0**. Siehe [LICENSE](LICENSE) und
[NOTICE](NOTICE) für Details.

Copyright © Olbricht Digital. Kontakt: <robin@olbricht-digital.de>.
