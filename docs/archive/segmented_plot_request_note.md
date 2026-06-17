# Archived Segmented Plot Request

This note preserves the original implementation request that led to
[../downtime_segments.md](../downtime_segments.md).

## Requested Outcome

- Design a segmented horizontal downtime plot.
- Show phases such as final dump, transfer, restore, health wait, and cutover.
- Support single runs, batches, and combined multi-batch analysis.
- Use existing monitor CSV, event NDJSON, `summary.json`, and analysis outputs.
- Produce a Markdown design note before implementation.

The resulting public design has been consolidated in the main documentation.
