# M5 Writer Experiment Analysis Report

> Evidence boundary: this report is rendered deterministically from `AnalysisSummary`. Conclusions apply only to its bound dataset, plan, score set, and analysis config. Formal status also requires review of the M5.5 manifest, source commit, provider, model, SDK, and price snapshot.

## Analysis Identity

| 字段 | 值 |
| --- | --- |
| Experiment | `writer-validation-v1` |
| Analysis checksum | `e1bd22e304c1e54845555b35daa1b18895fd700bdb46258e16cc02bd7bfcbe30` |
| Dataset checksum | `e8305386e305e39623ab1e852059148ed319ae63fc180a58288f1ac0a3e14a8e` |
| Definition checksum | `2a4bd8846bef6379fb38e8de771150321fe8f21d16655e7dadaa3b4d855a6570` |
| Plan checksum | `8e8ad93a8cb1b3207580c89917e4af9a6ac0c32c6ab47d83e04c6f04b233e920` |
| Score set checksum | `23babf695c02629f271533140290be6e5dfe495596b9d7d6b73f59a693b3c665` |
| Analysis config checksum | `82c867d569bd4c2dc0cc9226787aa3560f9d59fa89bbe565961e68688635fc68` |
| Tasks / repetitions | 24 / 5 |
| Bootstrap | 10000 iterations; seed=20260725; confidence=0.95 |

## Execution Completeness

| Condition | Planned | Succeeded | Failed |
| --- | ---: | ---: | ---: |
| `manual-agent` | 120 | 120 | 0 |
| `factory-agent` | 120 | 112 | 8 |

## Primary Analysis: Intention-to-treat

Execution failures use the preregistered worst-case mapping. H1/H4 effects are `FACTORY - MANUAL`; H2 is relative omission reduction.

| Hypothesis | Paired tasks | Effect | 95% CI | Absolute omission delta (95% CI) | Decision |
| --- | ---: | ---: | --- | --- | --- |
| `h1-schema-consistency` | 24 | -0.066666666667 | [-0.1, -0.033333333333] | N/A | `not-supported` |
| `h2-knowledge-omission` | 24 | -0.314685314685 | [-0.653467295548, -0.100594512195] | -0.125 [-0.213888888889, -0.043055555555] | `not-supported` |
| `h4-personalization` | 12 | -0.066666666667 | [-0.133333333333, 0] | N/A | `insufficient-evidence` |

## Succeeded-only Sensitivity Analysis

This population only examines the influence of execution failures. Its `decision` is `not-evaluated` and cannot replace primary analysis.

| Hypothesis | Paired tasks | Effect | 95% CI | Absolute omission delta (95% CI) | Decision |
| --- | ---: | ---: | --- | --- | --- |
| `h1-schema-consistency` | 24 | 0 | [0, 0] | N/A | `not-evaluated` |
| `h2-knowledge-omission` | 24 | -0.253496503497 | [-0.577317653277, -0.04241727621] | -0.100694444444 [-0.189583333333, -0.018055555556] | `not-evaluated` |
| `h4-personalization` | 12 | 0.004166666667 | [-0.0375, 0.058333333333] | N/A | `not-evaluated` |

## Reproduction Boundaries

- `summary.json` is the machine source of truth; `metrics.csv` and this report must reproduce from it byte for byte.
- This report excludes raw model responses, prompt bodies, credentials, and human ratings.
- Local checksums detect accidental corruption but cannot prevent a filesystem administrator from rewriting every artifact.
- Without the formal frozen manifest, this report must not be presented as a formal model experiment result.
