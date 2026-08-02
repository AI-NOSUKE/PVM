# PVM Standard 6.0.0 Release Notes

## Overview

PVM Standard 6.0.0 is the first PVM Standard release. It formalizes the Centroid Projection pipeline as the standard PVM route while keeping `PVM.py` as a single-file local CLI.

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

PVM Standard 6.0.0 does not treat that old route as the standard. The new standard uses Cluster① in the ICA① space, learns a between-class Centroid Projection from the Cluster① centroids, and then runs Cluster②.

## Schema Version

```text
SCHEMA_VERSION = "2.0"
SCRIPT_VERSION = "PVM-standard-6.0.0"
```

## Baseline Compatibility

Existing pre-6.0 projects should recreate baselines with PVM Standard 6.0.0. This is intentional: the meaning of the stored transform is different, because the reused transform slots now store Centroid Projection parameters rather than a second ICA model.

## Evaluation Caveat

Centroid Projection is learned from Cluster①. Therefore, internal metrics computed after projection, such as silhouette and Davies-Bouldin, are useful diagnostics and quality checks, but they are not standalone proof of external validity.

Evaluation should also include:

- stability under seed changes
- stability under resampling
- holdout data applied through baseline lock
- human or LLM-assisted review of representative examples, boundary examples, and cluster naming
- ARI/NMI only when reliable ground-truth labels exist

See [evaluation_protocol.md](../evaluation_protocol.md) for the proposed evaluation protocol.

## Python Runtime Support

The current repository later added Python 3.13 / 3.14 CI coverage, but this file records the PVM Standard 6.0.0 initial release. See [RELEASE_v6.1.0.md](RELEASE_v6.1.0.md) for the robustness update that followed.

## Tested Items

The following checks were run during the 6.0.0 migration and release preparation:

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

- version output: `PVM-standard-6.0.0`
- candidate mode: `full_original_pvm`
- candidate `fallback_level=0`
- baseline metadata: `schema_version="2.0"`
- baseline metadata: `transform_mode="full_original_pvm"`
- lock execution completed
- unlock execution completed and created a new history version

## Distribution Note

`PVM.py` remains a single-file local CLI by design. The goal is to keep local execution, auditability, copy-based distribution, and confidential-data workflows simple. Library packaging, PyPI publication, license changes, and file splitting are outside the scope of this release.
