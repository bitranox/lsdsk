# Warum es das gibt

[English](../WHY.md) | **Deutsch**

Das Problem, für das lsdsk geschrieben wurde, und die zwei Fälle, die man ohne
es am leichtesten falsch beurteilt. Zurück zur [README](README.md).

Wer einen Server mit vielen Platten betreut, kennt den Moment. Etwas ist
ausgefallen, und das Bild wird genau dann trüb, wenn es scharf sein müsste:

- Welches Laufwerk ist es?
- Wo hängt es, physisch und logisch?
- Wie geht es den anderen am selben Controller?
- Läuft alles mit der Verbindungsgeschwindigkeit, die es haben sollte?
- Wo könnte ein weiteres Laufwerk hin, und ist Bandbreite da, um es zu speisen?
- Gibt es Verkabelungs- oder Einschubprobleme, etwa CRC-Fehler, die sonst
  niemandem auffallen?
- Wie steht es um SMART?

Jede Antwort steckt irgendwo in `lspci`, `lsblk`, `smartctl` und `nvme`, in vier
Formaten, die sich in nichts einig sind, und Sie setzen sie zum denkbar
schlechtesten Zeitpunkt von Hand zusammen. Unter Windows setzen Sie sie gar
nicht zusammen.

Die naheliegende Antwort darauf ist, all das an einer Stelle einzusammeln und
ordentlich auszugeben. Nützlich, und die meisten Werkzeuge hören dort auf.

Das Einsammeln ist die leichte Hälfte. Eine Zahl für sich ist träge. `3.0 Gb/s`
heisst nichts, solange Sie nicht zugleich wissen, wozu genau dieses Laufwerk
imstande wäre, und das weiss um zwei Uhr nachts niemand für achtzehn Laufwerke.
Stellt man beide nebeneinander, hört das Sichmerken auf, Ihre Aufgabe zu sein:

```
port             disk             link
6G (0.60 GB/s)   6G (0.60 GB/s)   6G (0.60 GB/s)   everything agrees, nothing to say
12G (1.20 GB/s)  3G (0.30 GB/s)   3G (0.30 GB/s)   a 3 Gb/s drive occupying a 12 Gb/s seat
3G (0.30 GB/s)   6G (0.60 GB/s)   3G (0.30 GB/s)   the port is the limit, the drive could do more
6G (0.60 GB/s)   6G (0.60 GB/s)   3G (0.30 GB/s)   both ends can do 6 and the link cannot: a fault
```

Dieselben Bytes, vom selben Laufwerk, aus denselben Befehlen. Geändert hat sich
nur, woneben sie stehen. Das ist das meiste von dem, was lsdsk ist: jeder
Messwert gegen das gestellt, was er hätte sein sollen, sodass Sie ein Urteil
bekommen statt einer Rechenaufgabe.

## Der Ausfall, den niemand bemerkt

Nun der interessantere Fall, der, in dem überhaupt nichts defekt ist.

Ein altes Laufwerk mit 3 Gb/s an Ihrem einzigen 6-Gb/s-Anschluss ist kein
Fehler. Es läuft mit seiner Nenngeschwindigkeit, meldet tadellose Gesundheit,
und kein Überwachungssystem der Welt wird es je erwähnen. Derweil läuft ein
Laufwerk mit 6 Gb/s anderswo im selben Gehäuse mit halber Geschwindigkeit, weil
der schnelle Anschluss schon vergeben war.

Für sich genommen sind beide Laufwerke in Ordnung. Die Anordnung ist es nicht,
und die Abhilfe besteht darin, zwei Kabel zu tauschen. lsdsk sucht genau nach
dieser Paarung und nennt beide Laufwerke, denn so etwas überlebt die gesamte
Lebensdauer einer Maschine: zu klein, um jemanden zu alarmieren, zu billig, um
es liegen zu lassen.

Tote Laufwerke bekommen Aufmerksamkeit, weil sie laut sind. Dies ist die andere
Art.

## Es redet Ihnen eine Anschaffung aus

Die meiste Überwachung bläst auf. Alles wird zum roten Alarm, also lesen Sie die
Alarme nicht mehr.

lsdsk stuft den Schweregrad herunter, wann immer die Maschine es nicht besser
gekonnt hätte. Eine PCIe-4.0-Karte in einem Gen3-Board bekommt einen
zurückhaltenden Hinweis statt einer Warnung, und die Rechnung steht daneben: die
zehn Laufwerke daran wollen etwa 6,00 GB/s, die Verbindung trägt 7,88 GB/s, die
Decke ist also echt und kostet Sie heute nichts. Sehen Sie erneut hin, wenn die
Zahl der Laufwerke wächst.

Diese Zurückhaltung ist die Funktion, nicht gutes Benehmen. Ein Werkzeug, das
immer nur eskaliert, erzieht Sie dazu, es zu ignorieren. Eines, das Ihnen sagt,
wann Sie sich keine Sorgen machen müssen, ist an dem Tag glaubwürdig, an dem es
sagt: dieses Laufwerk ersetzen.

Im selben Geist veröffentlicht es eine Liste dessen, was es nicht wissen kann.
Lesen Sie diese, bevor Sie irgendeine seiner Zahlen bei Ihrer Leitung zitieren.
