# An lsdsk entwickeln

[English](../DEVELOPMENT.md) | **Deutsch**

## Einrichten

```bash
uv sync --extra dev          # legt .venv mit den Entwicklungswerkzeugen an
.venv/bin/lsdsk --version
```

Python 3.11 oder neuer. Das Projekt zielt auf Linux und Windows; die Testsuite
läuft auf beiden, und die plattformabhängigen Leser werden über mitgelieferte
Aufnahmen auch auf der jeweils anderen Plattform geübt.

## Das Bausystem

Alles läuft über [bmk](https://github.com/bitranox/bmk), das das `Makefile`
ERZEUGT. Die erste Zeile des `Makefile` lautet `# BMK MAKEFILE <version>`, und
jedes Ziel, das bmk aufruft, schreibt sie neu. Ein verändertes `Makefile` im
Arbeitsbaum nach einem `make test` ist also zu erwarten und kann bedenkenlos
committet werden.

**`make help` ist die massgebliche Liste der Ziele.** Es liest die Zielkommentare
des Makefile selbst aus und kann daher nicht im Widerspruch zu dem stehen, was
tatsächlich läuft. Dieses Dokument wiederholt sie bewusst nicht: eine von Hand
kopierte Liste veraltet in dem Moment, in dem bmk die Datei neu erzeugt.

Die gebräuchlichsten:

| Befehl                 | Was es tut                                                                          |
|------------------------|-------------------------------------------------------------------------------------|
| `make test`            | Die Prüfkette: Formatierung, Lint, Typprüfung, Import-Verträge, Sicherheit, pytest  |
| `make testintegration` | Nur die als `integration` markierten Tests, die echte Maschinen brauchen            |
| `make test-all`        | pytest und pyright auf jeder deklarierten Python-Version, nicht die ganze Prüfkette |
| `make run`             | Die CLI über bmk ausführen                                                          |
| `make build`           | Wheel und sdist nach `dist/` bauen                                                  |
| `make push`            | Die Prüfkette laufen lassen, dann committen und pushen                              |
| `make release`         | Taggen und veröffentlichen                                                          |

Den Rest zeigt `make help`, und die Optionen eines Befehls `bmk <befehl> --help`.

## Tests

```bash
make test                    # die Prüfkette, und das, was die CI ausführt
.venv/bin/python -m pytest tests/ -q          # nur pytest, schneller beim Arbeiten
.venv/bin/python -m pytest tests/ -q -k trend # ein Bereich
```

`make test` ist die Prüfkette vor dem Push. Sie ist eine echte Obermenge jeder
Teilmenge, die Sie von Hand laufen lassen könnten: ein grünes `ruff` plus ein
grünes `pytest` ist also nicht dasselbe Signal.

### Marker

| Marker                                              | Bedeutung                                                     |
|-----------------------------------------------------|---------------------------------------------------------------|
| `os_agnostic`                                       | Läuft überall                                                 |
| `os_posix` / `os_linux` / `os_macos` / `os_windows` | Wird sonst übersprungen, verdrahtet in `tests/conftest.py`    |
| `integration`                                       | Braucht externe Maschinen; aus `make test` ausgeschlossen     |
| `local_only`                                        | Braucht die Maschinen dieses Entwicklers; läuft nie in der CI |

Ein Marker überspringt nur deshalb etwas, weil `tests/conftest.py` ihn
verdrahtet. Ihn in `pyproject.toml` einzutragen bringt die Warnung über
unbekannte Marker zum Schweigen und überspringt nichts.

### Tests auf echter Hardware

`tests/e2e/` schickt eine Sonde über ssh auf echte Maschinen und prüft den
Vertrag dort. Eine abgespielte Aufnahme beweist die Dekoder und die Bauer; den
Leser der Plattform kann sie nicht beweisen, denn eine Aufnahme enthält bereits
das, was der Leser zu holen vermochte.

Sie brauchen Maschinen, die Hostliste steht daher in `.env`, die nie committet
wird:

```bash
LSDSK_E2E_HOSTS="server-a:root:linux,workstation-b:admin:windows"
```

Das Format steht in `.env.example`. Ohne sie überspringen die Tests und sagen
es. Jeder Lauf berichtet, was er berührt hat, sowohl in der Zusammenfassung im
Terminal als auch in `e2e-coverage.json`, denn `make testintegration` gibt nur
den Ergebnisumschlag von bmk aus, und ein Durchgang über null Maschinen sähe
sonst genauso aus wie ein vollständiger.

### Fixtures

`tests/fixtures/hw/*.json` sind Aufnahmen echter Maschinen, erzeugt vom
produktiven Leser, in denen die Seriennummern der Laufwerke und die
Maschinennamen durch erfundene ersetzt sind, damit das Repository nicht
veröffentlicht, um welche physischen Geräte es sich handelt. Modelle,
Firmwarestände und jede Messung sind wie aufgenommen.

Jede ist danach benannt, was sie enthält, und nicht danach, woher sie kommt; ein
Test, der eine nennt, sagt damit, was er prüft: `linux-sas-hba`,
`linux-nvme-board`, `linux-minimal`, `windows-ahci`. `linux-sas-hba-later` ist
dieselbe Aufnahme zu einem späteren Zeitpunkt, und die beiden teilen sich
absichtlich einen Hostnamen, weil die Verlaufstests ihn brauchen: der Speicher
weigert sich, zwei Maschinen zu vermischen.

Auffrischen oder neu anlegen:

```bash
lsdsk snapshot -o /tmp/capture.json
```

Dann umbenennen, das Feld `hostname` neu schreiben und die Seriennummern
ersetzen, bevor Sie committen. Eine Aufnahme hält dieselbe Seriennummer an bis
zu acht Stellen fest, darunter zwei, die sie vor jeder Textsuche verbergen: die
NVMe-Subsystem-NQN bettet sie in eine Namenszeichenkette ein, und das `wwid` in
sysfs bettet sie als Hex ein. Nur das JSON-Feld zu säubern, oder nur die
Stellen, die man sieht, lässt die echte eingebettet zurück und bringt die
Fixture in Widerspruch zu sich selbst.

Statt nach einer Liste zu arbeiten, führen Sie
`pytest tests/test_fixture_serials.py` aus: es dekodiert jede Stelle und schlägt
fehl unter Nennung jeder einzelnen, deren Seriennummer von dem abweicht, was das
Werkzeug für dieses Laufwerk meldet. Das ist die Prüfung, die vor dem Committen
einer Aufnahme bestanden sein muss.

Prüfen Sie auf Bereiche statt auf genaue Werte, wo sich etwas von selbst ändert:
Betriebsstunden und Verschleiss steigen nur, und eine genaue Zusicherung geht
beim nächsten Neuaufnehmen einer Fixture kaputt.

## Codequalität

Die Schichtgrenzen werden durch import-linter-Verträge in `pyproject.toml`
durchgesetzt, `lint-imports` prüft sie. Die Typprüfung ist pyright im
strict-Modus, und Typlücken von Drittbibliotheken werden mit typisierten Fassaden
geschlossen statt mit Unterdrückungen: siehe `adapters/cli/typed_click.py` und
`adapters/tui/typed_table.py`.

`make test` führt `pip-audit` aus. Ein Befund wird behandelt, indem die
Mindestversion der betroffenen Abhängigkeit in `pyproject.toml` angehoben wird,
in `[project].dependencies` oder im dev-Extra, je nachdem, wo sie liegt, mit der
CVE als Kommentar daneben.

## CI und Veröffentlichung

Die Workflows liegen in `.github/workflows/` und werden von einer externen
Vorlage verwaltet. **Bearbeiten Sie sie nicht in diesem Repository**; ändern Sie
sie in der Vorlage und verteilen Sie sie neu.

| Workflow                     | Wann                     |
|------------------------------|--------------------------|
| `default_cicd_public.yml`    | Push und Pull Request    |
| `default_release_public.yml` | Beim Veröffentlichen     |
| `codeql.yml`                 | Wöchentlich, plus Pushes |

Veröffentlicht wird über bmk (`make release` / `make ship`), das `vX.Y.Z` an der
Version taggt, die bereits in `pyproject.toml` steht, und sie veröffentlicht.
Keines von beidem hebt die Version an: das ist die getrennte Familie
`make bump-patch` / `bump-minor` / `bump-major`, und zuerst anzuheben würde die
neue Nummer taggen und die committete überspringen. Die Veröffentlichung
authentifiziert sich entweder mit dem Secret `PYPI_API_TOKEN` oder, wenn dieses
Secret fehlt, über einen Trusted Publisher bei PyPI mit der OIDC-Identität des
Workflows.

Die Version steht in `pyproject.toml` und wird in `__init__conf__.py` und
`.claude-plugin/plugin.json` gespiegelt; `tests/test_metadata_sync.py` schlägt
fehl, sobald eine der Kopien abweicht.

## Dieses Repository ist auch ein Marktplatz für Claude Code

Es liefert den Skill unter `skills/lsdsk/` mit. Heben Sie `version` in
`.claude-plugin/plugin.json` bei jeder ausgelieferten Änderung an, sonst holen
bestehende Installationen nie neu. `skills/lsdsk/SKILL.md` wird ausschliesslich
über das Verfahren `bitranox:meta-skill-writer` bearbeitet; eine Absicherung
erzwingt das.
