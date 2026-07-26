# Brivane Storage 2.2

Brivane Storage is a fictional object-storage service created only for the Agent Factory Pilot.

## Current contract

- Multipart uploads require parts of at least 5 MiB, except for the final part.
- A multipart upload can contain at most 5,000 parts.
- Deleted objects remain recoverable for seven days.
- Upload clients must send a SHA-256 checksum for every part.

## Legacy notes

Version 1 required parts of at least 8 MiB and accepted an MD5 checksum. Those rules are obsolete and must not be presented as the current contract.
