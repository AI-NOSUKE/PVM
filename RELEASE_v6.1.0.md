# PVM Standard 6.1.0 Release Notes

## Summary

PVM Standard 6.1.0 is a robustness update for PVM Standard 6.0.0.
The core pipeline is unchanged:

```text
Embedding → PCA → ICA① → Cluster① → Centroid Projection → Cluster②
```

This release moves candidate scoring to a shared evaluation space and adds a pre-projection novelty gate for lock/unlock.

## Main changes

- Candidate-selection metrics are now computed in a shared PCA evaluation space.
- Projected-space metrics are kept as diagnostics only.
- `total_score` no longer depends on projected-space self-amplified metrics.
- lock/unlock novelty detection now uses:
  - final-space gate
  - ICA① pre-projection gate
- schema updated from `2.0` to `2.1`
- schema 2.0 baselines remain readable with `pre_projection_gate_missing` warning
- unlock-resaved baselines include schema 2.1 ICA① gate fields
- `spherical_kmeans()` empty-cluster reassignment now avoids reusing the same farthest row
- docs now distinguish `silhouette_eval_space` from `silhouette_projected_space`

## Notes

Silhouette values may be lower than in v6.0.0.
This is expected. v6.0.0 reported values from the projected space, where separation could be inflated by Centroid Projection.
v6.1.0 reports candidate-selection metrics from a shared evaluation space, so values are more conservative.

## Schema Version

```text
SCHEMA_VERSION = "2.1"
SCRIPT_VERSION = "PVM-standard-6.1.0"
```

## Compatibility

Schema 2.0 baselines remain readable. When a schema 2.0 baseline is loaded, PVM reports `pre_projection_gate_missing` and continues with the previous final-space gate only.

For the full v6.1.0 behavior, including the ICA① pre-projection novelty gate and schema 2.1 gate metadata, recreate or unlock-resave the baseline with v6.1.0.

## Tested Items

The release was verified with:

```text
python -m py_compile PVM.py
python PVM.py --version
python PVM.py --self-check
```

GitHub Actions checks Python 3.13 and Python 3.14 for dependency installation, compile, version output, and self-check.