# Leitfaden für Beiträge

[English](../CONTRIBUTING.md) | **Deutsch**

Danke, dass Sie **lsdsk** verbessern helfen. Die folgenden Abschnitte fassen den
täglichen Ablauf zusammen, zeigen, was die Automatisierung des Repositories von
selbst tut, und nennen die Prüfungen, die vor einer Übernahme bestehen müssen.

## 1. Der Ablauf im Überblick

1. Forken und abzweigen - kurze Branch-Namen im Imperativ
   (`feature/cli-extension`, `fix/codecov-token`).
2. Eng gefasste Commits - halten Sie unzusammenhängende Umbauten aus derselben
   Änderung heraus.
3. Vor dem Push lokal `make test` laufen lassen, siehe den Hinweis zur
   Automatisierung weiter unten.
4. Dokumentation und Changelog-Einträge nachziehen, soweit die Änderung sie
   betrifft.
5. Einen Pull Request öffnen und die zugehörigen Issues nennen.

## 2. Commits und Pushes

- Commit-Nachrichten stehen im Imperativ (`Add rich handler`,
  `Fix CLI exit codes`).
- `make test` führt die gesamte Lint-, Typ- und Testkette aus und schreibt dabei
  in den Arbeitsbaum: es erzeugt das `Makefile` neu, wendet Formatierer und
  Korrekturen von Ruff an und hebt Mindestversionen in `pyproject.toml` an.
  Sehen Sie diese Änderungen durch, statt anzunehmen, ein sauberer Lauf habe
  nichts hinterlassen, und legen Sie die Commits selbst an, bevor Sie pushen
  oder Coverage-Artefakte hochladen.
- `make push` committet immer vor dem Push. Interaktiv fragt es nach einer
  Nachricht, beachtet `MSG="..."`, wenn angegeben (das Makefile reicht es als
  `BMK_COMMIT_MESSAGE` weiter), und legt einen leeren Commit an, wenn nichts
  vorgemerkt ist.

## 3. Regeln für den Code

- Es gelten die Clean-Architecture- und SOLID-Regeln des Repositories.
- Kleine Module und Funktionen mit einer Aufgabe; mischen Sie keine Belange, die
  nichts miteinander zu tun haben.
- Freie Funktionen und Module in `snake_case`, Klassen in `PascalCase`.
- Halten Sie die Laufzeitabhängigkeiten gering. Nehmen Sie die Standardbibliothek,
  wo es praktikabel ist.

## 4. Tests und Werkzeuge

- `make test` wendet Formatierer und Korrekturen von Ruff an und führt dann
  Bandit, die Import-Verträge (`lint-imports`), pip-audit, Pyright, Pytest, die
  Format- und Lint-Prüfungen von Ruff, PSScriptAnalyzer und ShellCheck aus.
  Pytest läuft immer unter Coverage; es gibt keinen Schalter, der das abstellt.
- Die Kette synchronisiert vor ihren Prüfungen das `.venv` des Projekts, die
  Werkzeuge sind also für Sie installiert. Diese Synchronisierung entfernt auch
  Pakete, die die deklarierten Extras nicht mitbringen: bewahren Sie im `.venv`
  also nichts auf, was `pyproject.toml` nicht nennt.
- Uploads zu Codecov brauchen einen Commit, den der oben beschriebene
  automatische Commit liefert. Für private Repositories setzen Sie
  `CODECOV_TOKEN` in der Umgebung oder in `.env`.
- Tests sind erzählend geschrieben: Namen wie
  `test_when_<bedingung>_<ergebnis>()`, jeder Fall haarscharf auf eine Sache
  gerichtet, Betriebssystem-Einschränkungen über die vorhandenen Marker
  (`@pytest.mark.os_agnostic`, `@pytest.mark.os_windows` und so fort).
- Wenn Sie ein CLI-Verhalten hinzufügen oder das Verhalten bei fehlenden
  Metadaten ändern, ziehen Sie die passende Geschichte in `tests/test_*.py` nach,
  damit die Spezifikation vollständig bleibt.

## 5. Prüfliste für die Dokumentation

Vor dem Öffnen eines Pull Requests:

- [ ] `make test` läuft lokal durch, und Sie haben die Dateien durchgesehen, die
      es neu geschrieben hat.
- [ ] Die betroffene Dokumentation (`README.md`, `DEVELOPMENT.md`,
      `docs/systemdesign/*`) ist nachgezogen.
- [ ] Keine erzeugten Artefakte und keine virtuellen Umgebungen sind committet.
- [ ] Versionssprünge halten, wo nötig, `pyproject.toml`, `CHANGELOG.md`,
      `src/lsdsk/__init__conf__.py` und `.claude-plugin/plugin.json` im
      Gleichschritt. `make bump-patch` erledigt alle vier;
      `tests/test_metadata_sync.py` schlägt fehl, wenn eines liegen bleibt.

## 6. Sicherheit und Konfiguration

- Committen Sie niemals Geheimnisse. Tokens (Codecov, PyPI) gehören in `.env`
  (von git ignoriert) oder in die CI-Secrets.
- Das Logging läuft durch den Scrubber von `lib_log_rich`. Ausgegebene
  Konfiguration bekommt ihren eigenen Schwärzungsdurchgang in
  `adapters/config/secrets.py`, der nicht am Logging-Pfad liegt. Ergänzen Sie
  ein Muster für den schützenswerten Schlüssel in demjenigen der beiden, der
  Ihren Fall abdeckt, statt an der Aufrufstelle zu schwärzen.

Viel Vergnügen beim Entwickeln.
