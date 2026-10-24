# Validierungskampagne Oktober 2026: Bereitstellung und Test

Dieser Branch erweitert die vorhandene Messung. Es wird weiterhin die vorhandene
`config/env.yaml` verwendet. Sie bleibt lokal und wird vom Kopierpaket ausdrücklich
mitgenommen. `main` bleibt auf seinem bisherigen Commit. Es wurden keine neuen
Labormessungen durch den implementierenden Agenten ausgeführt.

Stand 24.10.2026: Die drei VMs erhalten eine schlanke Installation ohne `.git`;
die frühere Branchwechsel-Anleitung in Abschnitt 2 ist für diese Bereitstellung
nicht anzuwenden. Vor dem Ersetzen wurden tatsächliche Bundlepfade und Mounts
inventarisiert; alle `runc-bundle`-Verzeichnisse und die benötigte Infrastruktur
bleiben erhalten. Historische Run-/Logbestände wurden einmalig gezielt bereinigt.
Neue Validierungsdaten werden dadurch nicht automatisch gelöscht.
Paketprüfsummen und Dateimanifest belegen den ausgelieferten Arbeitsstand.
Im Validierungsmodus sind fehlende Git-Metadaten beim Preflight Warnungen;
Werkzeug-, Speicher- und tatsächliche Codehashprüfungen bleiben verbindlich.
Die vorhandene `.venv` auf benke3 wird weiterverwendet. `tcpdump` ist auf allen
drei VMs bereits installiert; es sind keine Offlineinstallationen erforderlich.
Echte Funktionsruns benötigen weiterhin Eriks ausdrückliches Go. Zuerst je
einen Run pro bestätigter Workflow-/Szenariokombination mit `--repeats 1`, ohne
`--analyse`; die 30er-Kampagne startet nicht automatisch.

## Konfiguration und Rollen

| Rolle | Host | Aufgabe |
|---|---|---|
| Source | benke1 / 192.168.13.10 | Ursprungscontainer, Migrationsskript, Ressourcenmessung, Source-Destination-PCAP |
| Destination | benke2 / 192.168.13.15 | Restore, Ressourcenmessung, CRIU-/runc-Artefakte |
| Monitor | benke3 / 192.168.13.20 | CLM, ursprüngliche Probes, HTTP-Referenz, Client-PCAP, Runarchiv |

Aktuelle lokale Parameter: 30 Wiederholungen je Workflow/Szenario; acht Szenarien
und zwei Workflows ergeben 480 geplante Migrationsversuche. Die beiden Workflows
heißen im CLI weiterhin `precopy` (full dump/restore, null Pre-dumps, shared images)
und `postcopy`. Gunicorn bleibt bei zwei Workern mit acht Threads.

HTTP/L4 werden weiterhin alle 50 ms mit effektiv 40 ms Sockettimeout gemessen.
Die neue HTTP-Referenz fragt nur VIP `/health` alle nominell 10 ms mit ebenfalls
40 ms Sockettimeout ab, mit höchstens 32 gleichzeitig offenen Requests. Eine
40-ms-Gesamtfrist wird **nicht** eingeführt. Ein zusätzlicher technischer
1-s-Watchdog ist gesondert als Instrumentierungsgrenze gekennzeichnet. Sollslots,
die nicht gesendet werden konnten, werden protokolliert.

Der Hauptvergleich erhält die Klassifikation des bisherigen Raw-Clients:
HTTP-Status 200 und Antwortlesen bis EOF ohne Transportausnahme. Zusätzlich
prüft die Referenz HTTP-Framing und den erwarteten Body `OK\n`; dafür stehen
`message_complete`, `body_ok`, `strict_ok` und `protocol_error` separat im Log.
Ein vollständiger gültiger Healthresponse wird damit von einem bloßen
Status-200-Befund unterschieden, ohne die Vergleichsregel nur einseitig zu ändern.

Nach Laststart werden 5 s beobachtet, danach das vorhandene Migrationsskript
aufgerufen und nach seiner Rückkehr weitere 10 s beobachtet. Der bisherige
2-s-Startvorlauf des Originalmonitors bleibt zusätzlich bestehen. `wrk` und die
anderen Lastgeneratoren laufen bereits in wiederholenden Schleifen; ein
30-s-wrk-Aufruf begrenzt nicht die Gesamtdauer des Lastprozesses. Eine Wiederholung
eines Lastaufrufs kann neue Verbindungen erzeugen und ist kein Sessionerhalt.

Die bisherigen Pfade bleiben erhalten:

- Runarchive: `/mnt/criu/runs/batches/<batch_id>/runs/<nr>/`
- Laufende Originalmonitor-/Lastlogs: `/mnt/criu/logs/`
- Temporäre Zusatzmessung auf **jedem** Host: `/var/tmp/clm-validation/<run_id>/<role>/`

Die Zusatzmessung schreibt während des Messfensters lokal. Danach werden die
Dateien einschließlich PCAPs und Prüfsummen nach `<run>/validation/<role>/`
kopiert. Erst nach erfolgreichem Hashvergleich wird die jeweilige lokale
Stagingablage entfernt. Checkpointimages werden nicht vollständig als Kopie
archiviert: erhalten bleiben Größen-/Dateimanifeste, CRIU-Stats und Logs.

Volle Pakete werden aufgezeichnet (`capture_snaplen: 0`). Die frühere feste
512-MiB-Grenze ist deaktiviert (`max_capture_mb: 0`). Die freie Reserve von
1024 MiB und die maximale Aufzeichnungsdauer von 600 s bleiben aktiv.
Ein positiver Wert für `max_capture_mb` aktiviert bei Bedarf wieder eine Größenbegrenzung.
Bei Platzmangel, einem expliziten Größenlimit oder einem tcpdump-Abbruch endet
nur der Paketmitschnitt; das Ressourcensampling läuft bis zum angeforderten
Stopp bzw. zum Zeitlimit weiter. Capture- und Ressourcenstatus sowie ihre
Endzeitpunkte werden getrennt gespeichert. Die Reserveschwelle wird periodisch
geprüft und kann zwischen zwei Prüfungen unterschritten werden; sie ist keine Diskquote.
Ein erreichtes Limit, Captureverlust oder zu wenig Platz ist **kein vollständiger
Mitschnitt**: Der Run erhält den Fehler, und der Batch hält nach dem Run an.
Lokale nicht archivierte Daten bleiben bei einem Kopierfehler erhalten. Diese
Grenzen können in der vorhandenen env angepasst werden. 60 Runs eines Szenarios
passen wegen der PCAPs nicht garantiert auf die bisherige VM-Disk. In diesem Fall
vor Ablauf des Szenarios herunterladen; niemals einen Grenzabbruch als volle
Messung behandeln oder das Platzproblem durch stilles Überschreiben lösen.

## 1. Kopierpaket auf dem Windows-Rechner erzeugen

PowerShell im Messrepo:

```powershell
Set-Location 'D:\ContainerLiveMigration'
git branch --show-current
git rev-parse main
$payload = Join-Path $PWD ('results\validation-code-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.tar.gz')
python scripts/package_validation.py --output $payload
```

Der Branch muss `validation/2026-10-measurements` sein. Das Paket enthält den
aktuellen Dateistand einschließlich neuer/uncommittierter Dateien und
`config/env.yaml`. Shell-/Pythondateien erhalten LF-Zeilenenden. Es enthält keine
`.git`-Datenbank, Messdaten, virtuellen Umgebungen oder lokalen Agentenanweisungen.
`VALIDATION-PAYLOAD.json` enthält die Hashes der übertragenen Dateien.

Auf die drei vorhandenen SSH-Zugänge kopieren:

```powershell
foreach ($labHost in @('benke1', 'benke2', 'benke3')) {
    scp $payload ($labHost + ':~/clm-validation-code.tar.gz')
    if ($LASTEXITCODE -ne 0) { throw ('Kopieren fehlgeschlagen: ' + $labHost) }
}
```

## 2. Auf jedem Host auf den Arbeitsbranch wechseln und entpacken

Die folgenden Befehle einmal auf **benke1, benke2 und benke3** ausführen:

```bash
cd ~/ContainerLiveMigration
git status --short
git diff > "$HOME/clm-before-validation.patch"
if [ -f config/env.yaml ]; then
    cp -p config/env.yaml "$HOME/clm-env-before-validation.yaml"
fi
if git show-ref --verify --quiet refs/heads/validation/2026-10-measurements; then
    git switch validation/2026-10-measurements
else
    git switch -c validation/2026-10-measurements main
fi
```

Vorhandene eigene Änderungen und ungetrackte Dateien zuerst prüfen/sichern; das
Entpacken ersetzt die im Paket enthaltenen Codedateien. Kein Reset, Pull, Commit
oder Merge ist erforderlich. Erst auf dem Arbeitsbranch entpacken:

```bash
test "$(git branch --show-current)" = validation/2026-10-measurements || exit 1
tar --no-same-owner -xzf "$HOME/clm-validation-code.tar.gz"
python3 scripts/package_validation.py --verify
git branch --show-current
git rev-parse main
sudo -n python3 --version
```

Das Prüfkommando muss `"ok": true` melden. Source/Destination brauchen für den
Helper keine neue Python-Umgebung und keine zusätzlichen Pythonpakete. Bestehende
CRIU-/runc-/NFS-/VIP-Konfiguration bleibt bestehen. `tcpdump` wird auf benke1 und
benke3 benötigt; `python3` auf allen drei. `crit` ist für sofort decodierte
CRIU-Statistiken hilfreich; ohne `crit` bleiben die Rohstatistiken erhalten.
Nichtinteraktives sudo für den Helper muss funktionieren, wie es für den
automatisierten Laborablauf erforderlich ist. Bei fehlenden Werkzeugen oder
Berechtigungen diese konkret im Labor herstellen; es wurde nichts ferninstalliert.

## 3. Auf benke3 den Runner prüfen

Die vorhandene virtuelle Umgebung mit den bisherigen CLM-Abhängigkeiten benutzen:

```bash
cd ~/ContainerLiveMigration
source .venv/bin/activate
python -m clm.cli preflight --env config/env.yaml --dry-run
python -m clm.cli preflight --env config/env.yaml
```

`--dry-run` zeigt nur Konfiguration. Das normale Preflight führt unter anderem
SSH-, NFS-Schreib- und CRIU-Prüfungen aus. Die neue Prüfung vergleicht zusätzlich
die tatsächlich verwendeten Code-Dateihashes auf allen Hosts, Interfaces,
Werkzeuge und freien Speicher. Gleiche Git-Commits allein reichen bei diesem
uncommittierten Kopierstand nicht aus. Unterschiedliche HEADs werden bei
aktivierter Validierung als Warnung berichtet; unterschiedliche Codedateien
blockieren den Lauf. Die Inventur steht unter
`/mnt/criu/logs/validation-preflight/meta/validation_inventory.json`.

Alle folgenden Messbefehle laufen auf benke3. Falls die virtuelle Umgebung auf
deinem Host anders heißt, die vorhandene passende Umgebung aktivieren. Die
explizite Form `python -m clm.cli` verwendet den Code aus diesem Repository.

## 4. Je einen echten Funktionstest ausführen

Diese beiden Tests sind echte Migrationen. Ihre Metadaten-ID ist absichtlich
separat, damit sie nicht in die 30er-Kampagne eingehen. Keine weitere env nötig:

```bash
python -m clm.cli run --env config/env.yaml --method precopy --load idle --repeats 1 --campaign-id validation_test_2026-10
python -m clm.cli run --env config/env.yaml --method postcopy --load idle --repeats 1 --campaign-id validation_test_2026-10
```

CLM gibt den genauen Batchpfad aus. Im Run `runs/0001/` sollten vorhanden sein:

```text
meta/config_snapshot.yaml
meta/clock_offsets.json
meta/validation_inventory.json
meta/validation_window.json
meta/validation.json
monitor/mon-http.csv                 # ursprüngliche Reihe, Socketdaten angehängt
monitor/mon-l4.csv
events/events.ndjson
migrate/<method>.log
validation/monitor/reference.ndjson
validation/monitor/traffic.pcap
validation/source/traffic.pcap       # Standard: Source als Migrationsmesspunkt
validation/{monitor,source,dest}/resources.ndjson
validation/{monitor,source,dest}/manifest.json
validation/{source,dest}/images.json
validation/{source,dest}/artifacts/  # vorhandene Stats und Logs
summary.json
validation_summary.json
status.json
```

`meta/validation.json` beschreibt Erfassungs-/Archivfehler. `status.json` trennt
Migrationsstatus und gegebenenfalls `measurement_status: incomplete`.
`validation_summary.json` enthält den gemeinsamen 50-/10-ms-Vergleich,
Fehleranteile, Zeitfenster, Vorher-/Während-/Nachher-Latenzen und vorhandene
Aufwandsdaten. Eine erfolgreiche Migration garantiert keine vollständige
Instrumentierung. Fehlergründe immer mit aufbewahren.

## 5. Die 30er-Runs szenarienweise starten

Beispiel für idle: zwei Workflows mit je 30 Versuchen:

```bash
python scripts/run_validation_scenario.py --env config/env.yaml --load idle
```

Der Wrapper startet zuerst full dump/restore, dann post-copy. Bei einem Fehler
hält er an. Er protokolliert die genauen Batchpfade in einer neuen Datei wie
`/mnt/criu/logs/validation-idle-20261021_153000/batches.txt`; der tatsächliche Pfad
wird ausgegeben. Nicht `last:2` benutzen, weil zwischenzeitlich andere Batches
entstehen können. Fehlversuche bleiben sichtbar und werden nicht automatisch
durch zusätzliche erfolgreiche Versuche ersetzt.

Nach Download/Bereinigung genauso für `cpu`, `download`, `upload`, `stream`,
`wrk1`, `wrk2`, `wrk3` aufrufen. Keine acht Szenarien gleichzeitig starten.
Strg+C beendet den aktuellen Runner; der Fehlerpfad versucht die Zusatzmessung
zu stoppen und zu sichern. Abgebrochene Workflows/Containerzustände vor einem
neuen Start prüfen; ein Abbruch ist kein normal abgeschlossener Run.

## 6. Daten nach einem Szenario herunterladen und prüfen

PowerShell auf Windows; den **ausgegebenen** Manifestpfad einsetzen:

```powershell
Set-Location 'D:\ContainerLiveMigration'
$manifestRemote = '/mnt/criu/logs/validation-idle-20261021_153000/batches.txt'
$localRoot = Join-Path $PWD 'measurement_data\validation_campaign_2026-10\batches'
New-Item -ItemType Directory -Force -Path $localRoot | Out-Null
scp ('benke3:' + $manifestRemote) (Join-Path $PWD 'results\scenario-batches.txt')
if ($LASTEXITCODE -ne 0) { throw 'Manifestdownload fehlgeschlagen' }
$batchPaths = @(Get-Content 'results\scenario-batches.txt' | Where-Object { $_.Trim() })
foreach ($batchPath in $batchPaths) {
    scp -r ('benke3:' + $batchPath.Trim()) $localRoot
    if ($LASTEXITCODE -ne 0) { throw ('Batchdownload fehlgeschlagen: ' + $batchPath) }
}
$verifyArgs = @('-m', 'clm.validation_analysis', '--batches-root', $localRoot, '--verify-archive')
foreach ($batchPath in $batchPaths) {
    $verifyArgs += @('--batch-id', ($batchPath.Trim() -split '/')[-1])
}
python @verifyArgs
if ($LASTEXITCODE -ne 0) { throw 'Rohdatenprüfung fehlgeschlagen; Hostdaten behalten' }
```

Diese Prüfung vergleicht die übertragenen PCAP-/Ressourcen-/Stats-/Logdateien
mit den gespeicherten Hashes und prüft die benötigten Run-Dateien. Bei
Fehler/fehlenden Dateien nicht löschen. Testbatches benötigen für die
Auswertung/Prüfung zusätzlich `--campaign validation_test_2026-10`.

Auf benke3 anschließend die **tatsächlichen Batch-IDs** einsetzen. Zuerst zeigt
das Skript die Pfade; `--execute` entfernt nur diese ausgewählten archivierten
Validierungsbatches, deren Legacy-Links und ihre duplizierten Laufzeitlogs:

```bash
python scripts/cleanup_validation_batches.py --env config/env.yaml --batch BATCH_ID_1 --batch BATCH_ID_2
python scripts/cleanup_validation_batches.py --env config/env.yaml --batch BATCH_ID_1 --batch BATCH_ID_2 --execute
df -h /mnt/criu /var/tmp
```

Erst nach erfolgreichem Downloadvergleich ausführen. Andere Kampagnen und
laufende/unvollständig archivierte Batches werden abgelehnt. Für die beiden
Testbatches zusätzlich `--campaign-id validation_test_2026-10` angeben. Bei
fehlgeschlagener Archivierung kann noch Staging auf Source/Destination liegen;
die genaue Stelle steht in `meta/validation.json`. Diese Daten zuerst sichern
und untersuchen. Das Cleanupskript löscht keine dort verbliebenen CRIU-Images.

## 7. Auswertung

Die HTTP-/Ressourcen-/Imageauswertung erfolgt bereits pro Run. Nach dem Download
ist sie unabhängig vom Labor reproduzierbar:

```powershell
python -m clm.validation_analysis --batches-root measurement_data/validation_campaign_2026-10/batches --output results/validation_2026-10
```

Für Paket-/TCP-Auswertung muss `tshark` auf dem Auswerterechner im PATH liegen.
Unter Linux/auf benke3 funktioniert dieselbe Python-Modulform. Beispiel für einen
einzelnen tatsächlichen Run:

```bash
python -m clm.validation_analysis --run-dir /mnt/criu/runs/batches/BATCH_ID/runs/0001 --pcap
```

Oder offline alle heruntergeladenen Kampagnenruns mit `--pcap` auswerten.
Abgeleitete Ausgaben stehen in `validation_summary.json`, zusätzlich unter
`summary.json.validation`; zusammengefasste HTTP-Ergebnisse in `metrics.csv`
und `runs.json` des gewählten Ausgabeordners. `summary_stats.json` fasst
Runkennwerte je Workflow/Szenario mit n, Median, IQR, p95 und Maximum zusammen;
Fehlversuche und unvollständige Paare behalten eigene Zähler. Die übliche CLM-Analyse kann die
zusätzlichen Felder aus `config/analysis.yaml` lesen. Nach einer erneuten alten
Einzelrunanalyse die Validierungsanalyse erneut aufrufen, damit deren zusätzliche
Felder wieder in summary.json ergänzt werden. Keine Ergebnisse nach `plots/`
schreiben oder die Juli-Daten mit diesen Runs zusammenfassen.

## Aussagegrenzen und technischer Teststand

- Der HTTP-Dauervergleich verwendet identische Monitorgrenzen von Migrationsaufruf
  bis Rückkehr plus 10 s. Fehlerintervalle werden in Abschlussreihenfolge
  rekonstruiert. Bei überlappenden Requests ist dies eine beobachtete
  Klassifikationsfolge, kein Beweis eines kontinuierlichen Serverzustands.
  Referenzlücken, fehlende Fensterabdeckung und offene Fehlerintervalle liefern
  keinen vollständigen gepaarten Dauerwert.
- Original-`rt_ms` und gesamte Probedauer sind verschieden; neue Latenzkennwerte
  verwenden Start bis Ende. Erfolgs-Latenzen und Fehler-/Timeoutanteile bleiben
  getrennt. Keine Fehlprobe bedeutet keine beobachtete Fehlprobe, keine bewiesene
  kontinuierliche HTTP-Verfügbarkeit oder Sessionkontinuität.
- Memorywerte stammen aus der erkannten cgroup, nicht nur einem Gunicorn-Worker.
  Hostübergreifende Phasen verwenden aufgezeichnete Offsetschätzungen und deren
  Unsicherheit. Fehlt eine geeignete cgroup/Quelle, steht dies in den Rohdaten.
- CRIU-Stats werden als Rohdateien und, falls verfügbar, decodiert gesichert.
  Zeiten können ineinander enthalten sein; sie werden nicht zur HTTP-Downtime
  addiert. Die dynamische Dirty-page-Rate wird durch diese Instrumentierung
  **nicht** gemessen. `file_dirty` ist kein Ersatz dafür.
- NFS-/SSH-Bytezahlen enthalten gegebenenfalls Logs, Skripte und andere
  Kontrollkommunikation. Sie sind als gemischt bezeichnet, keine isolierten
  Checkpointbytes. IP-Bytes an einem VM-Messpunkt sind kein physischer
  Wire-Trafficwert; vorhandene Offloads werden dokumentiert und beibehalten.
- TCP-Streamklassifikation verlangt einen vor der Migration etablierten
  VIP-Stream mit vorangehendem Datenverkehr und anschließendem Datenverkehr auf
  derselben Verbindungsinstanz. Fehlender RST allein reicht nicht. Unklare Fälle
  und Captureverluste werden getrennt berichtet. Nutzdatenlücken und eindeutige
  TCP-Payloadbytes sind keine isolierte TCP-Recoveryzeit bzw. vollständig
  decodierter Anwendungsgoodput.
- Lokal geprüft werden HTTP-Timeout/Pacing, synthetische Fehlerintervalle,
  Lifecycle/Cleanup sowie echte Loopback-Captures und ein synthetischer lokaler
  Gesamtpfad unter Linux. Der reale Drei-Host-/CRIU-Test erfolgt durch Erik mit
  den oben genannten Befehlen. TShark-Ausführung und konkrete CRIU-Artefakte
  müssen auf der verfügbaren Auswerte-/Laborinstallation geprüft werden.

Technische Referenzen: [CRIU Statistics](https://www.criu.org/Statistics),
[Linux cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html),
[tcpdump](https://www.tcpdump.org/manpages/tcpdump.1.html),
[TShark](https://www.wireshark.org/docs/man-pages/tshark.html).
