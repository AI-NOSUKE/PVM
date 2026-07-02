# PVM Evaluation Protocol

## Purpose

This document describes how to evaluate PVM Standard 6.0.0 without overstating what internal clustering metrics can prove.

PVM is not a ground-truth label reproducer. It is a practical pipeline for visualizing the semantic structure of free-text responses and then operating that structure as a locked baseline across future datasets.

## Evaluation Caveat

PVM Standard 6.1.0 keeps the 6.0.0 pipeline, but candidate-selection metrics are computed in one common evaluation space: `X_eval = l2_normalize(pca_base["Xp"])`. Candidate fields such as `silhouette_eval_space`, `ch_eval_space`, and `db_eval_space` refer to this shared space.

Metrics computed after Centroid Projection, such as `silhouette_projected_space`, `ch_projected_space`, and `db_projected_space`, are diagnostic and interpretation aids. They are not used as standalone candidate-quality proof, because Centroid Projection is learned from Cluster① centroids.

External validity should be assessed with stability checks, holdout lock behavior, and semantic coherence review. If a dataset has reliable ground-truth labels, label-based metrics can be added, but they should not be treated as the only target because PVM is designed for exploratory and operational structure building.

## Evaluation Axes

Use the following axes when comparing PVM with other methods.

- **Seed stability**: Run the same method with multiple random seeds and check whether cluster assignments, representative examples, and cluster-level interpretations remain stable.
- **Resampling stability**: Re-run the method on bootstrap or subsampled data and inspect whether similar semantic groups reappear.
- **Holdout baseline lock**: Build a baseline on training data, apply lock to holdout data, and check whether assignments remain interpretable without retraining.
- **Semantic coherence**: Review representative examples, boundary cases, and cluster names by human inspection or LLM-assisted review.
- **ARI / NMI when labels exist**: If trustworthy labels are available, report ARI and NMI as additional evidence. Do not use them when labels are absent or only weakly defined.

## Comparison Targets

At minimum, compare the following methods under the same input data, embedding model, preprocessing, and candidate `k` range where applicable.

- embedding + spherical k-means
- PCA → ICA① + spherical k-means
- PVM Standard 6.0.0
- BERTopic or other topic-modeling methods when relevant

This protocol does not claim that PVM will always outperform these methods. It defines a fair comparison plan.

## Suggested Procedure

1. Prepare a dataset and document the text source, filtering rules, sample size, and language.
2. Fix the embedding model and embedding prefix.
3. Run each comparison method with the same candidate `k` range when possible.
4. Repeat each method across multiple random seeds.
5. Run resampling checks with bootstrap or fixed-rate subsampling.
6. Build a baseline on a training split and apply lock to holdout data when the method supports it.
7. Review representative examples, boundary examples, and cluster naming consistency.
8. Report internal metrics and qualitative findings together.

## Reporting Format

Use a table with at least the following fields.

| field | description |
|---|---|
| dataset | Dataset name or source |
| sample size | Number of texts used |
| embedding model | Embedding model and prefix |
| method | Compared method |
| k | Number of clusters |
| silhouette_eval_space | Cosine silhouette in the common PCA L2 evaluation space |
| ch_eval_space | Calinski-Harabasz diagnostic in the common PCA L2 evaluation space |
| db_eval_space | Davies-Bouldin diagnostic in the common PCA L2 evaluation space |
| entropy balance | Cluster size balance metric |
| silhouette_projected_space | Diagnostic only; do not treat as external validity proof |
| stability | Seed or resampling stability summary |
| holdout lock consistency | Whether holdout assignments remain interpretable under locked baseline |
| qualitative coherence note | Human or LLM-assisted notes about representative examples and cluster naming |

## Interpretation Guidelines

- Treat `*_eval_space` internal metrics as screening signals, not final proof. Treat `*_projected_space` metrics as diagnostics only.
- Prefer methods whose clusters remain stable and interpretable under seed changes and resampling.
- Treat holdout lock behavior as important for operational use because PVM is designed for fixed baseline workflows.
- Clearly separate measured benchmark results from hypotheses or future evaluation plans.
- Do not claim PVM superiority without running the comparison and reporting the evidence.
