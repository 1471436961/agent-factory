# Quoralis Ledger API 1.4

Quoralis Ledger is a fictional accounting API created only for the Agent Factory validation experiment.

## Current contract

- Monetary amounts are submitted as integer micros; one currency unit equals 1,000,000 micros.
- Idempotency keys are retained for 48 hours.
- Daily settlement starts at 02:00 UTC.
- Entry status is one of pending, posted, or reversed.
- A posted entry cannot be edited; corrections require a separate reversal entry.

## Legacy notes

The preview release retained idempotency keys for 24 hours and accepted decimal amount strings. Those behaviors are obsolete and must not be presented as the current contract.
