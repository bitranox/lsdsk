# Unsere Haltung zu KI

[English](../ai-stance.md) | **Deutsch**

Wann immer bei einem Softwareprojekt KI zur Sprache kommt, fällt das Gespräch
meist auf eine einzige Frage zusammen: hat ein Modell den Code geschrieben? Wir
halten das für den falschen Ausgangspunkt.

Was Sie über ein Stück Software vermutlich wissen wollen, ist älter als jedes
Sprachmodell. Tut es, was die Dokumentation sagt? Geht es mit Ihren Daten so um,
wie Sie es erwarten würden? Kann jemand es reparieren, wenn es kaputtgeht? Gibt
es am anderen Ende einen Menschen, der dafür geradesteht? Diese Fragen zählen,
ob der Code nun von Hand getippt, erzeugt oder aus einem Forenbeitrag von 2009
gezogen wurde.

Ein Modell kann etwas entwerfen und die Tastatur beschleunigen. Es entscheidet
nicht, was ein Projekt tun soll oder welche Abwägungen es wert sind. Diese
Entscheidungen gehören denen, die das Projekt betreiben, und das gilt
unabhängig davon, ob jemand Hilfe beim Tippen hatte.

Statt "war KI beteiligt" wäre uns also lieber, Sie fragten:

- Verstehen die Betreuer wirklich, was sie gebaut haben?
- Sind die Entwurfsentscheidungen vertretbar?
- Steht jemand gerade, wenn etwas schiefgeht?
- Kann ein Aussenstehender die Behauptungen selbst prüfen?

Unsere Sicht auf KI ist schlicht. Sie ist ein Werkzeug. Mit Aufmerksamkeit
eingesetzt, macht sie einen fähigen Entwickler schneller. Achtlos eingesetzt,
erzeugt sie Ergebnisse, die in Ordnung aussehen, bis man sie ein zweites Mal
liest. Den Unterschied zu erkennen ist Aufgabe des Menschen.

## Die Kurzfassung

Wie die meisten heutigen Entwicklerteams verwenden wir KI-gestützte Werkzeuge
für Dinge wie Codevervollständigung, Erzeugung von Dokumentation, Automatisierung
von Reviews und Sicherheitsprüfungen. Entscheidungen zu Architektur, Umsetzung
und Tests bleiben bei unserem Team. Für das grössere Bild siehe
[IBM](https://www.ibm.com/solutions/ai-coding),
[IBM-Fallstudien](https://www.ibm.com/case-studies/ibm-software-team) und die
[MIT Technology Review](https://www.technologyreview.com/2025/12/15/1128352/rise-of-ai-coding-developers-2026/).

Besonders die KI-gestützte Sicherheitsprüfung ist zu gängiger Praxis der Branche
geworden. Siehe
[The Hacker News](https://thehackernews.com/2026/02/claude-opus-46-finds-500-high-severity.html)
und [IBM Research](https://www.ibm.com/think/insights/chatgpt-4-exploits-87-percent-one-day-vulnerabilities).

So sieht das in unserer Arbeit aus.

## Wo wir KI einsetzen

### Nachschlagen

Wenn wir auf eine unbekannte Bibliothek, ein unbekanntes Protokoll oder ein
fremdes Stück Codebasis stossen, ist KI oft der schnellste Weg hinein. Wir lassen
uns etwas zusammenfassen, auf die richtige Seite der Dokumentation zeigen oder
eine vorhandene Umsetzung gemeinsam durchgehen. Es ist ein schnellerer Zugang zu
Wissen, das ohnehin irgendwo existiert. Was wir mit diesem Wissen tun, bleibt
unsere Sache.

### Laut denken

Die eigentliche Arbeit an einem Projekt geschieht meist vor der ersten Zeile
Code: herauszufinden, wie die Teile zusammenpassen, wo die Komplexität liegen
soll, was unter Last zuerst versagt. Dieser Teil ist überwiegend Denken.

Hier ist KI als Sparringspartner nützlich. Wir beschreiben einen Ansatz und
lassen Löcher hineinschiessen. Manchmal fördert sie einen Randfall zutage, den
wir übersehen hatten. Manchmal bietet sie eine Sichtweise, die einen Knoten
leichter lösbar macht. Manchmal erfindet sie selbstbewusst etwas, das es nicht
gibt. Der Kniff besteht darin, mit ihr zu streiten und ihr nicht zu glauben.

### Code schreiben

Sobald wir wissen, was wir bauen, kann KI beim Tippen helfen. Mal ist das ein
grober erster Entwurf eines Moduls. Mal Gerüstcode, mal Klebecode zwischen
Schichten, mal Tests und Logging. Bei schwereren Problemen läuft es eher auf
Pair Programming hinaus: etwas versuchen, es gemeinsam ansehen, überarbeiten,
wiederholen.

Vieles von dem, was erzeugt wird, bleibt. Vieles nicht. Modelle erfinden
bereitwillig eine Funktion, die es in der verwendeten Bibliothek nicht gibt. Sie
übersehen eine Anforderung, die zwei Absätze vorher stand. Sie schlagen eine
Form vor, die sich mit dem Rest der Codebasis beisst. Jede Zeile muss weiterhin
von einem Menschen gelesen und verantwortet werden, bevor sie ausgeliefert wird.

### Texte aufräumen

Für Dokumentation, READMEs, Versionshinweise und ähnliches geschriebenes
Material verwenden wir KI ungefähr so, wie man eine klügere Rechtschreibprüfung
verwendet. Grammatik, Einheitlichkeit, Formatierung, gelegentlich ein kaputter
Link. Die Gedanken und der Aufbau bleiben unsere.

### Bilder und Diagramme

Wo wir erzeugte Bilder zur Ausschmückung oder Veranschaulichung verwenden,
behandeln wir sie als genau das: Beiwerk, keine eigenständige schöpferische
Arbeit. Wenn es für den Zusammenhang eine Rolle spielt, dass ein Bild erzeugt
wurde, sagen wir es.

## Wo wir KI nicht einsetzen

Einiges halten wir auf der menschlichen Seite der Linie.

Entscheidungen, für die jemand geradestehen muss: die architektonische Richtung,
Abwägungen zur Sicherheit, das Urteil, ob etwas gut genug zum Ausliefern ist.

Und Code, den wir nicht am Whiteboard verteidigen könnten.

## Verantwortung

Ob ein Modell im Spiel war oder nicht, die Arbeit muss trotzdem standhalten.
Jedes Projekt, das wir ausliefern, durchläuft daher ungefähr dieselbe Routine.

Ein Mensch liest die Änderung, bevor sie übernommen wird. Sie wird überprüft, durch
Tests oder von Hand, je nachdem, was angemessen ist. Sie ist gut genug
dokumentiert, dass sich das beabsichtigte Verhalten gegen das tatsächliche
prüfen lässt. Die Begründung einer Änderung steht dort, wo die Änderung steht:
in der Commit-Nachricht, im Changelog und in den Entwurfsnotizen unter `docs/`,
dazu in jedem Issue und jedem Pull Request, den sie durchlaufen hat.

Wenn etwas, das wir ausliefern, falsch, unsicher oder schlecht entworfen ist,
ist es uns lieber, Sie sagen es öffentlich, als dass Sie höflich darüber
hinweggehen. Der Sinn der Arbeit im Offenen ist, dass Fehler ebenfalls offen
liegen.

## Unsere Arbeit nachprüfen

Sie müssen nichts davon glauben.

- Der Quelltext liegt offen. Lesen Sie ihn.
- Die Historie auch. Entscheidungen und Rücknahmen werden mit der Zeit sichtbar.
- Commit-Nachrichten und Entwurfsnotizen tragen die Begründung und nicht nur das
  Ergebnis, ebenso die Issues und Pull Requests, die eine Änderung durchlaufen
  hat.
- Die Tests liegen im Repository. Führen Sie sie aus.

Wenn etwas nicht zusammenpasst, öffnen Sie ein Issue. Dafür sind sie da.

## Warum wir das aufschreiben

Keines unserer Projekte ist ungewöhnlich genug, um ein Manifest zu brauchen. Wir
schreiben das, weil das Gespräch über KI in der Softwareentwicklung weitergehen
wird und die Gewohnheiten darum herum noch nicht gut sind.

Modelle verschwinden nicht, und sie werden besser werden. Mehr Entwickler werden
sie verwenden, manche still, manche laut, manche gut, manche schlecht. Viel
Software, die KI berührt hat, wird schlecht sein. Viel wird in Ordnung sein.
Dass ein Modell im Arbeitsablauf vorkam, sagt für sich genommen nicht, welches
von beidem.

Was fehlt, ist ein vernünftiger Mittelweg. Weniger Getöse und weniger zur Schau
gestellte Reinheit, dafür mehr klare Auskunft darüber, was tatsächlich getan
wurde.

Wenn Sie eines unserer Projekte verwenden und wissen wollen, wie KI darin
vorkommt, ist das eine berechtigte Frage, und Sie sollten eine fundierte
Antwort bekommen. Umgekehrt sollte sorgfältige Arbeit nicht als Slop
abgetan werden, bloss weil irgendwo in der Kette ein Modell war.

Diese Seite ist der Massstab, an dem wir uns zu halten versuchen. Wenn sie
jemanden dazu anstösst, seine eigene Fassung zu schreiben, ist das ein gutes
Ergebnis.
