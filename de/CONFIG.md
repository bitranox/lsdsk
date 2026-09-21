# Konfigurationssystem

[English](../CONFIG.md) | **Deutsch**

Dieses Projekt verwendet [`lib_layered_config`](https://github.com/bitranox/lib_layered_config), um die Konfiguration über ein System zusammengeführter Schichten zu verwalten. Konfigurationswerte werden aus mehreren Quellen geladen und in einer festgelegten Reihenfolge zusammengeführt, was Überschreibungen von systemweiten Vorgaben bis hinunter zu einzelnen Kommandozeilenargumenten erlaubt.

## Grundbegriffe

- **Zusammenführen in Schichten**: Die Konfiguration wird aus mehreren Dateien und Quellen gebildet, wobei spätere Schichten frühere überschreiben
- **Plattformübergreifende Pfade**: folgt unter Linux den XDG-Konventionen, unter macOS und Windows den dort üblichen Orten
- **Profile**: benannte Profile erlauben umgebungsabhängige Konfigurationen (etwa `production`, `staging`, `test`)
- **TOML-Format**: alle Konfigurationsdateien verwenden TOML-Syntax
- **Überschreiben zur Laufzeit**: Werte lassen sich über Umgebungsvariablen oder CLI-Schalter überschreiben, ohne Dateien zu ändern

---

## Die Schichten

Die Konfiguration wird in dieser Reihenfolge geladen und zusammengeführt (vom niedrigsten zum höchsten Vorrang):

| Vorrang | Schicht      | Beschreibung                                          |
|:-------:|--------------|-------------------------------------------------------|
| 1       | **defaults** | Mit dem Paket mitgeliefert (`defaultconfig.toml`)     |
| 2       | **app**      | Systemweite Einstellungen für alle Maschinen          |
| 3       | **host**     | Maschinenspezifische Überschreibungen                 |
| 4       | **user**     | Persönliche Einstellungen des Benutzers               |
| 5       | **.env**     | dotenv-Datei im Projektverzeichnis                    |
| 6       | **env vars** | Umgebungsvariablen                                    |
| 7       | **CLI**      | `--set`-Schalter der Kommandozeile (höchster Vorrang) |

**Verhalten beim Zusammenführen**: Jede Schicht muss nur die Werte nennen, die sie überschreiben will. Nicht genannte Werte erbt sie von den darunterliegenden Schichten.

---

## Wo die Dateien liegen

### Pfade je Plattform

| Schicht  | Linux                                   | macOS                                                               | Windows                                               |
|----------|-----------------------------------------|---------------------------------------------------------------------|-------------------------------------------------------|
| defaults | (mit dem Paket mitgeliefert)            | (mit dem Paket mitgeliefert)                                        | (mit dem Paket mitgeliefert)                          |
| app      | `/etc/xdg/{slug}/config.toml`           | `/Library/Application Support/{vendor}/{app}/config.toml`           | `C:\ProgramData\{vendor}\{app}\config.toml`           |
| host     | `/etc/xdg/{slug}/hosts/{hostname}.toml` | `/Library/Application Support/{vendor}/{app}/hosts/{hostname}.toml` | `C:\ProgramData\{vendor}\{app}\hosts\{hostname}.toml` |
| user     | `~/.config/{slug}/config.toml`          | `~/Library/Application Support/{vendor}/{app}/config.toml`          | `%APPDATA%\{vendor}\{app}\config.toml`                |

### Platzhalter in den Pfaden

| Platzhalter  | Linux    | macOS / Windows |
|--------------|----------|-----------------|
| `{slug}`     | `lsdsk`  | -               |
| `{vendor}`   | -        | `bitranox`      |
| `{app}`      | -        | `lsdsk`         |
| `{hostname}` | Hostname | Hostname        |

### Beispiele

**Linux:**
- Benutzerkonfiguration: `~/.config/lsdsk/config.toml`
- App-Konfiguration: `/etc/xdg/lsdsk/config.toml`
- Host-Konfiguration: `/etc/xdg/lsdsk/hosts/myserver.toml`

**macOS:**
- Benutzerkonfiguration: `~/Library/Application Support/bitranox/lsdsk/config.toml`

**Windows:**
- Benutzerkonfiguration: `%APPDATA%\bitranox\lsdsk\config.toml`

---

## CLI-Befehle

### Globale Optionen

Diese Optionen gelten für alle Befehle und stehen **vor** dem Befehlsnamen:

| Option                    | Beschreibung                                                                                                        |
|---------------------------|---------------------------------------------------------------------------------------------------------------------|
| `--version`               | Version ausgeben und beenden.                                                                                       |
| `--profile NAME`          | Konfiguration aus einem benannten Profil laden (etwa `production`, `test`).                                         |
| `--set SECTION.KEY=VALUE` | Eine Einstellung überschreiben. Für mehrere Überschreibungen wiederholbar.                                          |
| `--env-file PATH`         | Ausdrücklicher Pfad zu einer `.env`. Überspringt die Suche nach oben im Verzeichnisbaum.                            |
| `--replay FILE`           | Eine früher angelegte Aufnahme darstellen, statt diese Maschine zu lesen.                                           |
| `--history-file FILE`     | Zählerverlauf dort lesen und schreiben statt in der Zustandsdatei des Benutzers.                                    |
| `--no-record`             | Zähler gegen den aufgezeichneten Verlauf beurteilen, ohne diese Messung aufzunehmen.                                |
| `--expand-virtual`        | Jedes kernelvirtuelle Gerät auflisten, statt sie in einer Zeile zusammenzuzählen.                                   |
| `--tree-density LEVEL`    | Wie viel der PCI-Struktur die Topologie zeichnet: `storage-only` (die Vorgabe), `storage-and-siblings` oder `full`. |
| `--traceback`             | Bei Fehlern den vollständigen Python-Traceback zeigen (nützlich zur Fehlersuche).                                   |
| `--no-traceback`          | Keinen Traceback zeigen, nur die Fehlermeldung (Vorgabe).                                                           |

`--replay`, `--expand-virtual`, `--tree-density` und `--profile` werden auch
**nach** einem Befehl angenommen, der sie beachtet, und der Wert des Befehls
gewinnt: `--expand-virtual` bei `disks`, `topology` und `tui`,
`--tree-density` bei `topology`, `--profile` bei `config` und `config-deploy`,
`--replay` bei jedem Befehl, der Hardware liest. `--expand-virtual` und
`--tree-density` stehen dort, weil die Zeile unter der jeweiligen Ansicht sie
benennt - die Zählung der zusammengefalteten Geräte und der Hinweis über dem
Baum, wie viel der Struktur gezeichnet wurde - und wer tippt, was ihm gerade
gesagt wurde, darf nicht auf "no such option" stossen.

**Beispiele:**

```bash
# Ein bestimmtes Profil verwenden
lsdsk --profile production config

# Einstellungen zur Laufzeit überschreiben (wiederholbar)
lsdsk --set lib_log_rich.console_level=DEBUG config

# Konfiguration aus einer ausdrücklich genannten .env laden
lsdsk --env-file /etc/myapp/.env config

# Vollständigen Traceback zur Fehlersuche zeigen
lsdsk --traceback config-deploy --target user
```

---

### Konfiguration ansehen

Zeigt die zusammengeführte Konfiguration aus allen Quellen (defaults -> app -> host -> user -> .env -> Umgebungsvariablen).

Werte, deren Schlüssel sich wie ein Geheimnis liest (token, key, secret, password), werden als `***REDACTED***` ausgegeben. Die Schwärzung betrifft nur die Ausgabe und ändert nichts an dem Wert, den das Werkzeug verwendet.

#### Optionen

| Option           | Pflicht | Beschreibung                                                               |
|------------------|:-------:|----------------------------------------------------------------------------|
| `--format`       | Nein    | Ausgabeformat: `human` (Vorgabe) oder `json`.                              |
| `--section NAME` | Nein    | Nur einen Abschnitt zeigen (etwa `display`, `thresholds`, `lib_log_rich`). |
| `--profile NAME` | Nein    | Konfiguration für ein bestimmtes Profil laden.                             |

#### Beispiele

```bash
# Zusammengeführte Konfiguration aus allen Quellen zeigen
lsdsk config

# Als JSON ausgeben (nützlich für Skripte)
lsdsk config --format json

# Nur einen bestimmten Abschnitt zeigen
lsdsk config --section lib_log_rich

# Konfiguration für ein bestimmtes Profil laden
lsdsk config --profile production

# Optionen kombinieren
lsdsk config --profile staging --format json --section lib_log_rich
```

### Konfigurationsdateien ausbringen

Bringt die mitgelieferte Standardkonfiguration in die plattformabhängigen Verzeichnisse.

#### Optionen

| Option             | Pflicht | Beschreibung                                                                                      |
|--------------------|:-------:|---------------------------------------------------------------------------------------------------|
| `--format`         | Nein    | Ausgabeformat: `human` (Vorgabe) oder `json`.                                                     |
| `--target`         | Ja      | Zielschicht: `app`, `host` oder `user`. Mehrfach angebbar.                                        |
| `--force`          | Nein    | Vorhandene Konfigurationsdateien überschreiben. Ohne dies werden vorhandene Dateien übersprungen. |
| `--profile NAME`   | Nein    | In ein profilspezifisches Unterverzeichnis ausbringen (etwa `profile/production/`).               |
| `--permissions`    | Nein    | Setzen der Unix-Rechte einschalten (Vorgabe).                                                     |
| `--no-permissions` | Nein    | Setzen der Rechte abschalten; stattdessen die umask des Systems verwenden.                        |
| `--dir-mode MODE`  | Nein    | Verzeichnisrechte überschreiben (oktal: `750` oder `0o750`).                                      |
| `--file-mode MODE` | Nein    | Dateirechte überschreiben (oktal: `640` oder `0o640`).                                            |

#### Grundlegende Beispiele

```bash
# Benutzerkonfiguration anlegen
lsdsk config-deploy --target user

# An den systemweiten Ort ausbringen (braucht Rechte)
sudo lsdsk config-deploy --target app

# Hostspezifische Konfiguration ausbringen
sudo lsdsk config-deploy --target host

# An mehrere Orte auf einmal ausbringen
lsdsk config-deploy --target user --target host

# Vorhandene Konfiguration überschreiben
lsdsk config-deploy --target user --force

# In ein bestimmtes Profilverzeichnis ausbringen
lsdsk config-deploy --target user --profile production

# Produktionsprofil ausbringen und vorhandenes überschreiben
lsdsk config-deploy --target user --profile production --force
```

#### Für andere Benutzer ausbringen

Um die Konfiguration der Benutzerschicht für ein anderes Konto auszubringen, verwenden Sie `sudo -u`:

```bash
# Benutzerkonfiguration für das Konto 'serviceaccount' ausbringen
sudo -u serviceaccount lsdsk config-deploy --target user

# Mit einem bestimmten Profil ausbringen
sudo -u serviceaccount lsdsk config-deploy --target user --profile production

# Die Konfiguration entsteht im Heimatverzeichnis jenes Benutzers:
# /home/serviceaccount/.config/lsdsk/config.toml
```

**Wichtige Hinweise:**

- `sudo` allein (ohne `-u`) bringt in das Heimatverzeichnis von root aus, nicht in das des Zielbenutzers
- Verwenden Sie immer `sudo -u <benutzer>`, wenn Sie für Dienstkonten oder andere Benutzer ausbringen
- Die Dateien entstehen im Besitz des Zielbenutzers (das ist richtig so)
- Die Rechte werden nach den Vorgaben der Schicht `user` gesetzt (`0o700`/`0o600`, also privat)

**Häufige Fälle:**

```bash
# Administrator bringt die app-weite Konfiguration aus (alle Benutzer)
sudo lsdsk config-deploy --target app

# Administrator bringt für ein Dienstkonto aus
sudo -u myservice lsdsk config-deploy --target user

# Administrator bringt die hostspezifische Konfiguration aus
sudo lsdsk config-deploy --target host

# Ein gewöhnlicher Benutzer bringt seine eigene aus (kein sudo nötig)
lsdsk config-deploy --target user
```

#### Dateirechte (nur POSIX)

Unter Linux und macOS setzt `config-deploy` die Unix-Dateirechte je nach Zielschicht. Windows verwendet ACLs und ignoriert diese Einstellungen.

| Ziel   | Verzeichnisrechte   | Dateirechte         | Beschreibung                                   |
|--------|:-------------------:|:-------------------:|------------------------------------------------|
| `app`  | `0o755` (rwxr-xr-x) | `0o644` (rw-r--r--) | Für alle lesbar, systemweite Konfiguration     |
| `host` | `0o755` (rwxr-xr-x) | `0o644` (rw-r--r--) | Für alle lesbar, hostspezifische Konfiguration |
| `user` | `0o700` (rwx------) | `0o600` (rw-------) | Nur für den Benutzer selbst                    |

**Optionen für die Rechte:**

```bash
# Das Setzen der Rechte ganz überspringen (umask des Systems verwenden)
lsdsk config-deploy --target user --no-permissions

# Verzeichnisrechte überschreiben (oktal)
lsdsk config-deploy --target user --dir-mode 750

# Dateirechte überschreiben (oktal)
lsdsk config-deploy --target user --file-mode 640

# Beides zusammen
lsdsk config-deploy --target user --dir-mode 750 --file-mode 640

# Oktalformate: sowohl "750" als auch "0o750" werden angenommen
lsdsk config-deploy --target user --dir-mode 0o750
```

**Einstellbare Vorgaben:**

Die Vorgaben für die Rechte lassen sich in `[lib_layered_config.default_permissions]` anpassen:

```toml
[lib_layered_config.default_permissions]
# Values: octal strings ("0o755", "755") or decimal integers (493)
app_directory = "0o755"
app_file = "0o644"
host_directory = "0o755"
host_file = "0o644"
user_directory = "0o700"
user_file = "0o600"

# Set to false to disable permission setting by default
enabled = true
```

### Beispieldateien erzeugen

Erzeugt TOML-Beispieldateien, die den Aufbau eines Konfigurationsbaums zeigen, mit erklärenden Kommentaren. Sie tragen einen allgemeinen Platzhalterabschnitt statt lsdsks eigener Schlüssel `[thresholds]`, `[display]` und `[history]`; nehmen Sie sie also, um zu lernen, wohin die Dateien gehören, und für die Schlüssel selbst die mitgelieferten Vorgaben oder die Tabellen weiter unten.

#### Optionen

| Option              | Pflicht | Beschreibung                                                                        |
|---------------------|:-------:|-------------------------------------------------------------------------------------|
| `--format`          | Nein    | Ausgabeformat: `human` (Vorgabe) oder `json`.                                       |
| `--destination DIR` | Ja      | Verzeichnis, in das die Beispieldateien geschrieben werden.                         |
| `--force`           | Nein    | Vorhandene Dateien überschreiben. Ohne dies werden vorhandene Dateien übersprungen. |

#### Beispiele

```bash
# Beispiele in ein bestimmtes Verzeichnis erzeugen
lsdsk config-generate-examples --destination ./examples

# Vorhandene Beispieldateien überschreiben
lsdsk config-generate-examples --destination ./examples --force

# Beispiele im aktuellen Verzeichnis erzeugen
lsdsk config-generate-examples --destination .
```

#### Die erzeugten Dateien

| Datei                                  | Beschreibung                                               |
|----------------------------------------|------------------------------------------------------------|
| `.env.example`                         | Dieselben Einstellungen in der Form von Umgebungsvariablen |
| `xdg/lsdsk/config.toml`                | Die Hauptdatei einer Systemschicht                         |
| `xdg/lsdsk/hosts/your-hostname.toml`   | Wohin eine Datei je Host gehört, benannt nach der Maschine |
| `home/lsdsk/config.toml`               | Die Hauptdatei einer Benutzerschicht                       |
| `home/lsdsk/config.d/10-override.toml` | Eine Überschreibungsdatei, die der `config.toml` vorgeht   |

Die Verzeichnisse `xdg/` und `home/` spiegeln die Schichten, für die sie stehen, der Baum zeigt also, welche Datei wohin gehört. Jede trägt kommentierte Erläuterungen.

### Überschreiben zur Laufzeit

Mit `--set` überschreiben Sie Werte, ohne Dateien zu ändern. Diese Option:
- hat den **höchsten Vorrang** unter den Schichten, einschliesslich der
  präfixbehafteten Umgebungsvariablen weiter unten. Die nativen
  `LOG_*`-Variablen liest `lib_log_rich` selbst, statt dass sie zusammengeführt
  würden, sie entscheiden das Logging-Verhalten also weiterhin, gleich was
  `--set` sagt
- ist **wiederholbar**, um mehrere Werte zu setzen
- muss **vor** dem Befehlsnamen stehen

#### Syntax

```
--set SECTION.KEY=VALUE
--set SECTION.SUBSECTION.KEY=VALUE
```

#### Beispiele

```bash
# Einen einzelnen Wert überschreiben
lsdsk --set lib_log_rich.console_level=DEBUG config

# Mehrere Werte überschreiben
lsdsk --set lib_log_rich.console_level=DEBUG --set lib_log_rich.console_format_preset=short config

# Einen verschachtelten Wert überschreiben
lsdsk --set lib_layered_config.default_permissions.app_file=0o600 config

# Mit JSON-Listen oder -Objekten überschreiben (den Wert in einfache Anführungszeichen setzen)
lsdsk --set lib_log_rich.scrub_patterns='{"totp": "(?i)totp"}' config

# Mit einem Profil kombinieren
lsdsk --profile production --set lib_log_rich.console_level=DEBUG config
```

#### Unterstützte Werttypen

| Typ           | Beispiel                                                        |
|---------------|-----------------------------------------------------------------|
| Zeichenkette  | `--set section.key=value`                                       |
| Ganzzahl      | `--set section.timeout=30`                                      |
| Fliesskomma   | `--set section.ratio=0.5`                                       |
| Wahrheitswert | `--set section.enabled=true` oder `--set section.enabled=false` |
| JSON-Liste    | `--set section.hosts='["a.com", "b.com"]'`                      |
| JSON-Objekt   | `--set section.metadata='{"key": "value"}'`                     |

---

## Was lsdsk selbst liest

Drei Abschnitte gehören lsdsk und nicht der Konfigurationsbibliothek. Jeder
Wert, nach dem das Werkzeug urteilt oder ausrichtet, ist einer dieser Schlüssel,
eine Schwelle ist also nie eine in einer Funktion vergrabene Konstante.

Bringen Sie sie mit `lsdsk config-deploy --target user` aus, was
`config.d/60-thresholds.toml`, `70-display.toml` und `50-history.toml` mit den
mitgelieferten Werten und ihren Erläuterungen schreibt. Für einen einzelnen Lauf
überschreiben Sie einen mit `--set`:

```bash
lsdsk --set thresholds.crc_errors_significant=10 findings
```

### `[thresholds]` - woran die Regeln messen

Diese entscheiden den Schweregrad und damit den Exit-Code.

| Schlüssel                    | Vorgabe | Wirkung                                                                                         |
|------------------------------|---------|-------------------------------------------------------------------------------------------------|
| `wear_warning_percent`       | `80`    | Verbrauchte Nennlebensdauer, ab der ein Laufwerk eine Warnung bekommt                           |
| `wear_critical_percent`      | `95`    | Und ab der es kritisch heisst                                                                   |
| `crc_errors_significant`     | `100`   | Darunter ist ein CRC-Stand der Schnittstelle ein Hinweis statt einer Warnung                    |
| `mixed_firmware_threshold`   | `2`     | Verschiedene Firmwarestände eines Modells, ab denen es gemeldet wird                            |
| `wear_projection_min_points` | `2`     | Prozentpunkte gemessener Verschleissbewegung, bevor ein Verschleiss-Enddatum hochgerechnet wird |
| `quiet_expected_min`         | `10.0`  | Fehler, die die Rate des Laufwerks vorhergesagt haben muss, bevor Schweigen als Beleg zählt     |
| `min_span_hours`             | `1`     | Betriebsstunden zwischen zwei Messungen, bevor überhaupt eine Rate gerechnet wird               |

### `[display]` - Anordnung und Farbe

Diese ändern, wie ein Bericht aussieht. Keiner davon ändert den Schweregrad oder
den Exit-Code: der kommt aus `[thresholds]` und aus den Grenzen, die ein
Laufwerk über sich selbst veröffentlicht.

| Schlüssel                 | Vorgabe        | Wirkung                                                              |
|---------------------------|----------------|----------------------------------------------------------------------|
| `piped_width`             | `120`          | Breite, wenn die Ausgabe kein Terminal ist                           |
| `summary_limit`           | `6`            | Befunde in der Urteilszeile, bevor "and N more" folgt                |
| `wear_row_floor_percent`  | `10`           | Verschleiss darunter bekommt in beiden Ansichten keine Verlaufszeile |
| `expand_virtual`          | `false`        | Kernelvirtuelle Geräte auflisten, statt sie zusammenzuzählen         |
| `tree_density`            | `storage-only` | Wie viel der PCI-Struktur jede Ansicht zeichnet                      |
| `wwn_width`               | `24`           | Höchstzahl Zeichen für die Spalte wwn in beiden Ansichten            |
| `detail_height_percent`   | `33`           | Höchstanteil des interaktiven Fensters für die Detailtafel           |
| `traceback_summary_limit` | `500`          | Zeichen, die ein kurzer Traceback behält                             |
| `traceback_verbose_limit` | `10000`        | Und unter `--traceback`                                              |

Ein kernelvirtuelles Gerät ist eines, das der Kernel ohne Hardware dahinter
bereitstellt: zram, ein Loop-Mount, ein ZFS-zvol, ein Device-Mapper-Knoten. Es
hat keinen Controller, keine Verbindung und keine Zähler, es wird also gezählt
und benannt statt aufgelistet. Verborgen wird es nie: der Kopf zählt sie, der
Baum und die Plattentabelle sagen, wie viele weggelassen wurden, und der
JSON-Umschlag trägt jedes einzelne unter `virtual_disks`, wie dieser Schlüssel
auch gesetzt ist.

`tree_density` bestimmt, wie viel der PCI-Struktur jede Ansicht zeichnet, `lsdsk
topology`, der Bericht auf einer Seite und die interaktive Ansicht gleichermassen.
Die drei Schreibweisen laufen von der geringsten Genauigkeit zur grössten.
`storage-only`, die mitgelieferte Vorgabe, behält die Speichercontroller und die
Bridges ÜBER ihnen, also den Weg vom Board hinunter zu jedem Laufwerk und nichts,
was woandershin führt, denn auf echter Hardware sind vier von fünf Gerätezeilen
ohne Bezug zum Speicher. `storage-and-siblings` behält diese plus die Geräte
ohne Speicherbezug, die sich eine Bridge mit einem teilen; `full` nennt jedes
Gerät, mit dem das Board kam, von der Wurzel her. Keine Ansicht hält
stillschweigend etwas zurück: jede sagt in einer Zeile über dem Baum, was sie
zeichnet und wie Sie mehr anfordern. Das sind die einzigen angenommenen
Schreibweisen; das Wort ist der Wert, Gross- und Kleinschreibung egal. Die
interaktive Ansicht schaltet dieselbe Einstellung mit `d` auf ihrer
Topologieseite weiter, und der gesamte Baum einer Aufnahme reist im JSON-Umschlag
unter `pci_tree` mit, gleich was die Anzeige zeigt.

`detail_height_percent` begrenzt die Detailtafel der interaktiven Ansicht, also
den Kasten unter den Tabellen, der den vollständigen Eintrag dessen trägt,
worauf der Cursor steht, auf den sechs Seiten, die einen führen (Topologie,
Controller, Platten, Gesundheit, Steckplätze und Verlauf): jeder Wert, für den
die Zeile keine Spalte hatte, dann die Befunde, die es benennen, mit Begründung
und Abhilfe. Es ist eine OBERGRENZE und keine Höhe, ein kurzer Eintrag nimmt
sich also die Zeilen, die er braucht, und ein langer rollt innerhalb der Tafel.
Setzen Sie `25` für ein Viertel des Fensters. `i` blendet sie aus und gibt der
Tabelle den ganzen Bildschirm. An dem, was ein druckender Befehl zeichnet,
ändert es nichts.

Farbe ist kein Schlüssel, obwohl dieser Abschnitt nach ihr benannt ist. Die
gedruckte Palette muss auf einer schwarzen und einer weissen Konsole zugleich
lesbar bleiben, und das ist eine Messung und keine Geschmacksfrage: fährt man
die Helligkeit eines gesättigten Farbtons durch, liegt seine Decke gegen beide
nahe 4,2:1, die Prüfung, die jede mitgelieferte Farbe hält, sitzt daher bei 4,0
über vier echten Hintergründen, zwei dunklen und zwei hellen. Die interaktive
Ansicht malt ihren eigenen Hintergrund und trägt daher eine zweite Palette, die
gegen jenen am Fliesstext-Boden von 4,5 gemessen wird. Keine von beiden lässt
sich aus einer Datei überschreiben, denn ein Wert, der diese Prüfungen nicht
besteht, machte einen Schweregrad unlesbar; und wo Farbe einen Schweregrad
trägt, ist sie nie das Einzige, was ihn trägt, ein Bericht ohne Farbe sagt also
dasselbe wie einer mit.

### `[history]` - der Zählerspeicher

| Schlüssel               | Vorgabe | Wirkung                                                                                        |
|-------------------------|---------|------------------------------------------------------------------------------------------------|
| `enabled`               | `true`  | Ob ein gewöhnlicher Lauf eine Messung aufzeichnet. `--no-record` schaltet es für einen Lauf ab |
| `path`                  | `""`    | Wo der Speicher liegt. Leer löst je Plattform UND je Rechtestufe auf                           |
| `max_samples_per_drive` | `512`   | Messungen je Laufwerk, bevor die Reihe ausgedünnt wird                                         |

Ein leerer `path` löst bei einem root-Lauf unter Linux oder macOS zu
`/var/lib/lsdsk/history.json` auf, sonst zum Zustandsverzeichnis des Benutzers
(`$XDG_STATE_HOME/lsdsk/`, `~/Library/Application Support/bitranox/lsdsk/`,
`%LOCALAPPDATA%\bitranox\lsdsk\`). Das Lesen der Zähler braucht root, auf einem
Server füllt sich also der root-Pfad. `lsdsk record --format json` gibt unter
`store` den Pfad aus, auf den dieser Lauf aufgelöst hat.

Für einen einzelnen Lauf überschreiben Sie ihn mit dem globalen
`--history-file`.

### Eine Einstellung, die nichts tut, sagt das auch

Alles oben greift auf den mitgelieferten Wert zurück, statt den Lauf abzubrechen:
ein fehlerhafter Schwellwert darf niemanden daran hindern, ein ausfallendes
Laufwerk zu untersuchen. Ein Wert, der zurückgefallen ist, wirkt aber nicht, und
ein wirkungsloser Wert liest sich sonst genau wie ein übernommener. Deshalb
werden beide Arten von Fehler gemeldet.

Ein Schlüssel, den diese drei Abschnitte nicht haben, ist ein Tippfehler und
sonst nichts. Aus `--set` wird er abgewiesen, mit Exitcode 2 und dem Schlüssel,
den Sie wahrscheinlich gemeint haben. In einer Datei wird stattdessen gewarnt,
denn eine Datei bleibt liegen und teilt den Namensraum mit den Bibliotheken, die
dort ebenfalls schreiben:

```
$ lsdsk --set thresholds.wear_warnning_percent=1 findings
Error: Invalid override 'thresholds.wear_warnning_percent=1': [thresholds] has no key 'wear_warnning_percent'. Did you mean thresholds.wear_warning_percent?
```

Ein Wert, den der Schlüssel nicht annehmen kann, wird auf beiden Wegen gemeldet.
Die Meldung nennt auch, was die Maschine stattdessen beurteilt hat:

```
$ lsdsk --set thresholds.wear_warning_percent=abc --set display.tree_density=bogus findings
Warning: ignoring thresholds.wear_warning_percent=abc: not a whole number above zero, or too large to use. Using 80.
Warning: ignoring display.tree_density=bogus: not one of storage-only, storage-and-siblings, full. Using storage-only.
```

Beides geht in jedem Ausgabemodus nach stderr, damit `--format json` auf stdout
genau das bleibt, was ein Parser erwartet. Schlüssel außerhalb dieser drei
Abschnitte bleiben unangetastet: `lib_log_rich`, `lib_layered_config` und ein
Schlüssel der obersten Ebene aus einer `.env` nehmen Namen an, für die dieses
Projekt keine Zeile mitliefert. Ein Abweisen dort würde Sie an etwas scheitern
lassen, das nicht Ihre Sache ist.

Ein falsch geschriebener ABSCHNITT wird genauso abgewiesen, und er muss zuerst
beurteilt werden: die Schlüsselprüfung kann einen falschen Abschnitt gar nicht
sehen, denn sie sagt über einen Abschnitt, den dieses Werkzeug nicht besitzt,
überhaupt nichts - und genau so sieht `threshold` für sie aus. Ohne das
verschob ein einziger fehlender Buchstabe das Urteil von 16 Befunden auf die
mitgelieferten 5, bei Exit-Code 1 und ohne eine Zeile auf irgendeinem Strom:

```
$ lsdsk --set threshold.wear_warning_percent=1 findings
Error: Invalid override 'threshold.wear_warning_percent=1': there is no section [threshold]. Did you mean [thresholds]?
```

Abgewiesen wird nur eine NAHE Übereinstimmung, damit `lib_log_rich` und jeder
andere fremde Namensraum weiterhin unangetastet durchgehen. Ein Abschnitt, der
nichts Eigenem ähnelt - `a.b.c=1` -, geht ebenfalls durch: nichts hier kann
beweisen, dass es ein Fehler ist und nicht ein Schlüssel, den ein anderer Leser
derselben Datei haben möchte.

## Profile

Profile bieten voneinander getrennte Namensräume für verschiedene Umgebungen (etwa `production`, `staging`, `test`).

### Anforderungen an Profilnamen

Profilnamen werden auf Sicherheit und plattformübergreifende Verträglichkeit geprüft:

| Regel                 | Beschreibung                                                                                               |
|-----------------------|------------------------------------------------------------------------------------------------------------|
| **Höchstlänge**       | 64 Zeichen                                                                                                 |
| **Erlaubte Zeichen**  | ASCII-Buchstaben (`a-z`, `A-Z`), Ziffern (`0-9`), Bindestriche (`-`), Unterstriche (`_`)                   |
| **Erstes Zeichen**    | Muss ein Buchstabe oder eine Ziffer sein, nicht `-` oder `_`                                               |
| **Reservierte Namen** | Unter Windows reservierte Namen werden abgelehnt: `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9` |
| **Pfadsicherheit**    | Keine Pfadtrenner (`/`, `\`) und keine Rückwärtsschritte (`..`)                                            |

**Gültige Beispiele:** `production`, `staging-v2`, `test_env`, `dev01`

**Ungültige Beispiele:** `../etc` (Pfadwechsel), `-invalid` (beginnt mit Bindestrich), `CON` (unter Windows reserviert)

### Welche Schichten betrifft ein Profil?

| Schicht  | Vom Profil betroffen? | Anmerkung                                        |
|----------|:---------------------:|--------------------------------------------------|
| defaults | Nein                  | Wird immer aus dem Paket geladen                 |
| app      | Ja                    | Verwendet das Unterverzeichnis `profile/<name>/` |
| host     | Ja                    | Verwendet das Unterverzeichnis `profile/<name>/` |
| user     | Ja                    | Verwendet das Unterverzeichnis `profile/<name>/` |
| .env     | Nein                  | Projektverzeichnis                               |
| env vars | Nein                  | Umgebung                                         |
| CLI      | Nein                  | Kommandozeile                                    |

### Beispiele für Profilpfade

**Ohne Profil:**
- `~/.config/lsdsk/config.toml`

**Mit dem Profil `production`:**
- `~/.config/lsdsk/profile/production/config.toml`

### Leseverhalten

Profilverzeichnisse sind **getrennte Namensräume**. Mit einem Profil ausgebrachte Konfiguration ist nur sichtbar, wenn mit demselben Profil gelesen wird.

| Befehl                        | Sieht die Schicht `app`?              | Sieht die Schicht `user`?             |
|-------------------------------|---------------------------------------|---------------------------------------|
| `config` (ohne Profil)        | Nur wenn ohne Profil ausgebracht      | Nur wenn ohne Profil ausgebracht      |
| `config --profile production` | Nur wenn mit `production` ausgebracht | Nur wenn mit `production` ausgebracht |

**Beispiel**: Wenn Sie `app` mit `--profile production` ausbringen, `user` aber ohne Profil:

| Befehl                        | Schicht app | Schicht user |
|-------------------------------|:-----------:|:------------:|
| `config`                      | Nein        | Ja           |
| `config --profile production` | Ja          | Nein         |

### Ein Profil, auf das nichts antwortet

Weil ein Profil die Verzeichnisse ERSETZT und nicht ergänzt, liest ein Name mit
einem falschen Buchstaben überhaupt keine Datei, und jeder Wert fällt auf den
ausgelieferten zurück. Der Lauf endet trotzdem mit `0`, und `config` meldet
anschließend genau diese zurückgefallenen Zahlen als die geltenden Werte.

Ein `--profile`, das keine Schicht beigetragen hat, sagt das deshalb auf stderr
und nennt das nächstliegende Profil dieser Maschine, sofern eines nahe genug
liegt:

```text
Warning: profile prodd named nothing, so every value is the one configured without it. Did you mean prod?
```

Groß- und Kleinschreibung zählt, denn ein Profilverzeichnis wird exakt
verglichen: `--profile PROD` findet `prod` nicht und bekommt dieselbe Zeile.
Es ist eine Warnung und keine Zurückweisung, der Lauf berichtet also weiterhin
über die Hardware vor Ihnen. Ein Verzeichnis, das zwar existiert, aber nichts
Lesbares enthält, bekommt sie ebenfalls, denn es ist auf genau dieselbe Weise
ein stiller Rückfall.

---

## Umgebungsvariablen

Die Konfiguration lässt sich auf zwei Wegen über Umgebungsvariablen überschreiben:

### Weg 1: Die nativen Variablen von lib_log_rich

Für die Logging-Konfiguration nehmen Sie die nativen `LOG_*`-Variablen (höchster Vorrang):

```bash
LOG_CONSOLE_LEVEL=DEBUG lsdsk info
LOG_ENABLE_GRAYLOG=true LOG_GRAYLOG_ENDPOINT="logs.example.com:12201" lsdsk info
```

### Weg 2: Variablen mit Anwendungspräfix

Für jeden Konfigurationsabschnitt gilt die Form `<PRÄFIX>___<ABSCHNITT>__<SCHLÜSSEL>=wert`

```bash
LSDSK___LIB_LOG_RICH__CONSOLE_LEVEL=DEBUG lsdsk info
```

**Die Trenner:**
- `___` (drei Unterstriche) trennt das Präfix vom Abschnitt
- `__` (zwei Unterstriche) trennt den Abschnitt vom Schlüssel

---

## Unterstützung für .env-Dateien

Legen Sie für lokale Entwicklungsüberschreibungen eine `.env` im Projektverzeichnis an:

```bash
# .env
LOG_CONSOLE_LEVEL=DEBUG
LOG_CONSOLE_FORMAT_PRESET=short
LOG_ENABLE_GRAYLOG=false
```

Standardmässig sucht die Anwendung vom aktuellen Verzeichnis aus nach oben nach `.env`-Dateien.

Um stattdessen eine bestimmte `.env` zu laden, nehmen Sie `--env-file`:

```bash
# Von einem ausdrücklichen Pfad laden (überspringt die Suche nach oben)
lsdsk --env-file /opt/myapp/config/.env config
```

Die Datei muss vorhanden und lesbar sein; Click prüft das, bevor der Befehl läuft.

---

## Die Standardkonfiguration

`defaultconfig.toml` und die Dateien in `defaultconfig.d/` (mit dem Paket mitgeliefert) liefern die Grundwerte. Sie sind der Rückfall, wenn keine externen Konfigurationsdateien ausgebracht sind.

---

## Empfehlungen zum Anpassen

**Ändern Sie ausgebrachte Konfigurationsdateien NICHT unmittelbar.** `lsdsk config-deploy --force` schreibt sie neu, eine an Ort und Stelle vorgenommene Änderung ist also verloren, sobald das jemand ausführt.

Legen Sie stattdessen eigene Überschreibungsdateien im Verzeichnis der passenden Schicht an, mit einem hohen Zahlenpräfix:

```bash
# Anpassung auf Benutzerebene (Linux)
~/.config/lsdsk/config.d/999-myconfig.toml

# Anpassung auf Benutzerebene (macOS)
~/Library/Application Support/bitranox/lsdsk/config.d/999-myconfig.toml

# Anpassung auf Benutzerebene (Windows)
%APPDATA%\bitranox\lsdsk\config.d\999-myconfig.toml

# Systemweite Anpassung (Linux)
/etc/xdg/lsdsk/config.d/999-myconfig.toml
```

**Warum das funktioniert:**
- Eine Schicht liest `config.toml` und die Dateien in ihrem Verzeichnis
  `config.d/`, und sonst nichts. Eine Datei, die neben `config.toml` statt in
  `config.d/` liegt, wird stillschweigend übergangen, was genauso aussieht wie
  eine Einstellung, die nicht wirkt
- Innerhalb von `config.d/` laden die Dateien in alphabetischer Reihenfolge,
  eine hohe Zahl lädt also zuletzt
- Alles in `config.d/` geht der `config.toml` jener Schicht vor
- Ihre eigene Datei rührt `config-deploy` nicht an, denn es schreibt
  `config.toml`

**Beispiel `999-myconfig.toml`:**

```toml
# My custom overrides - survives package updates

[lib_log_rich]
console_level = "DEBUG"
```
