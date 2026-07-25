# Tavrix Metrics Ingestion 5.0

Tavrix Metrics is a fictional telemetry service created only for the Agent Factory validation experiment.

## Current contract

- Submit metric batches with `POST /v5/series`.
- One request can contain at most 50 metric series.
- A series can have at most 12 labels.
- Raw samples are retained for 14 days.
- Hourly aggregates are retained for 400 days.
- Timestamps are Unix milliseconds in UTC.

## Legacy notes

Version 4 allowed 100 series per request and retained raw samples for 7 days. Those values are obsolete and must not be presented as the current contract.
