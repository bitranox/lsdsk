# Sicherheitsrichtlinie

[English](../SECURITY.md) | **Deutsch**

## Unterstützte Versionen

| Version | Unterstützt |
|---------|-------------|
| latest  | ja          |

## Eine Sicherheitslücke melden

Wenn Sie in diesem Projekt eine Sicherheitslücke finden, melden Sie sie bitte
verantwortungsvoll:

1. Öffnen Sie **kein** öffentliches GitHub-Issue für Sicherheitslücken.
2. Schreiben Sie an `bitranox@gmail.com`, die in den Paket-Metadaten
   veröffentlichte Adresse. Die private Schwachstellenmeldung von GitHub ist für
   dieses Repository nicht aktiviert, ein Advisory lässt sich dort also nicht
   anlegen.
3. Beschreiben Sie die Lücke, die Schritte zur Reproduktion und alles, was sonst
   hilft: Logs, Bildschirmfotos.

Eine erste Antwort erhalten Sie innerhalb von 72 Stunden.

## Unterprozesse und Netzzugriff

lsdsk startet keinen Unterprozess und stellt keine Netzwerkanfrage, mit drei
engen Ausnahmen:

- **Die Graylog-Protokollsenke** ist standardmäßig aus. Schaltet man sie ein,
  öffnet sie eine Verbindung zum konfigurierten Graylog-Host, und sonst nichts.
- **Das Einrichten von uv nach ausdrücklicher Zustimmung.** Nimmt man das
  Angebot an, eine Desktop-Verknüpfung auf einer Maschine ohne `uvx` anzulegen,
  kann lsdsk uv einrichten, indem es den offiziellen astral.sh-Installer
  ausführt. Es läuft erst nach dieser Bestätigung, der Installer ist auf eine bekannte
  uv-Version festgelegt, er bleibt auf das einzige Modul
  `adapters/uv_bootstrap.py` beschränkt, und `tests/test_no_subprocess_or_network.py`
  lässt den Build scheitern, wenn irgendein anderes Modul etwas aufruft oder
  einen Socket öffnet.
- **Der Neustart mit erhöhten Rechten** startet lsdsks eigene erhöhte Kopie:
  eine UAC-Abfrage unter Windows, `sudo` unter Linux und macOS. Er startet kein
  anderes Programm, und `--no-elevate` schaltet ihn ab.

## Sicherheitswerkzeuge

In der CI laufen:

- **Bandit** - statische Analyse auf verbreitete Sicherheitsprobleme in Python
- **CodeQL** - das Code-Scanning von GitHub, wöchentlich
- **pip-audit** - prüft die Abhängigkeiten gegen den Advisory-Dienst von PyPI,
  seine Standardquelle
- **Ruff-Sicherheitsregeln** - die Regelsätze S (flake8-bandit) und B
  (flake8-bugbear)

## Umgang mit Abhängigkeiten

- Produktive Abhängigkeiten nennen eine Mindestversion (`>=`) und werden bei
  jedem CI-Lauf mit `pip-audit` geprüft.
- Bekannte CVEs in transitiven Abhängigkeiten werden in `pyproject.toml`
  festgehalten, mit einem Kommentar, der die CVE-Kennung nennt.
