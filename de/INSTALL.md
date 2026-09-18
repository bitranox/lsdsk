# lsdsk installieren

[English](../INSTALL.md) | **Deutsch**

Python 3.11 oder neuer, unter Linux oder Windows. Jede andere Plattform führt
`--replay` gegen eine Aufnahme aus, die auf einer der beiden entstanden ist.

## Am einfachsten: installieren und ausführen mit uv

`uv` holt das passende Python und das Werkzeug selbst, eine Zeile genügt also
für ein lauffähiges `lsdsk`, ohne dass vorher irgendetwas eingerichtet wird:

```bash
uvx lsdsk@latest                    # einmal ausführen, nichts installieren
uv tool install lsdsk               # dauerhaft im PATH behalten
```

Falls Sie `uv` noch nicht haben: die Projektseite trägt den Einzeiler für Linux,
macOS und Windows, <https://docs.astral.sh/uv/getting-started/installation/>
(Projektstartseite: <https://docs.astral.sh/uv/>).

Der Rest dieser Seite behandelt die anderen Wege: pipx und pip, ein Checkout,
und ein Wheel auf einer Maschine, die keines von beidem hat.

## Von PyPI ohne uv

```bash
pipx install lsdsk                  # isoliert, im PATH
pip install lsdsk                   # innerhalb einer virtuellen Umgebung
lsdsk --version
```

`uv tool upgrade lsdsk` bringt eine uv-Installation auf die aktuelle Version,
`pipx upgrade lsdsk` tut dasselbe für eine mit pipx.

Die Installation legt ein Konsolenskript an, `lsdsk`.

## Aus einem Checkout

Für die Arbeit an lsdsk selbst, oder um eine Fassung zu fahren, die nicht
veröffentlicht ist.

```bash
git clone https://github.com/bitranox/lsdsk.git
cd lsdsk
uv sync                      # oder: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/lsdsk --version
```

`uv sync` legt `.venv` an und installiert das Projekt. `dev` ist ein Extra und
keine Dependency-Group, die Test- und Lint-Werkzeuge kommen also erst mit
`uv sync --extra dev`. Das Konsolenskript landet unter `.venv/bin/lsdsk`
(unter Windows `.venv\Scripts\lsdsk.exe`).

Um es für den täglichen Gebrauch in den PATH zu legen:

```bash
uv tool install .            # aus dem Checkout-Verzeichnis heraus
lsdsk --version
```

## Aus einem Wheel

Nützlich für eine Maschine ohne Checkout, was der übliche Weg auf einen Server
ist:

```bash
make build                   # schreibt dist/lsdsk-<version>-py3-none-any.whl
```

Dann auf der Zielmaschine:

```bash
uv tool install ./lsdsk-<version>-py3-none-any.whl
# oder, ohne irgendetwas dauerhaft zu installieren:
uv run --with ./lsdsk-<version>-py3-none-any.whl lsdsk
```

`pip install ./lsdsk-<version>-py3-none-any.whl` tut in einer virtuellen
Umgebung dasselbe.

## Was Root-Rechte braucht

lsdsk läuft ohne Privilegien und sagt, was das kostet. Topologie,
Verbindungsgeschwindigkeiten, Kapazität und Firmware werden ohne jedes Recht
gelesen. Vier Dinge brauchen root oder Administrator: die SMART-Attribute und
der Verschleiss, die Fehlerzähler, die `trend` und `record` verwenden, die
Erkennung physischer PCIe-Steckplätze, und das AHCI-Fähigkeitsregister, aus dem
ein SATA-Controller seine Anschlussgeschwindigkeit und die Zahl freier
Anschlüsse bekommt. Ohne sie lesen die betroffenen Spalten `-`, und der Kopf der
Ausgabe sagt es.

Das Lesen der Zähler braucht root, ein geplantes `lsdsk record` gehört also in
die crontab von root oder in einen systemd-Timer, nicht in die eines Benutzers.

## Eine Installation prüfen

```bash
lsdsk --version                     # gibt die Version aus
lsdsk --help                        # listet jeden Befehl
lsdsk                               # liest diese Maschine
```

## Deinstallieren

```bash
uv tool uninstall lsdsk             # oder: pipx uninstall lsdsk / pip uninstall lsdsk
```

Konfiguration und Zählerverlauf bleiben liegen. Entfernen Sie sie von Hand, wenn
Sie sie loswerden wollen: `lsdsk config` gibt aus, woher die Konfiguration
geladen wurde, und `lsdsk record --format json` nennt unter `store` den Pfad des
Verlaufsspeichers.
