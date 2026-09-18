# Die acht Seiten

[English](../PAGES.md) | **Deutsch**

Eine Seite je Frage, in der Reihenfolge, in der die Zifferntasten sie erreichen.
Jede ist zugleich ein Unterbefehl, der dieselbe Ansicht ausgibt, `4` und
`lsdsk health` sind also eine Sache unter einem Namen. Zurück zur
[README](README.md).

Die interaktive Ansicht ist getastet wie die `*top`-Familie: `1` bis `8` oder
die zugehörige Funktionstaste wechseln die Seite, `q` beendet. Die Fusszeile
führt diese auf, es ist also nichts auswendig zu lernen. `left`, `right`, `tab`
und `shift+tab` schalten ebenfalls zwischen den Seiten weiter, und `r` liest neu
ein, ohne in der Fusszeile zu erscheinen. Die Seiten tragen dieselben Namen wie
die Befehle, in derselben Reihenfolge, `4` und `lsdsk health` sind also dieselbe
Ansicht mit denselben Spalten. Jede Seite steht für sich und zeigt jede Platte,
es muss also nichts ausgewählt werden, um eine zu lesen.

Sechs der acht Seiten führen ausserdem einen Cursor - Topologie, Controller,
Platten, Gesundheit, Steckplätze und Verlauf -, den `up` und `down` bewegen. Eine
Tafel unter der Tabelle antwortet für das, worauf der Cursor steht: jeder Wert,
für den die Zeile keine Spalte hatte, dann die Befunde, die es benennen, mit
Begründung und Abhilfe. Der Eintrag gehört zum Gegenstand und nicht zur Seite,
ein Laufwerk liest sich also überall gleich, und nur die Reihenfolge seiner
Gruppen ändert sich. `i` blendet die Tafel aus und gibt der Tabelle den ganzen
Bildschirm; `shift+up` und `shift+down` rollen darin und werden nur angeboten,
wenn der Eintrag höher ist als die Tafel. SMART und Befunde führen keinen
Cursor, rollen als ganze Seite, und die Tafel liest dort `Nothing selected.`

Zwei Tasten antworten auf je einer Seite, und die Fusszeile bietet sie dort an
und sonst nirgends. `d` schaltet weiter, wie viel der PCI-Struktur die
Topologieseite zeichnet, dieselbe Einstellung, die `--tree-density` für jede
Ansicht setzt. `,` und `.` verschieben den Streifen unter der Plattentabelle,
der die vollständige Kennung des Laufwerks unter dem Cursor trägt: so bleibt
eine WWN lesbar, die für ihre Spalte zu lang ist.

## 1 Topologie

![Topologie](../docs/screenshots/1-topology.png)

Was defekt ist, kommt zuerst, dann die Maschine selbst: jeder Controller mit der
ausgehandelten Verbindung gegen die, die er hätte fahren können, und den
Laufwerken darunter. Wer nach dieser Seite aufhört, hat trotzdem nichts
Handelnswertes übersehen.

## 2 Controller

![Controller](../docs/screenshots/2-controllers.png)

Eine Zeile je Controller: Treiber und Firmware, laufend gegen möglich, wie viele
Anschlüsse er hat und wie viele frei sind, und was die Laufwerke daran gemeinsam
ziehen würden. Die letzte Spalte ist es, die aus einem freien Anschluss eine
ehrliche Antwort macht statt einer verlockenden.

## 3 Platten

![Platten](../docs/screenshots/3-disks.png)

Eine Zeile je Laufwerk, mit der Kennung, die Sie für eine Ersatzbestellung
brauchen: Modell, WWN, Seriennummer und Firmware, dann Grösse, Bus und die drei
Geschwindigkeiten. `port` ist, was der Steckplatz hergibt, `disk`, was das
Laufwerk kann, `link`, worauf die beiden sich geeinigt haben. Eine
Geschwindigkeit trägt, was sie wert ist, denn eine Form sagt nichts über den
Durchsatz, wenn der Leser die PCIe-Lane-Tabelle nicht im Kopf hat; und `size`
nennt die Skala, auf der sie geschrieben ist: ein Laufwerk wird in Zehnerpotenzen
verkauft und meldet in Zweierpotenzen, `466GiB` ist also das Laufwerk, auf dessen
Etikett 500 GB steht.

## 4 Gesundheit

![Gesundheit](../docs/screenshots/4-health.png)

Verschleiss, Temperatur, Betriebsstunden und geschriebene Bytes, daneben die
Zähler, die entscheiden, ob ein Laufwerk stirbt oder nur schlecht verkabelt ist:
umgelagerte, schwebende und nicht korrigierbare Sektoren, CRC-Fehler der
Schnittstelle und Medienfehler.

## 5 SMART

![SMART-Attribute](../docs/screenshots/5-smart.png)

Jedes Attribut jedes Laufwerks, mit Wert, schlechtestem Wert und Schwelle neben
der Rohzahl, denn eine Rohzahl heisst nichts ohne die Schwelle, an der das
Laufwerk sie selbst misst. NVMe-Laufwerke veröffentlichen statt einer
Attributtabelle ein festes Gesundheitsprotokoll, und die Seite sagt das, statt
eine Lücke zu lassen.

## 6 Befunde

![Befunde](../docs/screenshots/6-findings.png)

Jeder Befund mit seiner Begründung darunter und einer Abhilfe: was gemessen
wurde, was es bedeutet, und was dagegen zu tun ist. Hier wird ein CRC-Stand als
das Kabel benannt und nicht als das Laufwerk.

## 7 Steckplätze

![Steckplätze](../docs/screenshots/7-slots.png)

Jeder PCIe-Anschluss: was er tragen kann, was er fährt, was ihn belegt, was
dieser Belegung fehlt, und ein Urteil. `FREE` ist ein leerer Anschluss, `full`
heisst, dass die Belegung ausnutzt, was der Anschluss bietet, und eine
Restzahl ist Bandbreite, die niemand verwendet.

## 8 Verlauf

![Zählerverlauf](../docs/screenshots/8-trend.png)

Was jeder Zähler TUT, statt was er insgesamt zählt: die Änderung seit der
letzten Messung, die Spanne, über die sie gemessen wurde, eine Rate pro Stunde,
und ein Urteil. Zwei Laufwerke lesen hier `rising`. Eines liest
`no new in 16h, 235 were due`, ein Zähler, der zu steigen aufgehört hat, was nur
deshalb sagbar ist, weil die Lebensdauerrate des Laufwerks angibt, wie viele zu
erwarten gewesen wären. Der Rest sagt `too soon to say`, die ehrliche Antwort,
solange nicht genug von der Uhr des Laufwerks vergangen ist.
