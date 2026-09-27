# Phase 9.4 — combined-only-miss standalone synthetic benchmark

Status: **bounded evidence, NOT ordinary application cache, no owner
architecture selection**. Based on `dd710370e755f90ee61b75ddc4df0499e1bc2166`.
This experiment complements the already published all-fresh
`PHASE9_ARCHITECTURE_SYNTHETIC_COMPARE.md`; never mix their medians.

## Experiment construction

Three identical fixed AKUZ-shaped synthetic inputs, 3000/3000/3001
events with BOM, invalid-UTF8 replacement and mixed line endings.
Nine trial workspaces, ORDER control/B-lite/missing-sidecar;
missing-sidecar/control/B-lite; B-lite/missing-sidecar/control.
Per trial, build all THREE single reports before measuring combined
in a NEW child Python process. Assert combined output is absent.
Controller validates original single report file SHA manifests and
source content SHA before/after each measured combined build.

- `control`: ordinary combined `event_stream` + fresh derive.
- `b_lite`: singles built with verified source-bound JSONL sidecars;
  combined verifies source+sidecar and reuses derived fields.
- `missing_sidecar`: pre-existing singles generated without sidecars;
  helper sees missing manifest and freshly derives combined without
  rebuilding or rewriting singles.

Single creation/setup wall+CPU is recorded SEPARATELY from combined
wall+CPU. There is no app inventory, report-intent, state lock or SQL
analytics in any trial. Source sha256 resolution before combined
is excluded equally from child wall; B-lite's further verification
SHA reads inside its prototype are included. Each child is a fresh
process; OS high-water memory is child lifetime, not isolated to
combined.generate alone. Sidecar is private in owned TemporaryDirectory
and deleted after all nine trials; no real AKUZ content is retained.

## Linux Bazzite measured proof

9/9 source SHA checks, ready-single checks and exact four-report
manifest comparisons PASS; owned workspace cleaned. Median of three
fresh child builds per mode:

| Mode | Single setup wall s | Combined wall s | Combined CPU s | MaxRSS KiB | Extra derived sidecar B |
|---|---:|---:|---:|---:|---:|
| Ordinary | 0.34418 | 0.37031 | 0.36762 | 40,364 | 0 |
| B-lite | 0.42324 | 0.22878 | 0.22627 | 41,416 | 827,976 |
| Missing sidecar fallback | 0.35025 | 0.37629 | 0.37335 | 39,596 | 0 |

This synthetic timing suggests reuse can reduce a *subsequent*
combined-only rebuild on already prepared B-lite metadata, at
extra upfront sidecar generation/disk/privacy cost. It does NOT
show throughput of full 956 MB source, long-lived app cache policy,
or safe reuse of old app reports across process restarts. Missing
sidecar correctly falls back, with no ready single regenerated.

Private per-trial numeric/hash evidence:
`diagnostics/private_phase94_combined_only_linux_v1.json`
(ignored; no log event text). Windows results, full exact-SHA
suites and final integration will be appended when independently
verified. GitHub Release unchanged.
