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

## Warum wir uv empfehlen: Schutz der Lieferkette

Dieses Repository enthält eine `uv.lock`. Sie legt jedes Paket, das das Projekt braucht,
transitive Abhängigkeiten eingeschlossen, auf eine genaue Version und auf die Prüfsummen der
Dateien dieser Version fest. Die Einstellung `exclude-newer` unter `[tool.uv]` in
`pyproject.toml` fügt eine Wartezeit hinzu: uv lässt jede Veröffentlichung, die jünger ist, aus
der Sperrdatei heraus. CI installiert aus der Sperrdatei, also erreicht eine gekaperte
Veröffentlichung, die innerhalb der Wartezeit wieder zurückgezogen wird, unsere Builds nie, und
eine Datei mit falscher Prüfsumme wird abgewiesen.

Dieser Schutz bleibt in diesem Repository. Das installierbare Paket gibt für seine Abhängigkeiten
nur Untergrenzen an (`>=`), also nimmt ein einfaches `pip install` von jeder transitiven
Abhängigkeit die neueste Veröffentlichung im Moment des Aufrufs. Wurde eine davon eine Stunde
vorher gekapert, bekommt man die gekaperte Version. `pipx install` und `uv tool install` ohne
Optionen tun dasselbe.

Um das Kommandozeilenwerkzeug mit einer eigenen Wartezeit zu installieren (uv überspringt dann
jede Veröffentlichung, die jünger als 7 Tage ist, transitive eingeschlossen), oder um genau den
Stand auszuführen, den unsere CI testet:

```bash
uv tool install --exclude-newer "7 days" lsdsk
git clone https://github.com/bitranox/lsdsk.git && cd lsdsk && uv sync --locked && uv run lsdsk --help
```

Wer lsdsk im eigenen Projekt als Abhängigkeit nutzt, gibt diesem Projekt dieselbe Wartezeit (hier
als Beispiel 7 Tage) und committet die `uv.lock`, die `uv add lsdsk` schreibt:

```toml
[tool.uv]
exclude-newer = "7 days"
```

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
der Verschleiß, die Fehlerzähler, die `trend` und `record` verwenden, die
Erkennung physischer PCIe-Steckplätze, und das AHCI-Fähigkeitsregister, aus dem
ein SATA-Controller seine Anschlussgeschwindigkeit und die Zahl freier
Anschlüsse bekommt. Unter Linux vor Kernel 6.9 kommt ein fünftes dazu: was eine
USB-Platte selbst kann, das ältere Kernel nur über usbfs veröffentlichen. Ohne sie lesen die betroffenen Spalten `-`, und der Kopf der
Ausgabe sagt es.

Das Lesen der Zähler braucht root, ein geplantes `lsdsk record` gehört also in
die crontab von root oder in einen systemd-Timer, nicht in die eines Benutzers.

## Eine Installation prüfen

```bash
lsdsk --version                     # gibt die Version aus
lsdsk --help                        # listet jeden Befehl
lsdsk                               # liest diese Maschine
```

## Desktop-Verknüpfung

lsdsk kann eine Desktop-Verknüpfung anlegen, die es startet, damit man den
Befehl nicht jedes Mal eintippen muss. Die interaktive Ansicht bietet eine an,
wenn man lsdsk das erste Mal an einem Terminal ausführt, und man kann sie auch
ausdrücklich anfordern:

```bash
lsdsk install-shortcut              # die Verknüpfung für diese Plattform schreiben
lsdsk install-shortcut --install-uv # und uv zuerst einrichten, wenn keines da ist
```

Unter Linux schreibt es einen `.desktop`-Eintrag, unter macOS eine
`.command`-Datei. Windows wird noch nicht unterstützt: Dort bietet die
interaktive Ansicht keine Verknüpfung an, und `lsdsk install-shortcut` lehnt mit
dem Hinweis ab, dass sie in diesem Build noch nicht verfügbar ist. Wo `uvx`
vorhanden ist, startet die Verknüpfung lsdsk darüber,
mit derselben 7-Tage-Wartezeit, die das Projekt festlegt, so bekommt ein Start
vom Desktop den Lieferkettenschutz, den ein einfacher `uvx`-Lauf hätte. (Ist nur
ein ganz normal installiertes `lsdsk` im PATH, startet sie dieses stattdessen.)
Der `uvx`-Aufruf sieht so aus:

```bash
uvx --exclude-newer "7 days" --exclude-newer-package <owned-package>=false lsdsk@latest
```

mit einem `--exclude-newer-package <name>=false` je Paket, das lsdsk in seiner
Abhängigkeits-Hülle besitzt, was diese von der Wartezeit ausnimmt, damit eine
neue lsdsk-Veröffentlichung nicht zurückgehalten wird.

Hat die Maschine kein uv, gibt es noch nichts, was die Verknüpfung starten
könnte. Um uv selbst einzurichten (neueste Version), führen Sie den offiziellen
astral.sh-Installer aus:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh                                       # Linux und macOS
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"    # Windows
```

Mit `--install-uv`, oder wenn man das interaktive Angebot annimmt, richtet lsdsk
uv aus demselben offiziellen Installer für Sie ein, erst nach ausdrücklicher
Bestätigung. Die eigene Einrichtung von lsdsk legt eine bekannte uv-Version fest,
statt die jeweils neueste zu holen, der Befehl, den lsdsk ausführt, nennt also
eine feste Version, während die Zeile oben der neuesten folgt. Setzen Sie
`display.offer_desktop_shortcut = false`, um das interaktive Angebot abzuschalten
(siehe [CONFIG.md](CONFIG.md)).

## Deinstallieren

```bash
uv tool uninstall lsdsk             # oder: pipx uninstall lsdsk / pip uninstall lsdsk
```

Konfiguration und Zählerverlauf bleiben liegen. Entfernen Sie sie von Hand, wenn
Sie sie loswerden wollen: `lsdsk config` gibt aus, woher die Konfiguration
geladen wurde, und `lsdsk record --format json` nennt unter `store` den Pfad des
Verlaufsspeichers.
