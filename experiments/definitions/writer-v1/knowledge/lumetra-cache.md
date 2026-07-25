# Lumetra Cache 3.1

Lumetra Cache is a fictional managed cache created only for the Agent Factory validation experiment.

## Current contract

- Reads in the same region provide read-after-write consistency.
- Entry TTL must be between 30 seconds and 12 hours.
- Namespace names can contain at most 64 characters.
- Capacity pressure uses least-frequently-used (LFU) eviction.
- Purging a namespace can take up to 90 seconds to become visible in every node.

## Legacy notes

Version 2 allowed a 24-hour TTL and used least-recently-used (LRU) eviction. Those settings are obsolete and must not be presented as the current contract.
