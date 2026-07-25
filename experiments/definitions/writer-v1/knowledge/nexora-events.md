# Nexora Events API 2.0

Nexora Events is a fictional event delivery service created only for the Agent Factory validation experiment.

## Current contract

- Submit batches with `POST /v2/events`.
- A batch can contain at most 80 events.
- The service retries failed deliveries after 10 seconds, 30 seconds, and 120 seconds.
- Clients must send `X-Nexora-Event-ID` as the duplicate-protection key.
- Duplicate-protection keys are retained for 36 hours.

## Legacy notes

Version 1 allowed 100 events per batch and documented a single 60-second retry. Those values are obsolete and must not be presented as the current contract.
