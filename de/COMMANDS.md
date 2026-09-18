# Befehlsreferenz

[English](../COMMANDS.md) | **Deutsch**

Jeder Befehl, jede globale Option, der JSON-Umschlag und die Exit-Codes. Zurück
zur [README](README.md).

## Befehle

| Befehl                           | Zeigt                                                                                                 |
|----------------------------------|-------------------------------------------------------------------------------------------------------|
| `lsdsk`                          | Am Terminal die interaktive Ansicht, sonst die Seite darunter                                         |
| `lsdsk report`                   | Alles auf einer Seite. Jeder Befehl darunter ist ein Abschnitt davon                                  |
| `lsdsk topology`                 | Die Problemübersicht, dann die gesamte PCI-Struktur von der Wurzel her, mit den Platten je Controller |
| `lsdsk controllers`              | Controller, PCIe-Platzierung, freie Anschlüsse, Last                                                  |
| `lsdsk disks`                    | Eine Zeile je Platte                                                                                  |
| `lsdsk health`                   | Verschleiss, Temperatur, Betriebsstunden und Fehlerzähler                                             |
| `lsdsk smart`                    | Die SMART-Attribute jeder Platte, gegen ihre eigenen Schwellen                                        |
| `lsdsk findings`                 | Jeden Befund mit seiner Begründung und seiner Abhilfe                                                 |
| `lsdsk slots`                    | Jeden PCIe-Anschluss: Fähigkeit, Bestückung, was frei ist                                             |
| `lsdsk trend`                    | Was jeder Fehlerzähler über die Zeit tut, nicht nur seinen Stand                                      |
| `lsdsk record`                   | Eine Messung ablegen und nichts ausgeben, für einen Timer                                             |
| `lsdsk tui`                      | Eine interaktive Seite je Frage, in der Art der `*top`-Werkzeuge                                      |
| `lsdsk snapshot -o f.json`       | Die rohe Messung aufnehmen                                                                            |
| `lsdsk --replay f.json`          | Eine Aufnahme von einer beliebigen Maschine darstellen                                                |
| `lsdsk config`                   | Die zusammengeführte Konfiguration, und aus welcher Schicht jeder Wert stammt                         |
| `lsdsk config-deploy`            | Die mitgelieferten Vorgaben dorthin schreiben, wo Sie sie bearbeiten können                           |
| `lsdsk config-generate-examples` | Kommentierte Beispieldateien schreiben, ohne die laufende Konfiguration anzufassen                    |
| `lsdsk info`                     | Version, Projektseite und die Angaben, die ein Fehlerbericht braucht                                  |

Jede Option unten ist global: sie steht vor dem Befehl und gilt für den Befehl,
der folgt. `--expand-virtual` wird auch nach `topology`, `disks` und `tui`
angenommen, weil genau das in der Zeile steht, die jene Geräte zusammenzählt.
`--tree-density` wird auch nach `topology` angenommen, und die interaktive
Ansicht schaltet dieselbe Einstellung mit `d` auf ihrer Topologieseite weiter.
Jede Ansicht öffnet auf `storage-only` und sagt in einer Zeile über dem Baum,
was sie zeichnet und wie Sie mehr anfordern.

| Option                                                    | Tut                                                                              |
|-----------------------------------------------------------|----------------------------------------------------------------------------------|
| `--replay FILE`                                           | Eine Aufnahme darstellen, statt diese Maschine zu lesen                          |
| `--history-file F`                                        | Zählerverlauf dort lesen und schreiben statt in der Zustandsdatei des Benutzers  |
| `--no-record`                                             | Zähler gegen den Verlauf beurteilen, ohne diese Messung aufzunehmen              |
| `--expand-virtual`                                        | Jedes kernelvirtuelle Gerät auflisten, statt sie in einer Zeile zusammenzuzählen |
| `--tree-density storage-only\|storage-and-siblings\|full` | Wie viel der PCI-Struktur jede Ansicht zeichnet (Vorgabe `storage-only`)         |
| `--profile NAME`                                          | Ein benanntes Konfigurationsprofil laden                                         |
| `--set S.K=V`                                             | Einen Konfigurationswert überschreiben, wiederholbar                             |
| `--env-file FILE`                                         | Diese `.env` lesen, statt vom Arbeitsverzeichnis aufwärts zu suchen              |
| `--traceback` / `--no-traceback`                          | Im Fehlerfall den Python-Traceback ausgeben statt einer Zeile                    |
| `--version`                                               | Die Version ausgeben und beenden                                                 |

`lsdsk disks` hat eine eigene Option. Die Spalte `wwn` ist auf
`display.wwn_width` Zeichen begrenzt, weil eine NVMe-WWN fünfmal so lang ist wie
die SATA-WWNs daneben und sonst die Spaltenbreite für jede Zeile bestimmen
würde. Ein gekürzter Wert wird markiert und nicht stillschweigend gestutzt, und
auf der interaktiven Seite bleibt er in voller Länge in einem Streifen unter der
Tabelle lesbar, den `,` und `.` verschieben; auf keiner anderen Seite werden
diese beiden Tasten angeboten. `--full-wwn` gibt stattdessen die ganze Kennung
aus und legt die Tabelle breiter an als das Terminal, statt die Breite von den
Spalten daneben abzuziehen: die Zeile läuft seitlich hinaus, und ein Pager
schiebt sie (`lsdsk disks --full-wwn | less -S`). Der JSON-Umschlag trägt jede
WWN immer vollständig, gleich was die menschenlesbare Ansicht angefordert hat.

`--format json` liefert einen maschinenlesbaren Umschlag, der den erzeugenden
Befehl nennt, bei jedem Befehl, der Daten erzeugt, ausser `report`, dessen
maschinenlesbare Form `lsdsk snapshot` ist. Die Exit-Codes sind `0`, wenn nichts
zu tun ist, und `1`, wenn eine Warnung oder ein kritischer Befund vorliegt, es
passt also unmittelbar in eine Überwachungsprüfung. Fehler folgen den
sysexits-Konventionen statt einem einzigen Code: `13`, wenn etwas ein Recht
braucht, das dieser Lauf nicht hat, `22` für einen Konfigurationsabschnitt oder
einen `--profile`-Namen, den die Konfigurationsbibliothek ablehnt, `78` für eine
Datei, die keine von dieser Fassung lesbare Aufnahme ist, oder für eine
Plattform ohne Hardwareleser. Behandeln Sie alles über `1` als "ist nicht
gelaufen".

`2` ist der Verwendungsfehler von Click und heisst, dass die Befehlszeile falsch
war, nicht dass eine Datei fehlte: eine unbekannte Option, ein unbekannter
Befehl, ein fehlendes Argument und eine falsche `--format`-Wahl erzeugen ihn
alle, ebenso ein `--replay`-Pfad, den es nicht gibt.
