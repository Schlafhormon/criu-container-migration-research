# Research on Container Live Migration with CRIU

This repository is an experimental research environment for container live
migration with CRIU and runc. It is intended for reproducible experiments,
measurement campaigns, failure analysis, and evaluation of migration
strategies under different workloads.

The research focuses on service continuity and client-visible effects during
migration:

- **Pre-copy:** iterative pre-dumps, final dump, restore, and VIP cutover
- **Post-copy:** CRIU lazy pages, restore, source forwarding, and VIP cutover
- HTTP, L4, stream, download, upload, and workload measurements
- Batch analysis and configurable plot generation

## CLM Tool

**CLM** is the experiment runner included in this repository. It coordinates
the source, destination, and monitoring hosts, executes repeatable measurement
batches, and collects the resulting artifacts.

The repository is the broader research project. CLM is one tool within it,
alongside the migration scripts, workload, monitoring, analysis, and research
documentation.

## Experiment Design

Experiments use three roles:

- **source host:** runs the original container and creates CRIU checkpoints
- **destination host:** restores the container and takes over the service
- **monitoring host:** acts as an external client and records all measurements

A shared filesystem, typically mounted at `/mnt/criu`, stores checkpoint
images, logs, run metadata, and analysis outputs. The workload is a Flask
service in a runc bundle. It exposes health, counter, info, stream, download,
upload, CPU, and delay endpoints.

The service is reached through a virtual IP address. During migration, the VIP
is moved from source to destination, followed by connection-tracking cleanup
and gratuitous ARP. This makes the measured downtime reflect the service view
of a client using the VIP, not only the internal CRIU restore time.

The paper campaign compares pre-copy and post-copy over repeated runs for:

- `idle`
- `cpu`
- `wrk1`, `wrk2`, `wrk3`
- `download`, `upload`, `stream`

Each scenario is measured for both migration methods. The paper configuration
uses `config/analysis_paper.yaml` to produce comparable scenario and method
views from the same raw monitor data.

## Published Measurement Results

The complete paper measurement results are stored under `plots/`. This
directory is the main result artifact of the repository and should be kept
consistent with the paper.

Important result folders:

- `plots/combined/all_scenarios/` - combined dataset for all scenarios and
  both migration methods
- `plots/combined/no_wrk/` - combined dataset without `wrk` profiles
- `plots/combined/only_wrk/` - combined dataset for `wrk1`, `wrk2`, and `wrk3`
- `plots/precopy/` - pre-copy-only views
- `plots/postcopy/` - post-copy-only views

Each result directory contains:

- `metrics.csv` - per-run metrics used for statistical analysis
- `summary_stats.csv` and `summary_stats.json` - grouped summary statistics
- `downtime_segments.csv` - long-format downtime phase data
- `plots/*.png` - generated figures for the paper and diagnostics

`plots/combined/all_scenarios/metrics.csv` is the broadest dataset. It contains
the full 100-run campaign for eight scenarios and two migration methods.

## Repository Layout

- `clm/` - CLM runner, batching, and analysis orchestration
- `scripts/` - bundle setup, migration, host information, and forensics
- `tools/monitor/monitor.py` - runtime monitoring and per-run analysis
- `workload/flask_app/app.py` - Flask test workload
- `config/env.example.yaml` - host, migration, monitoring, and load settings
- `config/analysis.yaml` - default metrics and plots
- `config/analysis_paper.yaml` - additional paper-oriented plots
- `plots/` - complete measurement campaign results and generated figures
- `docs/` - public research notes and archived historical notes; start at
  `docs/README.md`

## Requirements

The migration scripts run on Linux hosts and require:

- `runc` and `criu` on source and destination
- key-based SSH access and passwordless `sudo`
- a shared directory, typically NFS at `/mnt/criu`
- tools such as `iptables`, `conntrack`, `arping`, `curl`, `jq`, and `rsync`
- Python 3.9 or newer on the monitoring host
- `wrk` on the monitoring host when using `wrk1`, `wrk2`, or `wrk3`

## Setup

Install the CLM runner on the monitoring host:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp config/env.example.yaml config/env.yaml
```

Adjust `config/env.yaml` for the hosts, interfaces, VIP, bundle paths, and
load profiles.

Validate the environment:

```bash
clm preflight --env config/env.yaml
```

## Running Measurements

Run pre-copy or post-copy batches:

```bash
clm run --env config/env.yaml --method precopy --repeats 10
clm run --env config/env.yaml --method postcopy --repeats 10
```

Load profiles are repeatable or comma-separated:

```bash
clm run --env config/env.yaml --method precopy --load cpu --repeats 10
clm run --env config/env.yaml --method postcopy --load wrk2,download --repeats 10
clm run --env config/env.yaml --method postcopy --load stream --load upload
```

Supported profiles:

- `idle`
- `cpu` (`heavy` is a compatibility alias)
- `wrk1`, `wrk2`, `wrk3`
- `download`, `upload`, `stream`

Useful run options:

```bash
# Analyze the batch and generate plots after the run.
clm run --env config/env.yaml --method precopy --repeats 10 --analyse

# Run migration without monitoring.
clm run --env config/env.yaml --method precopy --no-monitor

# Run monitoring without migration as a control measurement.
clm run --env config/env.yaml --method precopy --no-migrate

# Keep checkpoint and restore artifacts for investigation.
clm run --env config/env.yaml --method postcopy --no-cleanup
```

## Analysis and Plots

Analyze recent or explicit batches:

```bash
clm analyse --env config/env.yaml --batch last
clm analyse --env config/env.yaml --batch last:4 --with-plots
clm analyse --env config/env.yaml \
  --batch /mnt/criu/runs/batches/<BATCH_ID>
```

Combine several batches:

```bash
clm analyse --env config/env.yaml --batch last:4 \
  --combine-batches --with-plots

clm plots --env config/env.yaml --batch last:4 \
  --combine-batches --config config/analysis_paper.yaml
```

A batch manifest can select batch IDs or paths, one per line:

```bash
clm analyse --env config/env.yaml \
  --batch-manifest batches.txt --combine-batches --with-plots
```

Standalone entry points are also available:

```bash
python3 tools/analyze.py --batch last --config config/analysis.yaml
python3 tools/plots.py --batch last --config config/analysis.yaml
```

Generated analysis artifacts, including the checked-in campaign results under
`plots/`, include:

- `metrics.csv`: one row per run with extracted and derived metrics
- `summary_stats.csv` and `summary_stats.json`: grouped descriptive statistics
- `downtime_segments.csv`: normalized downtime phases for timeline plots
- `plots/*.png`: generated figures

Default plots are defined in `config/analysis.yaml`. They include box plots,
median confidence-interval plots, histograms, scatter plots, downtime event
composition, and downtime timelines. Plot generation is configuration-driven;
new plots should usually be added to the YAML config rather than hard-coded.

Paper-specific plots are defined in `config/analysis_paper.yaml`. This config
extends the default analysis and adds views for:

- pre-copy versus post-copy per scenario
- all scenarios for one migration method
- non-wrk scenarios only
- wrk-only scenarios
- client-visible VIP HTTP downtime
- VIP L4 downtime
- event-path and downtime-segment breakdowns

## Artifact Layout

```text
/mnt/criu/runs/
  batches/
    <batch-id>/
      batch.json
      runs/
        0001/
          summary.json
          status.json
          events/
          meta/
          monitor/
      analysis/
        metrics.csv
        summary_stats.json
        summary_stats.csv
        plots/
  analysis/
    combined_<selector>/
```

Compatibility links or pointer directories may also be created under
`/mnt/criu/runs/<RUN_ID>/`.

## Downtime Metrics

The primary client-visible HTTP metric is:

- `vip_http_client_visible_total_down_ms`: total duration of all VIP HTTP
  downtime segments in the migration window

Related metrics:

- `vip_http_client_visible_down_segments`: number of downtime segments
- `vip_http_client_visible_outage_span_ms`: time from first failure to final
  recovery, including successful gaps
- `vip_http_downtime_ms`: cutover-near VIP HTTP downtime segment
- `vip_l4_downtime_ms`: cutover-near VIP L4 downtime segment
- `http_downtime_ms`: source-to-destination HTTP availability gap
- `l4_downtime_ms`: source-to-destination L4 availability gap
- `vip_http_cutover_gap_ms` and `vip_l4_cutover_gap_ms`: legacy two-point
  diagnostic gaps

Monitoring uses monotonic pacing and records probe start and end timestamps.
Source and destination clock offsets are estimated for each run.

## Manual Script Usage

The CLI is the recommended entry point. For manual operation, invoke shell
scripts through Bash:

```bash
bash scripts/build_runc_bundle_from_docker_image.sh
bash scripts/migrate_precopy_vip_cutover.sh
bash scripts/migrate_postcopy_lazy_pages_vip_cutover.sh
```

The scripts are configured through environment variables. Review
`config/env.example.yaml` and the corresponding script defaults before
running them.

## License

Code and scripts are licensed under the MIT License. Measurement data, plots,
figures, and documentation are licensed under CC BY 4.0.
