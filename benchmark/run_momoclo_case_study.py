# -*- coding: utf-8 -*-
"""Run the published PVM 6.2.4 Momoclo ablation case study.

This script imports the repository's PVM.py and never modifies its baseline or
runtime state.  It compares four transformation stages with one shared
embedding matrix and writes aggregate metrics plus mechanically selected text
examples for human review.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.metrics import adjusted_rand_score, silhouette_score


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import PVM  # noqa: E402


PCA_VAR = 0.90
ABLATION_SEEDS = (42, 52, 62, 72, 82)
LOCK_SPLIT_SEEDS = tuple(range(2024, 2034))
VARIANTS = (
    ("V1", "embedding + spherical k-means"),
    ("V2", "PCA + spherical k-means"),
    ("V3", "PCA + ICA1 + spherical k-means"),
    ("V4", "PVM full (V3 + Centroid Projection)"),
)


def clean_text(value: Any, limit: int = 180) -> str:
    text = PVM.normalize_text(value)
    return text[:limit] + ("..." if len(text) > limit else "")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def array_sha256(array: np.ndarray) -> str:
    value = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(value.shape).encode("ascii"))
    digest.update(str(value.dtype).encode("ascii"))
    digest.update(value.tobytes())
    return digest.hexdigest()


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def load_sample(
    input_csv: Path,
    text_col: str,
    id_col: Optional[str],
    sample: int,
    sample_seed: int,
) -> pd.DataFrame:
    raw = PVM.read_table(input_csv, "csv")
    detected_text, detected_id = PVM.autodetect_columns(raw, text_col, id_col)
    prepared, excluded = PVM.prepare_input_dataframe(raw, detected_text, detected_id)
    if excluded:
        print(f"[input] excluded blank/missing texts: {excluded}")
    if sample and len(prepared) > sample:
        prepared = prepared.sample(n=sample, random_state=sample_seed).reset_index(drop=True)
    if len(prepared) < 200:
        raise ValueError("case study requires at least 200 valid texts")
    return prepared[["id", "text"]].reset_index(drop=True)


def load_or_compute_embeddings(
    frame: pd.DataFrame,
    embeddings: Optional[Path],
    model: str,
    batch: int,
    max_len: int,
) -> tuple[np.ndarray, str, float]:
    if embeddings is not None:
        X = np.load(embeddings).astype(np.float32)
        if len(X) != len(frame):
            raise ValueError(
                f"embedding row mismatch: embeddings={len(X)}, texts={len(frame)}"
            )
        return X, "cache", 0.0
    started = time.perf_counter()
    X, device = PVM.compute_embeddings(
        frame["text"].tolist(), model, batch, max_len,
        embedding_prefix=PVM.DEFAULT_EMBEDDING_PREFIX,
    )
    return X.astype(np.float32), str(device), time.perf_counter() - started


def shuffle_columns(X: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    shuffled = np.array(X, dtype=np.float32, copy=True)
    for column in range(shuffled.shape[1]):
        shuffled[:, column] = shuffled[rng.permutation(len(shuffled)), column]
    return shuffled


def align_labels(reference: np.ndarray, target: np.ndarray, k: int) -> np.ndarray:
    overlap = np.zeros((k, k), dtype=np.int64)
    for left, right in zip(reference, target):
        overlap[int(left), int(right)] += 1
    rows, cols = linear_sum_assignment(-overlap)
    mapping = {int(col): int(row) for row, col in zip(rows, cols)}
    return np.asarray([mapping[int(label)] for label in target], dtype=int)


def pairwise_ari(labels: list[np.ndarray]) -> Optional[float]:
    values = [
        adjusted_rand_score(labels[i], labels[j])
        for i in range(len(labels))
        for j in range(i + 1, len(labels))
    ]
    return float(np.mean(values)) if values else None


def exact_retry_config() -> PVM.IcaRetryConfig:
    return replace(PVM.DEFAULT_ICA_RETRY, allow_dim_fallback=False)


def variant_run(
    name: str,
    X: np.ndarray,
    k: int,
    ica_dim: int,
    seed: int,
    cache: dict[Any, Any],
    retry: PVM.IcaRetryConfig,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    if name == "V1":
        cluster = PVM.spherical_kmeans(X, k, seed)
        return cluster["labels"], cluster["dists"], {"cluster": cluster}
    if name == "V2":
        Xp = PVM.get_pca_base(X, PCA_VAR, seed, cache)["Xp"]
        cluster = PVM.spherical_kmeans(Xp, k, seed)
        return cluster["labels"], cluster["dists"], {"cluster": cluster}
    if name == "V3":
        stage1 = PVM.get_stage1_result(X, PCA_VAR, ica_dim, seed, cache, retry)
        if not stage1["ica1_success"] or int(stage1["d1"]) != int(ica_dim):
            raise RuntimeError(f"ICA1 did not converge exactly at d={ica_dim}")
        cluster = PVM.spherical_kmeans(stage1["Xi1"], k, seed)
        return cluster["labels"], cluster["dists"], {
            "cluster": cluster,
            "stage1": stage1,
        }
    if name == "V4":
        bundle, Xfinal, info = PVM.fit_transforms(
            X, PCA_VAR, ica_dim, k, seed, cache, retry,
        )
        if (
            bundle.transform_mode != "full_original_pvm"
            or int(bundle.ica1_n_components) != int(ica_dim)
        ):
            raise RuntimeError(
                f"V4 was not exact strict-full: {bundle.transform_mode}, "
                f"d={bundle.ica1_n_components}"
            )
        cluster = PVM.spherical_kmeans(Xfinal, k, seed)
        return cluster["labels"], cluster["dists"], {
            "cluster": cluster,
            "bundle": bundle,
            "Xfinal": Xfinal,
            "info": info,
        }
    raise ValueError(name)


def run_ablation(
    X: np.ndarray,
    data_kind: str,
    k: int,
    ica_dim: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    retry = exact_retry_config()
    base_cache: dict[Any, Any] = {}
    Xeval = PVM.l2_normalize(
        PVM.get_pca_base(X, PCA_VAR, 42, base_cache)["Xp"]
    )
    rows: list[dict[str, Any]] = []
    labels_by_variant: dict[str, list[np.ndarray]] = {name: [] for name, _ in VARIANTS}
    seed42: dict[str, Any] = {}

    for seed in ABLATION_SEEDS:
        seed_cache: dict[Any, Any] = {}
        payloads: dict[str, Any] = {}
        for name, description in VARIANTS:
            row = {
                "experiment": "ablation",
                "data_kind": data_kind,
                "variant": name,
                "description": description,
                "seed": seed,
                "status": "ok",
                "silhouette_eval": None,
                "entropy_balance": None,
                "changed_from_v3_count": None,
                "changed_from_v3_rate": None,
                "error": "",
            }
            try:
                labels, dists, payload = variant_run(
                    name, X, k, ica_dim, seed, seed_cache, retry,
                )
                labels = np.asarray(labels, dtype=int)
                row["silhouette_eval"] = float(
                    silhouette_score(
                        Xeval, labels, metric="cosine",
                        sample_size=min(4000, len(labels)), random_state=0,
                    )
                )
                row["entropy_balance"] = float(PVM.entropy_balance(labels, k))
                labels_by_variant[name].append(labels)
                payloads[name] = {
                    "labels": labels,
                    "dists": np.asarray(dists, dtype=np.float32),
                    **payload,
                }
            except Exception as exc:  # recorded by variant and seed
                row["status"] = "error"
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)

        if "V3" in payloads and "V4" in payloads:
            v3_labels = payloads["V3"]["labels"]
            v4_aligned = align_labels(v3_labels, payloads["V4"]["labels"], k)
            changed = v3_labels != v4_aligned
            for row in rows[-len(VARIANTS):]:
                if row["variant"] == "V4" and row["status"] == "ok":
                    row["changed_from_v3_count"] = int(changed.sum())
                    row["changed_from_v3_rate"] = float(changed.mean())
            payloads["V4"]["aligned_labels"] = v4_aligned
        if seed == 42:
            seed42 = payloads

    summary: dict[str, Any] = {}
    for name, description in VARIANTS:
        ok = [row for row in rows if row["variant"] == name and row["status"] == "ok"]
        summary[name] = {
            "description": description,
            "successful_seeds": len(ok),
            "stability_ari": pairwise_ari(labels_by_variant[name]),
            "mean_silhouette_eval": (
                float(np.mean([row["silhouette_eval"] for row in ok])) if ok else None
            ),
            "mean_entropy_balance": (
                float(np.mean([row["entropy_balance"] for row in ok])) if ok else None
            ),
        }
        changed_rows = [
            row for row in ok if row["changed_from_v3_rate"] is not None
        ]
        if changed_rows:
            summary[name]["mean_changed_from_v3_rate"] = float(
                np.mean([row["changed_from_v3_rate"] for row in changed_rows])
            )
    return rows, summary, seed42


def run_lock_resampling(
    X: np.ndarray,
    data_kind: str,
    k: int,
    ica_dim: int,
) -> list[dict[str, Any]]:
    retry = exact_retry_config()
    _, Xfull, _ = PVM.fit_transforms(X, PCA_VAR, ica_dim, k, 42, {}, retry)
    reference = PVM.spherical_kmeans(Xfull, k, 42)["labels"]
    rows: list[dict[str, Any]] = []

    for split_seed in LOCK_SPLIT_SEEDS:
        row = {
            "experiment": "lock_resampling",
            "data_kind": data_kind,
            "split_seed": split_seed,
            "status": "ok",
            "holdout_n": None,
            "holdout_lock_ari": None,
            "error": "",
        }
        try:
            rng = np.random.default_rng(split_seed)
            order = rng.permutation(len(X))
            holdout_n = int(round(0.30 * len(X)))
            holdout_idx, train_idx = order[:holdout_n], order[holdout_n:]
            row["holdout_n"] = holdout_n

            bundle, Xtrain_final, _ = PVM.fit_transforms(
                X[train_idx], PCA_VAR, ica_dim, k, 42, {}, retry,
            )
            train_cluster = PVM.spherical_kmeans(Xtrain_final, k, 42)
            base_threshold = float(np.quantile(train_cluster["dists"], 0.95))

            Xtrain_pre = PVM.apply_pre_projection_space(X[train_idx], bundle)
            pre_gate = PVM.compute_pre_projection_gate_state(
                Xtrain_pre, train_cluster["labels"], k, 0.95,
            )
            locked = PVM.gated_lock_assign(
                Xfinal=PVM.apply_transforms(X[holdout_idx], bundle),
                centroids=train_cluster["centroids"],
                protected_cluster_count=k,
                base_threshold=base_threshold,
                extra_accept_thresholds=[],
                extra_relative_advantage=PVM.DEFAULT_EXTRA_REL_ADV,
                Xpre=PVM.apply_pre_projection_space(X[holdout_idx], bundle),
                ica1_centroids=pre_gate["ica1_centroids"],
                ica1_base_threshold=pre_gate["ica1_base_threshold"],
            )
            row["holdout_lock_ari"] = float(
                adjusted_rand_score(locked["labels"], np.asarray(reference)[holdout_idx])
            )
        except Exception as exc:
            row["status"] = "error"
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    return rows


def summarize_lock(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        float(row["holdout_lock_ari"])
        for row in rows
        if row["status"] == "ok" and row["holdout_lock_ari"] is not None
    ]
    if not values:
        return {"successful_splits": 0}
    return {
        "successful_splits": len(values),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "minimum": float(np.min(values)),
        "maximum": float(np.max(values)),
        "below_0_5": int(np.sum(np.asarray(values) < 0.5)),
        "values": values,
    }


def assignment_margins(cluster: dict[str, Any]) -> np.ndarray:
    distances = PVM.cosine_distance_to_centroids(
        np.asarray(cluster["Xn"], dtype=np.float32),
        PVM.l2_normalize(np.asarray(cluster["centroids"], dtype=np.float32)),
    )
    ordered = np.partition(distances, kth=1, axis=1)[:, :2]
    ordered.sort(axis=1)
    return ordered[:, 1] - ordered[:, 0]


def add_example(
    lines: list[str],
    manifest: list[dict[str, Any]],
    frame: pd.DataFrame,
    index: int,
    role: str,
) -> None:
    text = PVM.normalize_text(frame.iloc[index]["text"])
    source_id = str(frame.iloc[index]["id"])
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    lines.append(f"- `{source_id}`: {clean_text(text)}")
    manifest.append({
        "role": role,
        "row_index": index,
        "source_id": source_id,
        "text_sha256": text_hash,
    })


def qualitative_output(
    frame: pd.DataFrame,
    seed42: dict[str, Any],
    best: Any,
    k: int,
    output_dir: Path,
) -> list[dict[str, Any]]:
    required = {"V1", "V3", "V4"}
    if not required.issubset(seed42):
        raise RuntimeError("seed 42 qualitative payload is incomplete")
    manifest: list[dict[str, Any]] = []
    lines = [
        "# Mechanically selected qualitative examples", "",
        "These excerpts are selected by fixed rules, not by whether they make PVM look favorable.",
        "Two center-nearest texts are shown per V1/V4 cluster; two extremes per ICA1 axis side;",
        "and up to two lowest-V3-margin texts per non-empty V3->V4 transition.", "",
    ]

    for variant in ("V1", "V4"):
        payload = seed42[variant]
        labels = payload.get("aligned_labels", payload["labels"])
        dists = payload["dists"]
        lines.extend([f"## {variant} cluster centers", ""])
        for cluster_id in range(k):
            indices = np.where(labels == cluster_id)[0]
            selected = indices[np.argsort(dists[indices])[:2]]
            lines.extend([f"### {variant} cluster {cluster_id} (n={len(indices)})", ""])
            for index in selected:
                add_example(
                    lines, manifest, frame, int(index),
                    f"{variant}_cluster_{cluster_id}_center",
                )
            lines.append("")

    stage1 = seed42["V3"]["stage1"]
    Xi1 = np.asarray(stage1["Xi1"], dtype=np.float32)
    lines.extend(["## ICA1 axis extremes", ""])
    for axis in range(Xi1.shape[1]):
        values = Xi1[:, axis]
        positive = np.argsort(values)[::-1][:2]
        negative = np.argsort(values)[:2]
        lines.extend([f"### ICA1 axis {axis + 1}: positive", ""])
        for index in positive:
            add_example(lines, manifest, frame, int(index), f"ICA1_{axis + 1}_positive")
        lines.extend(["", f"### ICA1 axis {axis + 1}: negative", ""])
        for index in negative:
            add_example(lines, manifest, frame, int(index), f"ICA1_{axis + 1}_negative")
        lines.append("")

    v3_labels = seed42["V3"]["labels"]
    v4_labels = seed42["V4"]["aligned_labels"]
    margins = assignment_margins(seed42["V3"]["cluster"])
    lines.extend(["## V3 to V4 changed assignments", ""])
    for source in range(k):
        for target in range(k):
            if source == target:
                continue
            indices = np.where((v3_labels == source) & (v4_labels == target))[0]
            if not len(indices):
                continue
            selected = indices[np.argsort(margins[indices])[:2]]
            lines.extend([f"### V3 {source} -> V4 {target} (n={len(indices)})", ""])
            for index in selected:
                add_example(
                    lines, manifest, frame, int(index),
                    f"V3_{source}_to_V4_{target}_lowest_margin",
                )
            lines.append("")

    lines.extend([
        "## Selection metadata", "",
        f"- selected plan: d={best.ica1_dim}, k={best.k}, CP rank={best.cp_rank}",
        f"- CP changed count reported by search: {best.changed_count}",
        f"- CP boundary focus: {best.boundary_focus:.6f}",
        f"- CP core preservation: {best.core_preservation:.6f}", "",
    ])
    (output_dir / "qualitative_examples.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8",
    )
    with (output_dir / "qualitative_manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["role", "row_index", "source_id", "text_sha256"])
        writer.writeheader()
        writer.writerows(manifest)
    return manifest


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_csv", type=Path, required=True)
    parser.add_argument("--text_col", default="text")
    parser.add_argument("--id_col")
    parser.add_argument("--sample", type=int, default=5000)
    parser.add_argument("--sample_seed", type=int, default=7)
    parser.add_argument("--embeddings", type=Path)
    parser.add_argument("--model", default="cl-nagoya/ruri-v3-310m")
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--max_len", type=int, default=384)
    parser.add_argument("--k_min", type=int, default=3)
    parser.add_argument("--k_max", type=int, default=12)
    parser.add_argument("--search_budget", choices=tuple(PVM.ADAPTIVE_SEARCH_PRESETS), default="standard")
    parser.add_argument("--shuffle_seed", type=int, default=0)
    parser.add_argument("--output_dir", type=Path, required=True)
    args = parser.parse_args()

    if PVM.__version__ != "6.2.4":
        raise RuntimeError(f"this published case study expects PVM 6.2.4, got {PVM.__version__}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    frame = load_sample(
        args.input_csv, args.text_col, args.id_col, args.sample, args.sample_seed,
    )
    X, device, embedding_seconds = load_or_compute_embeddings(
        frame, args.embeddings, args.model, args.batch, args.max_len,
    )
    if args.embeddings is None:
        embedding_cache = args.output_dir / "embeddings.npy"
        np.save(embedding_cache, X)
        print(f"[cache] {embedding_cache}")
    Xshuffled = shuffle_columns(X, args.shuffle_seed)

    search_started = time.perf_counter()
    search_cache: dict[Any, Any] = {}
    candidates = PVM.explore_candidates(
        X, args.k_min, args.k_max, PCA_VAR, 42, search_cache,
        ica_retry_config=PVM.DEFAULT_ICA_RETRY,
        search_config=PVM.resolve_adaptive_search_config(args.search_budget),
    )
    best = candidates[0]
    if best.selection_tier != "strict_full" or not best.cp_effective:
        raise RuntimeError(f"selected plan is not strict_full with effective CP: {best}")
    search_seconds = time.perf_counter() - search_started
    k, ica_dim = int(best.k), int(best.ica1_dim)
    print(f"[plan] d={ica_dim}, k={k}, cp={best.cp_rank}, tier={best.selection_tier}")

    real_rows, real_summary, seed42 = run_ablation(X, "real", k, ica_dim)
    shuffled_rows, shuffled_summary, _ = run_ablation(Xshuffled, "shuffled", k, ica_dim)
    real_lock = run_lock_resampling(X, "real", k, ica_dim)
    shuffled_lock = run_lock_resampling(Xshuffled, "shuffled", k, ica_dim)
    manifest = qualitative_output(frame, seed42, best, k, args.output_dir)

    all_rows = real_rows + shuffled_rows + real_lock + shuffled_lock
    write_csv(args.output_dir / "runs.csv", all_rows)
    metadata = {
        "script_version": "momoclo-case-study-1",
        "pvm_version": PVM.SCRIPT_VERSION,
        "pvm_git_commit": git_commit(),
        "input_file_sha256": file_sha256(args.input_csv),
        "sample_size": len(frame),
        "sample_seed": args.sample_seed,
        "sample_ids_sha256": hashlib.sha256(
            "\n".join(frame["id"].astype(str)).encode("utf-8")
        ).hexdigest(),
        "embedding_shape": list(X.shape),
        "embedding_sha256": array_sha256(X),
        "embedding_model": args.model,
        "embedding_prefix": PVM.DEFAULT_EMBEDDING_PREFIX,
        "max_len": args.max_len,
        "device_or_source": device,
        "embedding_seconds": embedding_seconds,
        "pca_var": PCA_VAR,
        "k_range": [args.k_min, args.k_max],
        "search_budget": args.search_budget,
        "search_seconds": search_seconds,
        "ablation_seeds": list(ABLATION_SEEDS),
        "shuffle_seed": args.shuffle_seed,
        "lock_split_seeds": list(LOCK_SPLIT_SEEDS),
        "total_seconds": time.perf_counter() - started,
    }
    result = {
        "metadata": metadata,
        "selected_plan": asdict(best),
        "ablation": {
            "real": real_summary,
            "shuffled": shuffled_summary,
        },
        "lock_resampling": {
            "real": summarize_lock(real_lock),
            "shuffled": summarize_lock(shuffled_lock),
        },
        "qualitative_manifest_rows": len(manifest),
    }
    (args.output_dir / "metrics.json").write_text(
        json.dumps(json_safe(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[out] {args.output_dir}")


if __name__ == "__main__":
    main()
