# KI-Transparenz

[English](../ai-transparency.md) | **Deutsch**

Autor und Eigentümer dieses Projekts ist der Mensch,
[@bitranox](https://github.com/bitranox). Jede Entwurfs- und
Ingenieursentscheidung ist seine, und er steht für alles gerade, was hier
veröffentlicht ist. Ein KI-Assistent (Claude, betrieben über die Claude-Code-CLI)
wurde unterwegs als Werkzeug eingesetzt, überwiegend fürs Tippen und für die
Laufarbeit unter dieser Anleitung. Diese Seite sagt geradeheraus, wo, damit Sie
die Arbeit nach ihren Verdiensten beurteilen können. Die Überlegung dahinter
steht in [ai-stance.md](ai-stance.md).

## Die Arbeit des Menschen

Die Gestalt dieser Software ist von Anfang bis Ende die des Menschen. Er hat das
Problem gestellt, jede Entscheidung getroffen und trägt das Ergebnis.

- Das Problem ist seines: ein Vorgängerskript, das `lspci`, `lsblk`, `smartctl`
  und `nvme` aufrief, deren menschenlesbare Ausgabe per Regex abschabte, unter
  Windows nicht laufen konnte und über Verschleiss nichts berichtete.
- Die zentrale Entwurfsregel ist seine, und sie ist es, die dieses Werkzeug von
  einer Faktenhalde unterscheidet: **der Schweregrad wird daran gemessen, was
  die Maschine überhaupt hätte liefern können.** Eine Verbindung unterhalb des
  Maximums eines Geräts ist nur dann ein Fehler, wenn etwas Besseres verfügbar
  ist; jede Geschwindigkeitsregel wägt daher, was das Gerät kann, was das andere
  Ende kann, und was ausgehandelt wurde.
- Ebenfalls seine, und jede einzelne hat die Ausgabe verändert: dass eine
  PCIe-Bridge kein benutzbarer Steckplatz ist; dass ein Vorschlag berücksichtigen
  muss, welche Steckplätze wirklich frei oder tauschbar sind, weil der Platz der
  Grafikkarte nicht zur Verfügung steht; dass die Breite ebenso zählt wie die
  Geschwindigkeit; dass die drei Geschwindigkeiten in drei Spalten namens port,
  disk und link gehören statt zu einem Urteil zusammengefasst; welche Farbe
  welche Spalte trägt und warum; dass die ganze Maschine die Standardansicht
  sein soll, weil jemand, der nicht weiss, was defekt ist, auch nicht wissen
  kann, wonach er fragen soll; dass die interaktiven Seiten dieselben Namen
  tragen müssen wie die Befehle; und dass die SMART-Seite jedes Laufwerk zeigen
  soll, statt eine Auswahl zu verlangen.
- Die Entscheidungen, die das Werkzeug ehrlich halten, sind seine: keinen Wert
  melden, der nicht gemessen wurde, und klar sagen, welche Werte nicht gelesen
  werden konnten.
- Dass ein Skill für KI-Agenten mitgeliefert wird, war seine Entscheidung,
  einschliesslich der, dass er einem Agenten beibringen soll, ein Mainboard- oder
  Controller-Handbuch zu holen, wo das Werkzeug selbst aufhört, da lsdsk
  grundsätzlich keine Netzwerkanfrage stellt.

## Wo die KI eingesetzt wurde

Unter dieser Anleitung hat der Assistent das Tippen und die Laufarbeit erledigt:
sysfs und die Win32-APIs auf echten Maschinen abgetastet, um herauszufinden, was
tatsächlich lesbar ist, die Strukturen von ATA IDENTIFY, ATA SMART und NVMe
dekodiert, die Renderer und die Testsuite geschrieben und die unten beschriebene
Überprüfung durchgeführt.

Er wurde auch eingesetzt, um seine eigene Arbeit zu prüfen, und dort hat er sich
gelohnt: die Ausgabe von lsdsk Feld für Feld gegen `smartctl`, `lspci` und
`nvme` auf laufender Hardware gemessen, und dabei mehrere Stellen gefunden, an
denen das Werkzeug etwas behauptete, das es nicht gemessen hatte.

## Was geprüft ist, und was nicht

Auf echter Hardware geprüft:

- Jede Messung Feld für Feld gegen `smartctl --json -x` über 19 Platten eines
  Speicherservers verglichen: 207 von 207 stimmten überein, und die zwei
  scheinbaren Abweichungen erwiesen sich als ein Zähler, der zwischen den beiden
  Läufen weiterzählte, nicht als Dekodierfehler.
- Der PCIe-Verbindungszustand gegen `lspci -vv` verglichen und die physischen
  Steckplatznummern gegen dessen `SltCap`-Zeile, an 16 Anschlüssen: identisch.
- Die Dekodierung des AHCI-Fähigkeitsregisters gegen die Sondierungsmeldungen
  des Kernels verglichen, die übereinstimmen.
- Der Durchsatz der Schnittstelle direkt gemessen, um einen Befund zur
  Überbuchung zu bestätigen: zwei SSDs lasen einzeln je 400 MB/s und gemeinsam
  je 206 MB/s, eine geteilte Decke genau dort, wo PCIe 2.0 x1 sie vorhersagt.

Nicht geprüft:

- Keine physische Windows-Maschine. Der Windows-Pfad wird auf einer virtuellen
  Maschine und über eine Aufnahme geübt, die auf jedem CI-Runner erneut
  dargestellt wird; die Abbildung ist also getestet, der Transportweg aber nicht
  gegen echte Windows-Hardware bewiesen.
- Kein SAS-Expander, kein Hardware-RAID-Controller, und kein Laufwerk nahe am
  Ende seiner Lebensdauer.

## Es selbst nachprüfen

Nichts hier verlangt Vertrauen. `lsdsk snapshot` schreibt die rohe Messung,
`--replay` stellt jede Aufnahme über denselben Dekodier- und Diagnosepfad dar,
und `--format json` gibt sie strukturiert aus. Jede Behauptung in der Ausgabe
lässt sich also auf Ihrer eigenen Maschine gegen `smartctl`, `lspci` oder `nvme`
prüfen. Die Testsuite spielt Aufnahmen echter Maschinen erneut ab, eine Änderung,
die eine Messung kaputtmacht, macht also einen Test kaputt.

## Was diese Seite nicht ist

Diese Seite ist kein Haftungsausschluss, und sie ist keine Entschuldigung. Die
Arbeit ist die des Menschen; das Werkzeug hat ihm geholfen, sie schneller zu
tun. Wenn hier etwas falsch ist, hat er dafür geradezustehen.

## Lizenz und Urheberschaft

MIT, wie beim Rest des Projekts. Die Urheberschaft liegt beim menschlichen Autor.
