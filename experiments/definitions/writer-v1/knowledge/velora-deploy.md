# Velora Deploy 4.0

Velora Deploy is a fictional release pipeline created only for the Agent Factory validation experiment.

## Current contract

- Every release moves through validate, build, canary, and promote in that order.
- The canary receives 15% of production traffic.
- The canary health window lasts 8 minutes.
- Two consecutive failed health probes trigger automatic rollback.
- Release artifacts are immutable and identified by a SHA-256 digest.

## Legacy notes

Version 3 sent 25% of traffic to the canary and used a 5-minute health window. Those values are obsolete and must not be presented as the current process.
