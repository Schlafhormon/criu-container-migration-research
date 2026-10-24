# Pilotdaten 24.10.2026

16 Einzelversuche: acht Szenarien mit jeweils full dump/restore (`precopy`) und
post-copy. Alle 16 Migrationen erfolgreich, elf Versuche vollständig instrumentiert.
Keine fachliche Auswertung und keine Plots erzeugt; die integrierten Auswertungen
wurden mit `--no-analysis` aufgeschoben. Keine automatischen Ersatzversuche.

Fünf unvollständige Captures bleiben unverändert dokumentiert:
- idle/precopy: 41 Source-Kernel-Drops beim ursprünglichen 4-MiB-Puffer.
- download und upload, jeweils beide Workflows: Monitor-Capturelimit 512 MiB erreicht.

Ab idle/postcopy ist der Capture-Puffer 64 MiB; dies steht im Collectorstatus und
im zweiten Codepaket. Sonstige Messparameter und Workflows wurden beibehalten.

## Ablage

- `batches/`: alle 16 unveränderten Rohbatches mit je einem Run.
- `checkpoints/`: zusätzliche, wegen unvollständiger Captures zurückbehaltene Images.
- `execution/`: Laufstatus, Dateihashes, Transferprüfungen und Bereinigungsnachweise.
- `provenance/`: tatsächlich verwendete Codepakete/Manifeste, Arbeitsbaum-Diff,
  gesicherte Preflight-Inventur.
- `TRANSFER-STATUS.json`: Batchzuordnung und technischer Status aller Versuche.
- `FILE-HASHES.json`: SHA256 aller übrigen hier bei Übergabe vorhandenen Dateien.

Alle erhobenen Dateien wurden vollständig auf den PC kopiert und gegen die
Remote-Dateihashes geprüft. Zusätzliche Roharchivprüfungen bestanden. Die Hashes
belegen unveränderte Übertragung, nicht vollständige Captures der fünf Problemfälle.
Pilotdaten auf den Hosts erst nach erfolgreicher Kopierprüfung entfernt; ebenso
verbliebene Container/VIPs und leere Stagingordner. Dauerhafte Bundles erhalten.

`campaign.repeats: 30` in den Konfigurationssnapshots ist nur der unveränderte
Default. Tatsächlich hat jeder Batch `repeats: 1` und genau einen Run; alle CLI-
Aufrufe verwendeten `--repeats 1 --no-analysis`.

Vor einer späteren Auswertung zuerst technische Unvollständigkeitskennzeichnungen
berücksichtigen. Bisher keine TCP-/HTTP-Kennwerte oder Statistik neu berechnet.
