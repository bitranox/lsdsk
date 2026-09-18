# lsdsk

[English](../README.md) | **Deutsch**

<!-- Badges -->
[![CI](https://github.com/bitranox/lsdsk/actions/workflows/default_cicd_public.yml/badge.svg)](https://github.com/bitranox/lsdsk/actions/workflows/default_cicd_public.yml)
[![CodeQL](https://github.com/bitranox/lsdsk/actions/workflows/codeql.yml/badge.svg)](https://github.com/bitranox/lsdsk/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)
[![Open in Codespaces](https://img.shields.io/badge/Codespaces-Open-blue?logo=github&logoColor=white&style=flat-square)](https://codespaces.new/bitranox/lsdsk?quickstart=1)
[![PyPI](https://img.shields.io/pypi/v/lsdsk.svg)](https://pypi.org/project/lsdsk/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/lsdsk.svg)](https://pypi.org/project/lsdsk/)
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-46A3FF?logo=ruff&labelColor=000)](https://docs.astral.sh/ruff/)
[![codecov](https://codecov.io/gh/bitranox/lsdsk/graph/badge.svg?token=JKJR0XzLus)](https://codecov.io/gh/bitranox/lsdsk)
[![Maintainability](https://qlty.sh/gh/bitranox/projects/lsdsk/maintainability.svg)](https://qlty.sh/gh/bitranox/projects/lsdsk)
[![security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)

lsdsk ist ein Speicher-Diagnosewerkzeug für Linux und Windows: es gruppiert jedes
Laufwerk unter dem Controller und dem PCIe-Pfad, an dem es hängt, misst die Verbindung
der Ports, der Laufwerke und die aktuell ausgehandelten Geschwindigkeiten, 
SMART-Daten und Fehlerzähler, und gibt eine Handlungsempfehlung.

Das c't Magazin hat dieses Tool am 17. September 2026 vorgestellt:
[Kommandozeilentool lsdsk: Performance-Engpässe bei SSDs und Controllern finden](https://www.heise.de/ratgeber/Kommandozeilentool-lsdsk-Performance-Engpaesse-bei-SSDs-und-Controllern-finden-11440011.html).

Zehn Laufwerke, drei Controller, ein Chipsatz und eine Riserkarte zwischen ihnen
und der CPU. Die Maschine startet einwandfrei, und nichts daran sagt Ihnen, dass
eine Karte in einem x8-Steckplatz x1 ausgehandelt hat, dass zwei SSDs sich eine
Verbindung teilen, oder dass der freie Anschluss, den Sie gerade belegen wollten, 
an einer bereits ausgelasteten Anbindung hängt. Ein Speicherserver fällt selten rundheraus aus. 
Er läuft still über Jahre mit einem leicht behebbaren Defizit, und die Werte, 
die das erklären würden, liegen verstreut über sysfs, in einer Reihe von Ioctls und im Mainboard-Handbuch.

lsdsk startet keine Unterprozesse und stellt keine Netzwerkanfragen: jeder Wert, den
es ausgibt, wurde direkt gelesen, unter Linux aus sysfs und über direkte Ioctls,
unter Windows über SetupAPI und DeviceIoControl.

## SCHNELLSTART

Der Befehl unten benötigt lediglich `uv`; falls `uv` noch nicht installiert
ist, dokumentiert [INSTALL.md](INSTALL.md#am-einfachsten-installieren-und-ausführen-mit-uv) den Einzeiler für Linux, macOS und Windows.

Für die vollständige Auskunft führen Sie `lsdsk` am besten als root oder Administrator aus.
Ohne Admin-Rechte fehlen SMART-Verschleiss, Fehlerzähler, die Erkennung
physischer PCIe-Steckplätze und die Anschlusszahl der SATA-Controller. 
Die genaue Dokumentation dazu finden Sie unter [INSTALL.md](INSTALL.md#was-root-rechte-braucht).

Der Aufruf erfolgt am besten mit `uvx lsdsk@latest` - uv installiert dann die letzte, aktuellste Version in 
einem virtuellen Environment - im restlichen Dokument verwenden wir zur besseren Lesbarkeit die Kurzform `lsdsk`.

```bash
# als Administrator oder root ausführen für die vollständige Auskunft
uvx lsdsk@latest          # die lsdsk-TUI
uvx lsdsk@latest report   # einen gedruckten Bericht
uvx lsdsk@latest --help   # weitere Hilfe
```

`uvx lsdsk@latest` öffnet am Terminal eine interaktive Ansicht mit einer Seite je Thema; 
in eine Pipe oder eine Datei umgeleitet gibt es dieselben Daten als eine Seite aus: das Mainboard, 
was defekt ist, den Controllerbaum, die Kennung jeder Platte, Verschleiss und Fehlerzähler, jedes SMART-Attribut,
die PCIe-Steckplätze, und jeden Befund mit seiner Begründung.

## Die TUI

Zifferntasten wechseln zwischen den Seiten, `Tab` schaltet weiter, und jede
Seite ist zugleich ein Unterbefehl: `lsdsk health` gibt genau das aus, was
Seite 4 zeigt.

![Die interaktive Ansicht von lsdsk: acht Seiten, die wechselnde Baumdichte, die Detailtafel und eine Tabelle, die verschoben wird](../docs/media/lsdsk-demo.gif)

Sechs der acht Seiten führen einen Cursor, und eine Tafel unter der Tabelle
bietet Detailinformationen: jeder Wert, für den die Zeile keine Spalte
hatte und die entsprechenden Befunde. [PAGES.md](PAGES.md) beschreibt alle acht Seiten im Detail.

Über eine Pipe, oder in eine Datei oder mit dem Kommando `lsdsk report` gibt 
das Programm stattdessen einen Text aus, mit den wichtigsten Befunden zuerst.
Unter [REPORT.md](REPORT.md) finden Sie die Dokumentation zu diesem Report.

## Rechte

`lsdsk` läuft auch ohne Privilegien. Topologie, PCIe-Zustand,
SATA-Fähigkeit und ausgehandelte Geschwindigkeit, SAS-Phy-Raten, Kapazität,
Controller-Firmware und NVMe-Temperatur werden sämtlich ohne erhöhte Rechte
gelesen.

Vier Dinge brauchen jedoch root oder Administrator: 

- **SMART-Attribute und Verschleiss.**
- **Die Fehlerzähler, also `trend` und `record`.**
- **PCIe-Steckplatznummern und ob ein Anschluss ein echter Steckverbinder ist.**
- **Das AHCI-Fähigkeitsregister**

In einem LXC- oder Proxmox-Container können diese Werte auch mit erhöhten Rechten nicht gelesen werden.


## lsdsk bringt einen Skill für Claude Code mit

Das Schwierige an einem Speicherbericht ist nicht, ihn zu lesen, sondern zu
wissen, welche Befunde Eingriffe in das System nahelegen. lsdsk liefert dieses Urteilsvermögen
als Claude-Code-Skill mit, damit ein Agent, der die Ausgabe liest, zu denselben
Schlüssen kommt wie ein geübter Administrator.

```
# in claude code
/plugin marketplace add bitranox/lsdsk
/plugin install lsdsk
```

Der Skill zeigt und erklärt, was das Werkzeug nicht kann: dass ein CRC-Stand das Kabel ist
und nie das Laufwerk; dass ein Verschleisswert nichts heisst ohne die Schwelle
des Laufwerks selbst; dass ein Controller, den das Board begrenzt, zwei
entgegengesetzte Abhilfen hat, je nachdem, ob ein schnellerer Anschluss
existiert und nur belegt ist; und dass eine Steckplatznummer gegen das
Mainboard-Handbuch gehalten wird, weil keine lesbare Quelle die Bauform angibt.
`lsdsk` kann alle Werte auch als JSON exportieren und der Skill kann diese Daten dann
auf einer anderen Maschine interpretieren. Niemand möchte schliesslich einen Agenten
direkt am Server laufen haben.


## Installation

```bash
uvx lsdsk@latest       # ausführen, ohne zu installieren
uv tool install lsdsk  # für den wiederholten Gebrauch installieren - in einem venv
pip install lsdsk      # für Nostalgiker
```

Python 3.11 oder neuer, Linux oder Windows. Es ruft nichts auf: kein
`smartmontools`, kein `nvme-cli`, kein `lspci`, keinerlei Unterprozess, und zu
keinem Zeitpunkt einen Netzzugriff. Seine eigenen Python-Abhängigkeiten stehen
in `pyproject.toml`.

Von der Hardware liest `lsdsk` nur die Zahlencodes eines Controllers, nicht seinen
Namen. Mitgeliefert wird deshalb die PCI-Namensdatenbank. 
Darum heisst ein Controller unter Linux und unter Windows gleich, 
lsdsk zitiert die übersetzten Gerätenamen von Windows nicht. Die Lizenz der
Datenbank steht in `NOTICE`.

## Wie lsdsk arbeitet

Unter Linux liest es sysfs und setzt `SG_IO`-ATA-Passthrough- und
NVMe-Admin-Ioctls unmittelbar ab. Die Geschwindigkeit eines SATA-Anschlusses
kommt aus dem Fähigkeitsregister des AHCI-Controllers. 
Unter Windows verwendet es `SetupAPI` und `DeviceIoControl` über `ctypes`, 
ohne WMI und ohne PowerShell. Beide Plattformen erhalten dieselben
Strukturen aus ATA IDENTIFY, ATA SMART und NVMe, ein einziger Satz Dekoder
bedient also beide und wird auf jedem unterstützten Betriebssystem gegen
Aufnahmen echter Hardware geprüft.

Jeder Befehl, den lsdsk absetzt, ist ein Lesezugriff. Es schreibt also nie auf ein Gerät oder Controller.

## Dokumentation

| Dokument                                                                          | Was es behandelt                                                                                               |
|-----------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| [PAGES.md](PAGES.md)                                                              | Die acht Seiten und die Tastenbelegung                                                                         |
| [REPORT.md](REPORT.md)                                                            | Der Bericht auf einer Seite                                                                                    |
| [COMMANDS.md](COMMANDS.md)                                                        | Jeder Befehl und jede globale Option, der JSON-Umschlag und die Exit-Codes                                     |
| [FINDINGS.md](FINDINGS.md)                                                        | Was es meldet, welchen Beleg jede Regel braucht                                                                |
| [WHY.md](WHY.md)                                                                  | Das Problem, für das es geschrieben wurde, und die zwei Fälle, die man ohne es am leichtesten falsch beurteilt |
| [INSTALL.md](INSTALL.md)                                                          | Installation, und was ohne Rechte geht gegenüber dem, was root braucht                                         |
| [CONFIG.md](CONFIG.md)                                                            | Jeder Konfigurationsschlüssel, die Schichten und die Formen der Umgebungsvariablen                             |
| [DEVELOPMENT.md](DEVELOPMENT.md)                                                  | Arbeit an lsdsk: die Prüfkette, die Testspuren, eine Aufnahme anlegen                                          |
| [CONTRIBUTING.md](CONTRIBUTING.md)                                                | Wie man eine Änderung vorschlägt                                                                               |
| [SECURITY.md](SECURITY.md)                                                        | Eine Schwachstelle melden                                                                                      |
| [CHANGELOG.md](../CHANGELOG.md)                                                   | Was sich geändert hat, und wann (English)                                                                      |
| [docs/systemdesign/module_reference.md](../docs/systemdesign/module_reference.md) | Jedes Modul, die Schichtregel, die CLI-Befehle und die Exit-Codes (English)                                    |
| [ai-transparency.md](ai-transparency.md)                                          | Wo ein KI-Assistent eingesetzt wurde, was auf echter Hardware geprüft ist und was nicht                        |
| [ai-stance.md](ai-stance.md)                                                      | Warum das Projekt diese Haltung einnimmt                                                                       |

## Lizenz

MIT.
