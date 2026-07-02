# PVM Standard 6.0.0 Release Notes

## Overview

PVM Standard 6.0.0 refreshes the internal semantic-space construction used by `PVM.py` while keeping the project as a single-file local CLI.

The CLI workflow remains centered on local execution, automatic input detection, candidate exploration, baseline creation, cluster lock, unlock, and history management. The main change is the standard transformation core.

## New Standard Pipeline

PVM Standard 6.0.0 uses the following pipeline:

```text
Embedding
→ PCA
→ ICA①
→ Cluster①
→ Centroid Projection
→ Cluster②
```

The standard `transform_mode` is:

```text
full_original_pvm
```

## Change From the Old full_pvm / Full-Document Second-ICA Route

The older `full_pvm` route used:

```text
PCA → ICA① → full-document second-ICA(k−1) → clustering
```

PVM Standard 6.0.0 does not treat that old route as the standard. The new standard uses Cluster① in the ICA① space, learns a between-class centroid projection from the Cluster① centroids, and then runs Cluster②. Current v6.1.0 code can load schema 2.0 baselines with a warning, but the added ICA①-space novelty gate is unavailable for those baselines.

## Schema Version

```text
SCHEMA_VERSION = "2.1"
SCRIPT_VERSION = "PVM-standard-6.1.0"
```

## Baseline Compatibility

Baselines older than schema 2.0 are not compatible with the PVM Standard 6.x line.

Schema 2.0 baselines can be read by v6.1.0 with a `pre_projection_gate_missing` warning. They continue with the previous final-space gate only. For the full schema 2.1 behavior, including the ICA①-space novelty gate, recreate baselines with v6.1.0.

## Evaluation Caveat

In v6.1.0, candidate-selection metrics are computed in a common evaluation space (`X_eval = l2_normalize(pca_base["Xp"])`). Projected-space metrics such as `silhouette_projected_space` remain diagnostic and interpretation aids; they are not standalone proof of external validity and are not used as the primary candidate-quality evidence.

Evaluation should also include:

- stability under seed changes
- stability under resampling
- holdout data applied through baseline lock
- human or LLM-assisted review of representative examples, boundary examples, and cluster naming
- ARI/NMI only when reliable ground-truth labels exist

See [docs/evaluation_protocol.md](docs/evaluation_protocol.md) for the proposed evaluation protocol.

## Python Runtime Support

Current code reports `PVM-standard-6.1.0` and keeps the PVM Standard 6.0.0 core pipeline. The update adds schema 2.1 metadata, common evaluation-space candidate scoring, and an ICA①-space novelty gate for lock/unlock. CI checks Python 3.13 and Python 3.14 for dependency installation, compile, version output, and self-check.

## Tested Items

The following checks were run during the migration and release preparation:

```text
python -m py_compile PVM.py
python PVM.py --version
python PVM.py --help
python PVM.py --self-check
python PVM.py --input_csv examples\sample_texts.csv --text_col text --show-candidates
python PVM.py --input_csv examples\sample_texts.csv --text_col text --project standard6_final_smoke
python PVM.py --input_csv examples\sample_texts.csv --text_col text --project standard6_final_smoke
python PVM.py --input_csv <sample_with_added_rows.csv> --text_col text --project standard6_final_smoke --unlock
```

Observed results included:

- version output: `PVM-standard-6.1.0`
- candidate mode: `full_original_pvm`
- candidate `fallback_level=0`
- baseline metadata: `schema_version="2.1"`
- baseline metadata: `transform_mode="full_original_pvm"`
- lock execution completed
- unlock execution completed and created a new history version

## Distribution Note

`PVM.py` remains a single-file local CLI by design. The goal is to keep local execution, auditability, copy-based distribution, and confidential-data workflows simple. Library packaging, PyPI publication, license changes, and file splitting are outside the scope of this release.
