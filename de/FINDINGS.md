# Was es findet, und wie es entscheidet

[English](../FINDINGS.md) | **Deutsch**

Jede Regel, die lsdsk anwendet, welchen Beleg es braucht, bevor es etwas einen
Fehler nennt, und was es zu raten ablehnt. Zurück zur [README](README.md).

## Was es findet

- Eine Verbindung, die unterhalb dessen ausgehandelt wurde, was beide Enden
  unterstützen, was fast immer ein Kabel, ein Backplane-Platz oder ein
  Steckverbinder ist.
- CRC-Fehler der Schnittstelle, also Rahmen, die unterwegs beschädigt und erneut
  gesendet wurden. Das ist das Kabel und nicht das Laufwerk, und das Laufwerk zu
  tauschen behebt nichts.
- Das SMART-Gesamturteil, gerechnet so, wie das Laufwerk es rechnet: ausfallend,
  sobald ein bewertetes Attribut die Schwelle erreicht hat, die sein Hersteller
  gesetzt hat.
- Zwei Laufwerke in den falschen Anschlüssen: ein langsames hält einen schnellen
  Platz, auf den ein schnelleres wartet.
- Ein Controller in einem Steckplatz, der schmaler oder langsamer ist, als er
  braucht, mit dem freien Steckplatz, in den er gehört, oder eine Karte, die
  durch einen Platztausch nichts verlöre: eine Warnung, wenn die Laufwerke daran
  den schnelleren Steckplatz nutzen würden, ein Hinweis, wenn sie in den
  vorhandenen passen.
- Ein Controller, den das Mainboard begrenzt, mit der PCIe-Generation, die ihn
  anheben würde, und der Angabe, ob die angeschlossenen Laufwerke den
  Unterschied überhaupt nutzen können.
- Ein Laufwerk, das ein langsamerer Anschluss zurückhält, als es unterstützt.
- Verschleiss gegen einstellbare Bänder, sowie umgelagerte, schwebende und nicht
  korrigierbare Sektoren und NVMe-Medienfehler bei jedem Stand über null.
- Temperatur gegen die Grenzen, die das Laufwerk selbst veröffentlicht, nicht
  gegen eine Schätzung.
- Gleiche Modelle mit unterschiedlicher Firmware.
- Wo Platz für ein weiteres Laufwerk ist, und ob dieser Controller die
  Bandbreite hat, es zu speisen. Ein SAS-HBA beantwortet das genau, weil jeder
  Phy ein echter Anschluss ist, den der Kernel veröffentlicht. Ein
  AHCI-Controller beantwortet es nur, wenn seine Firmware eine Bitmaske der
  bestückten Anschlüsse veröffentlicht, und meldet sonst gar keine Zahl statt
  der aufgeblähten, die die Anschlussliste des Kernels ergäbe. Ein
  NVMe-Controller hat keinen freien Anschluss zu melden: er ist die Schnittstelle
  des Laufwerks selbst. Wo die Zahl fehlt, zeigt `lsdsk slots` trotzdem, welche
  PCIe-Anschlüsse frei sind.

Der Schweregrad wird danach abgestuft, was die Maschine überhaupt hätte liefern
können. Eine Karte, die unter ihrem eigenen Maximum läuft, in einem Board, das
nichts Schnelleres hat, ist ein zurückhaltender Hinweis und keine Warnung, denn
es gibt nichts zu beheben.

## Eine Zahl ist keine Rate

Ein Fehlerzähler liegt in der nichtflüchtigen Tabelle des Laufwerks selbst. Er
übersteht Neustarts, Stromausfälle und Neuinstallationen, und der Rechner kann
ihn nicht zurücksetzen. Genau das macht ihn vertrauenswürdig und zugleich für
sich allein fast nutzlos: er sagt, wie viel Schaden es je gab, nie wann.

Zwei Laufwerke einer Maschine zeigen es. Beide sind dasselbe Modell, beide
melden Hunderttausende CRC-Fehler der Schnittstelle. Eines hat in fünfzehn
Stunden sechzehntausend dazubekommen. Das andere keinen einzigen, und seine
eigene Lebensdauerrate sagt, dass in dieser Zeit ein paar hundert hätten
auftauchen müssen. Das erste beschädigt gerade jetzt Rahmen. Das zweite ist ein
Kabel, das jemand längst neu gesteckt hat, vermutlich vor Jahren.

Jedes Werkzeug, das nur den Stand liest, meldet die beiden gleich. lsdsk nicht,
weil es einen eigenen Verlauf führt:

```
device        counter           total  change  span  per hour  verdict
/dev/sdc      interface CRC     99361     +16   16h       1.0  rising
/dev/sdd      interface CRC   2196127  +16642   15h      1109  rising
/dev/sde      interface CRC       430      +0   15h         -  too soon to say, this drive's rate would not have produced even one in 15h
/dev/sdj      interface CRC    462640      +0   16h         -  no new in 16h, 235 were due
```

`span` ist das Fenster, das die Angabe unter `change` abdeckt, und es wird je
Zähler gemessen und nicht je Laufwerk: es reicht bis dorthin zurück, wo sich
dieser Zähler zuletzt bewegt hat, ein Laufwerk zeigt also berechtigterweise in
jeder seiner Zeilen eine andere Zahl.

Raten beziehen sich auf die Betriebsstunden des Laufwerks selbst und nicht auf
die Wanduhr. Die Uhr des Laufwerks läuft monoton, ignoriert Zeitsprünge und
Zeitzonen und läuft nicht weiter, während die Maschine aus ist; die Zahl bedeutet
also auch auf einem Rechner noch etwas, der zwei Wochen im Jahr läuft.

Die letzten beiden Zeilen sind der Teil, über den zu streiten sich lohnt.
Schweigen zählt nur dann als Beleg, wenn die eigene Vorgeschichte des Laufwerks
sagt, dass Fehler hätten auftauchen müssen: bei 430 Fehlern über zehntausend
Stunden beweisen fünfzehn stille Stunden nichts, und das Werkzeug sagt das,
statt anzudeuten, das Laufwerk sei in Ordnung. Eine feste Regel kann das nicht,
und eine feste Wochenregel weist auf dieser Maschine jedes Laufwerk ab,
einschliesslich dessen, dessen Fehler nachweislich vorbei ist.

Der Verlauf speist sich selbst. Jeder gewöhnliche Lauf legt eine Messung ab,
sobald die Uhren der Laufwerke weitergelaufen sind, es ist also nichts
einzurichten; `lsdsk record` aus einem systemd-Timer gibt es für die
unbeaufsichtigte Abtastung. Ein BERICHTENDER Befehl legt nichts ab, wenn er die
Aufnahme eines anderen abspielt oder wenn `--format json` verlangt wird, denn
ein Befehl in einer Pipeline soll den Zustand nicht hinter Ihrem Rücken ändern.
`record` ist die Ausnahme, weil das Ablegen einer Messung sein ganzer Zweck ist:
es schreibt unter beidem, und genau so faltet `lsdsk record --replay` eine alte
Aufnahme in den Verlauf.

`--no-record` steigt ganz aus, und `--history-file` legt den Speicher dorthin,
wo Sie ihn haben wollen. Beide sind global wie `--profile` und stehen daher vor
dem Unterbefehl: `lsdsk --history-file /var/lib/lsdsk.json record`. Für eine
dauerhafte Einstellung gibt es den Abschnitt `[history]` mit `enabled`, `path`
und `max_samples_per_drive`; `lsdsk config` zeigt die wirksamen Werte und woher
jeder stammt. Als root liegt der Speicher standardmässig unter
`/var/lib/lsdsk/history.json`, weil das Aufgezeichnete eine Eigenschaft der
Maschine ist und nicht dessen, der den Befehl getippt hat; ein Lauf ohne
Root-Rechte behält einen Pfad je Benutzer, da er dort ohnehin nicht schreiben
könnte. Der erste berichtende Lauf, der etwas ablegt, nennt die Datei, einmal;
`lsdsk record` schreibt stillschweigend, sofern nicht `--format json` verlangt
wird.

Jeder Wert, nach dem das Werkzeug urteilt oder ausrichtet, ist ein
Konfigurationsschlüssel: `[thresholds]` trägt die Verschleissbänder, die
Erheblichkeitsschwelle für CRC, die Zahl für nicht zusammenpassende Firmware und
die Angabe zum Schweige-Beleg, und `[display]` trägt die angenommene Breite bei
umgeleiteter Ausgabe, die Kappung der Zusammenfassung, den Verschleissboden, den
eine Verlaufszeile überschreiten muss, ob kernelvirtuelle Geräte aufgelistet
werden, die Obergrenze der Spalte wwn und die Traceback-Grenzen. Bewusst *nicht*
einstellbar ist alles, was eine Spezifikation festlegt: Registerabstände,
IOCTL-Codes, der Kelvin-Versatz, der 512-Byte-Sektor. Das sind keine
Entscheidungen, und eine Datei, die sie ändern könnte, würde die Dekodierung
kaputtmachen statt sie abzustimmen. Das Aufzeichnen abzuschalten hält den
Verlauf nie davon ab, *gelesen* zu werden, Befunde bleiben also in beiden Fällen
gegen die Vergangenheit abgestuft.

Die Tabelle `health` trägt dieselbe Unterscheidung in der Spalte, die Sie ohnehin
lesen: ein Stand, der weiter steigt, bekommt ein angehängtes `+`, und einer,
dessen Stillstand bewiesen ist, fällt aus dem Rot heraus. Eine rote Zahl, die
sich nie ändert, ist die Art, wie ein Werkzeug Ihnen beibringt, Rot zu
ignorieren.

Eine Folge, die Sie kennen sollten, wenn Sie auf den Exit-Code alarmieren: ein
Fehler, dessen Ende der Verlauf beweist, wird zu einem Hinweis herabgestuft, und
ein Hinweis zählt nicht als handelnswert. Ein Rechner, dessen einzige Klage ein
längst behobener Kabelfehler war, wandert von Exit `1` zu Exit `0`. Das ist die
richtige Antwort, und es ist eine Änderung, wenn Sie einen Cron-Job daran binden.

## Wo könnte diese Karte hin

`lsdsk slots` bringt das ganze Board auf einen Bildschirm: jeden PCIe-Anschluss,
wozu er imstande ist, was er ausgehandelt hat, was ihn belegt, was diese Belegung
tatsächlich braucht, und ein Urteil. Ein freier Anschluss wird benannt, und eine
Karte, die Bandbreite ungenutzt lässt, wird mit der Zahl benannt, ein Tausch ist
also offensichtlich, bevor Sie das Gehäuse öffnen.

```
MSI MEG Z690 ACE (MS-7D27)   18 ports   3 free
   port          slot  capable              running             occupant                        needs               verdict
   0000:00:01.0  #1    Gen5x8 (31.50 GB/s)  Gen3x8 (7.88 GB/s)  AMD Hawaii XT [Radeon R9 290X]  Gen3x16 (15.76 GB/s)  in use (graphics)
   0000:00:01.1  #2    Gen5x8 (31.50 GB/s)  Gen2x8 (4.00 GB/s)  Intel 82599ES 10G SFI/SFP+      Gen2x8 (4.00 GB/s)    spare 27.50 GB/s
   0000:00:1d.0  #12   Gen3x4 (3.94 GB/s)   Gen3x4 (3.94 GB/s)  Samsung 980 PRO 2TB             Gen4x4 (7.88 GB/s)    port limits it
   0000:00:1c.0  #0    Gen3x1 (0.98 GB/s)   -                   empty                           -                     FREE
```

Es sagt nicht, ob ein Anschluss ein M.2-Sockel oder ein Kartensteckplatz ist,
und das ist Absicht. Die Steckplatztabelle der Firmware ist das Einzige, was die
Bauform trägt, und auf drei hier vermessenen Boards nannte sie überhaupt keinen
M.2-Sockel, während sie die meisten ihrer Busadressen falsch angab. Die Ansicht
trägt stattdessen die Steckplatznummer des Boards, die Sie gegen das Handbuch
halten. Ungenutzte Bandbreite wird berichtet, gleich ob ein Umstecken
vorgeschlagen werden kann, denn die Zahl ist in beiden Fällen gemessen.

## Es sagt Ihnen, wessen Hardware Sie ansehen

Speicherwerkzeuge laufen regelmässig in einem Container oder einem Gast, wo die
Antwort etwas anderes bedeutet. lsdsk erkennt, welches von beidem, und sagt es:

- In einem **Container** sehen Sie die echte Hardware des Wirts durch einen
  gemeinsamen Kernel. Die Fehler sind echt, sie gehören nur dem Wirt, und die
  fehlenden SMART-Daten sind fehlende Geräteknoten und keine Rechtefrage; es
  schickt Sie also nicht los, es mit `sudo` zu versuchen.
- In einer **virtuellen Maschine** sind die Platten und die
  Verbindungsgeschwindigkeiten eine Erfindung des Hypervisors, die Regeln zu
  Verbindung und Bestückung werden daher unterdrückt, statt Kabelfehler an einem
  emulierten Controller zu melden.
