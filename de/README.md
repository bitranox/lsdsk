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

lsdsk ist ein Speicher-Diagnosewerkzeug für Linux und Windows: es gruppiert jede
Platte unter dem Controller und dem PCIe-Pfad, an dem sie hängt, misst jede
Verbindung an dem, was ihre beiden Enden könnten, liest SMART-Verschleiss und
Fehlerzähler, und sagt, bei welchem der gefundenen Unterschiede sich Handeln
lohnt.

Das c't Magazin hat es am 17. September 2026 vorgestellt:
[Kommandozeilentool lsdsk: Performance-Engpässe bei SSDs und Controllern finden](https://www.heise.de/ratgeber/Kommandozeilentool-lsdsk-Performance-Engpaesse-bei-SSDs-und-Controllern-finden-11440011.html).

Zehn Laufwerke, drei Controller, ein Chipsatz und eine Riserkarte zwischen ihnen
und der CPU. Die Maschine startet einwandfrei, und nichts daran sagt Ihnen, dass
eine Karte in einem x8-Steckplatz x1 ausgehandelt hat, dass zwei SSDs sich eine
Verbindung teilen, die schmaler ist als jede von ihnen allein, oder dass der
freie Anschluss, den Sie gerade belegen wollten, an einer bereits ausgelasteten
Anbindung hängt. Ein Speicherserver fällt selten rundheraus aus. Er läuft still
über Jahre mit einem Bruchteil dessen, was er gekostet hat, und die Teile, die
das erklären würden, liegen verstreut in sysfs, in einer Reihe von Ioctls und im
Mainboard-Handbuch.

lsdsk ist der schnelle Blick, bevor Sie etwas kaufen oder etwas beschuldigen: wo
jedes Laufwerk hängt, was jede Verbindung ausgehandelt hat gegen das, was sie
gekonnt hätte, welche Steckplätze frei sind und was sie taugen, und wo der
Engpass tatsächlich sitzt statt dort, wo er am leichtesten zu sehen ist.

Es startet keine Unterprozesse und stellt keine Netzwerkanfragen: jede Zahl, die
es ausgibt, wurde hier gelesen, unter Linux aus sysfs und über direkte Ioctls,
unter Windows über SetupAPI und DeviceIoControl.

## SCHNELLSTART

Der Befehl unten braucht `uv` und sonst nichts; falls es noch nicht installiert
ist, trägt [INSTALL.md](INSTALL.md#am-einfachsten-installieren-und-ausführen-mit-uv)
den Einzeiler für Linux, macOS und Windows.

Für die vollständige Auskunft führen Sie es als root oder Administrator aus
(`sudo`). Ohne das lesen SMART-Verschleiss, die Fehlerzähler, die Erkennung
physischer PCIe-Steckplätze und die Anschlusszahl eines SATA-Controllers als
`-`, und der Kopf der Ausgabe sagt es. Die Liste steht in
[INSTALL.md](INSTALL.md#was-root-rechte-braucht).

```bash
# als Administrator oder root ausführen für die vollständige Auskunft
uvx lsdsk@latest          # die lsdsk-TUI
uvx lsdsk@latest report   # einen gedruckten Bericht
uvx lsdsk@latest --help   # weitere Hilfe
```

Das ist der ganze Befehl. Am Terminal öffnet er eine interaktive Ansicht mit
einer Seite je Frage; in eine Pipe oder eine Datei umgeleitet gibt er dieselbe
Maschine als eine Seite aus: das Mainboard, was defekt ist, den Controllerbaum,
die Kennung jeder Platte, Verschleiss und Fehlerzähler, jedes SMART-Attribut,
die PCIe-Steckplätze, und jeden Befund mit seiner Begründung.

## Die TUI

Zifferntasten wechseln zwischen den Seiten, `Tab` schaltet weiter, und jede
Seite ist zugleich ein Unterbefehl: `lsdsk health` gibt genau das aus, was
Seite 4 zeigt.

![Die interaktive Ansicht von lsdsk: acht Seiten, die wechselnde Baumdichte, die Detailtafel und eine Tabelle, die verschoben wird](../docs/media/lsdsk-demo.gif)

Sechs der acht Seiten führen einen Cursor, und eine Tafel unter der Tabelle
antwortet für das, worauf er steht: jeder Wert, für den die Zeile keine Spalte
hatte, dann die Befunde, die es benennen. [PAGES.md](PAGES.md) geht alle acht
durch, mit einem Bild von jeder und den Tasten, die sie erreichen.

In eine Pipe, in eine Datei oder namentlich angefordert, gibt dieselbe Maschine
sich stattdessen als eine Seite aus, das Schlimmste zuerst, sodass schon der
erste Bildschirm alles Handelnswerte zeigt. [REPORT.md](REPORT.md) zeigt diese
Seite und sagt, wann Sie sie ausdrücklich anfordern sollten.

## Rechte

Es läuft ohne Privilegien und sagt, was das kostet. Topologie, PCIe-Zustand,
SATA-Fähigkeit und ausgehandelte Geschwindigkeit, SAS-Phy-Raten, Kapazität,
Controller-Firmware und NVMe-Temperatur werden sämtlich ohne jedes Recht
gelesen.

Vier Dinge brauchen root oder Administrator, und keines davon wird ohne sie
geraten:

- **SMART-Attribute und Verschleiss.** Diese Spalten lesen `-`.
- **Die Fehlerzähler, also `trend` und `record`.** Dieselbe
  Passthrough-Leseoperation: ein Lauf ohne erhöhte Rechte zeichnet also
  überhaupt nichts auf, und daraus lässt sich kein Verlauf bilden.
- **PCIe-Steckplatznummern und ob ein Anschluss ein echter Steckverbinder ist.**
  Die Strukturen, die das tragen, liegen hinter den ersten 64 Byte des
  Konfigurationsraums, und dort hört ein Lesezugriff ohne Rechte auf. Ohne sie
  liest die Spalte `slot` als `-`, und es wird nie vorgeschlagen, eine Karte
  umzustecken: ein Anschluss, der sich nicht als physischer Steckverbinder
  bestätigen lässt, könnte fest verlötetes Silizium sein.
- **Das AHCI-Fähigkeitsregister**, für das die BAR5 des Controllers abgebildet
  sein muss und das beides trägt: die Bitmaske der bestückten Anschlüsse, aus
  der die Zahl freier Anschlüsse eines SATA-Controllers kommt, und die
  Geschwindigkeit, die der Anschluss selbst tragen kann, was in jeder SATA-Zeile
  die Spalte `port` ist. Ohne es lesen beide `-`. Dieses eine wird auf manchen
  Rechnern auch als root verweigert, ein `-` dort ist also kein Beweis für einen
  Lauf ohne Rechte.

In einem Container kommt nichts davon zurück, weil die Geräteknoten nicht da
sind, um gelesen zu werden; erhöhte Rechte ändern daran nichts.

Freie Bandbreite an einem Anschluss wird in beiden Fällen berichtet: sie ist
eine Messung, und eine Messung wird gezeigt, gleich ob eine Karte dorthin
versetzt werden könnte oder nicht.

## Es bringt einen Skill für Claude Code mit

Das Schwierige an einem Speicherbericht ist nicht, ihn zu lesen, sondern zu
wissen, welche Befunde Handeln verdienen. lsdsk liefert dieses Urteilsvermögen
als Claude-Code-Skill mit, damit ein Agent, der die Ausgabe liest, zu denselben
Schlüssen kommt wie ein geübter Administrator.

```
/plugin marketplace add bitranox/lsdsk
/plugin install lsdsk
```

Der Skill lehrt, was das Werkzeug nicht kann: dass ein CRC-Stand das Kabel ist
und nie das Laufwerk; dass ein Verschleisswert nichts heisst ohne die Schwelle
des Laufwerks selbst; dass ein Controller, den das Board begrenzt, zwei
entgegengesetzte Abhilfen hat, je nachdem, ob ein schnellerer Anschluss
existiert und nur belegt ist; und dass eine Steckplatznummer gegen das
Mainboard-Handbuch gehalten wird, weil keine lesbare Quelle die Bauform angibt.
Er sagt auch, wohin man geht, wo lsdsk aufhört: das Werkzeug stellt
grundsätzlich keine Netzwerkanfrage, ein Agent aber kann das Board-Handbuch oder
das Datenblatt des HBA holen, und der Skill sagt, welche Fragen das beantwortet
und wie die nachgeschlagenen Zahlen von den gemessenen getrennt bleiben.

## Installation

```bash
uvx lsdsk@latest       # ausführen, ohne zu installieren
uv tool install lsdsk  # für den wiederholten Gebrauch installieren
pip install lsdsk
```

Python 3.11 oder neuer, Linux oder Windows. Es ruft nichts auf: kein
`smartmontools`, kein `nvme-cli`, kein `lspci`, keinerlei Unterprozess, und zu
keinem Zeitpunkt einen Netzzugriff. Seine eigenen Python-Abhängigkeiten stehen
in `pyproject.toml`.

Aus den numerischen Kennungen eines Controllers einen Namen zu machen ist das
eine, was es nicht von der Hardware ablesen kann; es liefert daher die
PCI-Namensdatenbank mit und verwendet bevorzugt die Kopie des Systems, wo es
eine gibt. Deshalb liest sich ein Controller unter Linux und unter Windows
gleich, und deshalb liest er sich auf Englisch, auch wenn das Betriebssystem der
Maschine es nicht ist: Windows übersetzt seine Gerätebeschreibungen, und lsdsk
zitiert sie nicht. Die Lizenz dieser Datei steht in `NOTICE`.

## Wie es arbeitet

Unter Linux liest es sysfs und setzt `SG_IO`-ATA-Passthrough- und
NVMe-Admin-Ioctls unmittelbar ab. Die Geschwindigkeit eines SATA-Anschlusses
kommt aus dem Fähigkeitsregister des AHCI-Controllers, denn `libata`
veröffentlicht eine Anschlussgeschwindigkeit erst, nachdem eine Begrenzung auf
ihn angewendet wurde; auf gesunder Hardware hat sysfs darauf also gar keine
Antwort. Unter Windows verwendet es `SetupAPI` und `DeviceIoControl` über
`ctypes`, ohne WMI und ohne PowerShell. Beide Plattformen erhalten dieselben
Strukturen aus ATA IDENTIFY, ATA SMART und NVMe, ein einziger Satz Dekoder
bedient also beide und wird auf jedem unterstützten Betriebssystem gegen
Aufnahmen echter Hardware geprüft.

Jeder Befehl, den es absetzt, ist ein Lesezugriff. Es schreibt nie auf ein Gerät.

## Dokumentation

| Dokument                                                                          | Was es behandelt                                                                                               |
|-----------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| [PAGES.md](PAGES.md)                                                              | Die acht Seiten, die Tasten, die sie erreichen, und was jede beantwortet                                       |
| [REPORT.md](REPORT.md)                                                            | Der Bericht auf einer Seite, und wann Sie ihn benennen sollten, statt das Terminal entscheiden zu lassen       |
| [COMMANDS.md](COMMANDS.md)                                                        | Jeder Befehl und jede globale Option, der JSON-Umschlag und die Exit-Codes                                     |
| [FINDINGS.md](FINDINGS.md)                                                        | Was es meldet, welchen Beleg jede Regel braucht, und was es zu raten ablehnt                                   |
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
