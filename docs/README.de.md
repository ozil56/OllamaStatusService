[English](../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Español](README.es.md)

# Ollama Status Service

**Den tatsächlichen Ollama-Status sehen und lokalen Anfragen den nächsten freien Platz geben — ohne laufende Arbeit zu unterbrechen.**

**Version 1.2.3 · Windows x64** · [Download](https://github.com/ozil56/OllamaStatusService/releases/tag/v1.2.3) · [Änderungen](../CHANGELOG.md) · [Build-Anleitung](BUILD.md)

Ein HTTP-Gateway mit Windows-Diensten, grafischem Installer und Tray-Anwendung für eine vorhandene Ollama-Installation. Geladene Modelle sind nicht automatisch beschäftigt: Das Gateway verfolgt die Anfragen, die es weiterleitet. Ollama, Modelle und GPU-Treiber werden nicht mitgeliefert. Die Oberfläche ist derzeit auf vereinfachtem Chinesisch; diese README-Übersetzungen übersetzen nicht die Anwendung.

## Funktionen

- `/status` und `/dashboard`: Zustand, laufende und wartende Anfragen, Herkunft und IDs.
- Lokale wartende Anfragen haben Vorrang, innerhalb jeder Klasse gilt FIFO. Laufende Anfragen werden nicht verdrängt; zukünftige Batches erhalten keine Reservierung.
- `/help`, Diensterkennung und OpenAPI dokumentieren aktuelle Grenzen und Integrationsregeln.
- Tray-Symbole: Grün = frei, Orange = beschäftigt, Rot = offline, Violett = Abschluss unklar. Das Schließen des Trays beendet keine Dienste.
- Erkennung vorhandener Modellpfade, Paketprüfung und Vorabprüfung von Zustand und Ports beim Upgrade.

## Installation und Upgrade

Voraussetzungen: Windows 10/11 x64 oder Windows Server mit Desktop, vorhandenes Ollama mit lokalen Modellen, PowerShell 5.1, .NET Framework 4.8, Aufgabenplanung, Firewall-Verwaltung und Administratorrechte für die Installation.

1. `OllamaStatusService-Windows-x64-v1.2.3.zip` und `.sha256.txt` aus den Release-Dateien laden. Mit `Get-FileHash -Algorithm SHA256` vergleichen. Die automatisch erzeugten Source-code-Archive sind keine Installationspakete.
2. Neue Aufträge pausieren, laufende und wartende Arbeit abschließen lassen und das vollständige ZIP in einen neuen Ordner entpacken.
3. **`安装.exe`** starten, Ollama- und Modellpfade prüfen und UAC bestätigen. Dasselbe Programm aktualisiert bestehende Installationen.
4. `http://127.0.0.1:11434/dashboard` öffnen. Laufzeit und Abhängigkeiten sind enthalten; die Installation lädt nichts nach.

Die Unterbrechungsfreiheit gilt für normale Planung. Installation, Upgrade, Stopp und Wiederherstellung benötigen ein Wartungsfenster. Scheitert die Neuinstallation nach Entfernung der alten Version, bleiben Fehlerbericht und Sicherungen erhalten; ein automatisches Rollback wird nicht zugesichert. **`卸载.exe`** deinstalliert den Dienst und behält das ursprüngliche Ollama und die Modelle.

## Ports und FRP

| Port | Aufgabe |
| --- | --- |
| `11434` | Direkter IPv4/IPv6-Zugang; Herkunft anhand des tatsächlichen Sockets |
| `11436` | Standardmäßig `127.0.0.1`, Proxy-Zugang, immer remote |
| `11435` | Ollama-Backend auf Loopback; nicht direkt umgehen |

Auf jedem Server im bestehenden FRP-Eintrag `localIP = "127.0.0.1"` und `localPort = 11436` setzen. Für INI heißen die Felder `local_ip` und `local_port`. Öffentliche Ports, Namen, Authentifizierung und TLS beibehalten. Auch lokale Nginx-/Caddy-/SSH-Proxys verwenden `11436`. Proxys auf anderen Rechnern benötigen eine explizite Bind-Adresse und auf ihre Quellen beschränkte Firewall-Regeln. PROXY protocol wird nicht verarbeitet.

Beide Eingänge teilen eine Warteschlange. HTTP-Header können keine lokale Priorität vortäuschen. `/help.request_origin` muss lokal direkt `local` und über den Proxy `remote` anzeigen. Weiterleitung an `11434` kann weiterhin als lokal gelten.

## Warteschlange und Sicherheit

`X-Ollama-Queue-Timeout` setzt die Wartezeit in Sekunden: `0` lehnt bei Auslastung sofort ab, ohne Header gelten standardmäßig 15 Sekunden. Aktuelle Grenzen stehen in `/help`. Diese Frist beendet keine bereits gestartete Inferenz. Lokaler Vorrang erfordert tatsächliches Einreihen; dauerhaft lokale Arbeit kann entfernte Anfragen bis zu deren Frist verzögern.

Sicheres automatisches Ausweichen erfordert **HTTP 429/503, `X-Ollama-Forwarded: false` und JSON `forwarded: false` gemeinsam**. Timeout, Verbindungsabbruch oder Teilausgabe beweisen keine Nichtausführung. `can_start_now` ist eine Momentaufnahme, keine Reservierung.

**Das Gateway ergänzt keine Authentifizierung.** Seit 1.2.2 setzt Installation/Upgrade die produktspezifische TCP-`11434`-Freigabe auf alle Quellen, lokalen Adressen und Netzwerkprofile, beschränkt auf das mitgelieferte Python-Programm. Frühere Einschränkungen werden gesichert, aber nicht automatisch übernommen. Regeln nach dem Upgrade prüfen und Authentifizierung, HTTPS sowie Netzwerkkontrollen beibehalten. `11435` und `11436` bleiben standardmäßig auf Loopback.

Bei unklarem Abschluss blockiert `unknown` neue Arbeit. Die Wiederherstellung stoppt und prüft das Backend vor dem Bereinigen unklarer Datensätze. Die Datenbank nicht während laufender Arbeit löschen. Wartende Nutzdaten werden nach einem Neustart nicht wiederholt. Einstellungen liegen unter `%ProgramData%\OllamaStatusService`.

Tests verwenden ein simuliertes Backend und belegen keine erfolgreiche GPU-/FRP-Installation auf jedem Zielrechner. Details und Validierungsgrenzen stehen in der [englischen README](../README.md).

## Lizenz

No project source-code license has been selected yet. Publication does not grant a general license to use, modify or redistribute the project source. Third-party notices do not license this project’s own code.

Für Drittanbieter gelten deren [eigene Lizenzen](../licenses/THIRD-PARTY-NOTICES.txt).
