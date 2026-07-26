# Aerilon Routing 1.4

Aerilon Routing is a fictional traffic-routing service created only for the Agent Factory Pilot.

## Current contract

- Route definitions are submitted to `POST /v3/routes`.
- One workspace can contain at most 80 active route rules.
- A published route can take up to 45 seconds to reach every edge location.
- Regional failover starts after three consecutive failed health checks.

## Legacy notes

Version 1.2 used `POST /v2/routes` and allowed 120 active route rules. Those values are obsolete and must not be presented as the current contract.
