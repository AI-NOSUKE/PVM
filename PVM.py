# -*- coding: utf-8 -*-
from __future__ import annotations

"""
PVM complete edition

A single-file CLI for Japanese free-text clustering with baseline management.

Design goals
------------
- 初回はそのまま実行で baseline を自動作成
- --show-candidates で探索のみ
- --use-plan N で候補を明示採用
- 2回目以降は保存済み baseline を用いた cluster lock
- --unlock で既存体系を壊しにくい add-only 拡張
- baseline は history/vXXX で版管理

Important behavior
------------------
- embedding は Ruri v3 のクラスタリング用途に合わせ、既定で「トピック: 」prefix を内部付与する
- 入力CSV/Excelや出力CSV/AIパケットの原文には prefix を混ぜない
- Full PVM は PCA→ICA①→クラスタ①→centroid projection→クラスタ②で実行する
- lock は baseline を再学習せず固定適用する
- unlock は既存点を再編せず、新規話題候補だけを add-only で追加判定する
- 次回 lock でも、まず base gate を見てから extra cluster を評価する二段階割当を行う
- embedding_model / embedding_prefix / max_len が baseline と一致しない場合は安全のため停止する

Current defaults
----------------
- embedding_model: cl-nagoya/ruri-v3-310m
- embedding_prefix: トピック:
- max_len: 8192
- batch: 8
"""

__version__ = "6.0.0"

import argparse
import json
import logging
import math
import platform
import re
import sys
import time
import unicodedata
import warnings
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from numpy.linalg import LinAlgError
from sklearn.decomposition import FastICA, PCA
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import adjusted_rand_score, calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.preprocessing import StandardScaler

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    class tqdm:  # type: ignore[no-redef]
        """tqdm 未インストール環境向けの最小互換シム。

        イテレータラップ (for x in tqdm(it)) と、手動モード
        (tqdm(total=...) + update()/close()) の両方に対応する。
        進捗表示は行わない。
        """

        def __init__(self, iterable=None, **kwargs):
            self._iterable = iterable

        def __iter__(self):
            return iter(self._iterable if self._iterable is not None else [])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def update(self, n=1):
            pass

        def close(self):
            pass

SCHEMA_VERSION = "2.0"
LEGACY_SCHEMA_VERSION = "1.1"
SCRIPT_VERSION = "PVM-standard-6.0.0"
DEFAULT_EMBEDDING_PREFIX = "トピック: "
DEFAULT_MAX_LEN = 8192
DEFAULT_BATCH = 8
DEFAULT_UNLOCK_Q = 0.95
DEFAULT_EXTRA_REL_ADV = 0.90
DEFAULT_EXTRA_RADIUS_MULT = 1.10
DEFAULT_UNLOCK_MIN_REL_GAIN = 0.15
DEFAULT_UNLOCK_MIN_SIL = 0.0
DEFAULT_BOUNDARY_MARGIN = 0.03

# unlock 1回あたりの base_threshold 更新幅の上限（相対値）。
# 偏ったバッチ1回で基準が大きく動いて baseline が壊れるのを防ぐ。
DEFAULT_UNLOCK_THRESHOLD_DRIFT = 0.20

DEFAULT_MIN_CLUSTER_SHARE = 0.01
DEFAULT_BASELINE_REVIEW_EXCEED_RATE = 0.18
DEFAULT_BASELINE_REVIEW_BOUNDARY_RATE = 0.28
DEFAULT_BASELINE_REVIEW_EXTRA_RATE = 0.15

# Column autodetection uses a simple heuristic: prefer long text-like columns,
# while penalizing URL-heavy columns and ID-like token columns.
AUTODETECT_SAMPLE_SIZE = 200
AUTODETECT_URL_PENALTY = 30.0
AUTODETECT_IDLIKE_PENALTY = 10.0


@dataclass(frozen=True)
class CandidateScoringConfig:
    sep_db_inv_weight: float = 0.45
    sep_sil_weight: float = 0.35
    sep_objective_weight: float = 0.20
    sep_sil_shift: float = 0.20
    sep_sil_scale: float = 1.20
    total_sep_weight: float = 0.36
    total_stability_weight: float = 0.30
    total_balance_weight: float = 0.18
    total_k_penalty_weight: float = 0.06
    total_fallback_penalty_weight: float = 0.10
    failed_gate_penalty: float = 0.10


@dataclass(frozen=True)
class UnlockScoringConfig:
    hard_min_ratio: float = 0.03
    min_cluster_share: float = DEFAULT_MIN_CLUSTER_SHARE
    min_rel_gain: float = DEFAULT_UNLOCK_MIN_REL_GAIN
    min_sil: float = DEFAULT_UNLOCK_MIN_SIL
    gain_scale: float = 0.35
    gain_weight: float = 0.55
    cohesion_weight: float = 0.25
    balance_weight: float = 0.15
    penalty_weight: float = 0.05


@dataclass(frozen=True)
class IcaRetryConfig:
    # 0 means "no additional cap" beyond the finite candidate grid below.
    max_attempts: int = 0
    max_seconds: float = 0.0
    max_dim_candidates: int = 10
    seed_offsets: Tuple[int, ...] = (0, 1, 2, 3, 4, 5)
    algorithms: Tuple[str, ...] = ("parallel", "deflation")
    configs: Tuple[Tuple[int, float], ...] = (
        (2000, 1e-4),
        (5000, 1e-4),
        (10000, 3e-4),
        (15000, 1e-3),
    )


CANDIDATE_SCORING = CandidateScoringConfig()
UNLOCK_SCORING = UnlockScoringConfig()
DEFAULT_ICA_RETRY = IcaRetryConfig()


def build_ica_retry_config(max_attempts: int = 0, max_seconds: float = 0.0) -> IcaRetryConfig:
    """CLI 指定を反映した ICA retry 設定を返す。

    グローバル状態は書き換えず、main() から探索/fit 系へ明示的に渡す。
    デフォルトでは PVM の標準 retry 方針をフルに使う。
    max_attempts / max_seconds を指定したときだけ、安全弁として途中で打ち切る。
    """
    return replace(
        DEFAULT_ICA_RETRY,
        max_attempts=max(0, int(max_attempts or 0)),
        max_seconds=max(0.0, float(max_seconds or 0.0)),
    )


def ica_retry_cache_key(config: IcaRetryConfig) -> Tuple[Any, ...]:
    """ICA retry 設定を cache key 化する。

    呼び出し側で DEFAULT_ICA_RETRY への解決を済ませる前提にして、
    None と明示 config の扱いを曖昧にしない。
    """
    return (
        int(config.max_attempts),
        float(config.max_seconds),
        int(config.max_dim_candidates),
        tuple(int(x) for x in config.seed_offsets),
        tuple(str(x) for x in config.algorithms),
        tuple((int(mi), float(tol)) for mi, tol in config.configs),
    )


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] [OCHIBI] %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("PVM")


log = logging.getLogger("PVM")


class PVMUserError(Exception):
    """ユーザー操作や入力状態に起因する、CLIで短く表示すべきエラー。"""


class BaselineSelectionError(PVMUserError):
    """baseline 自動選択が曖昧なときに投げる。"""


# ---------------------------------------------------------------------------
# utilities
# ---------------------------------------------------------------------------

def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def normalize_text(s: Any) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_embedding_prefix(value: Optional[str]) -> str:
    """CLI指定を実際に付与する embedding prefix に解決する。

    Ruri v3 の分類・クラスタリング用途では「トピック: 」が公式推奨なので、
    新規配布版ではこれを既定値にする。上級者が明示的に none/empty を
    指定した場合だけ空 prefix を許す。
    """
    if value is None:
        return DEFAULT_EMBEDDING_PREFIX
    raw = str(value)
    key = raw.strip().lower()
    if key in ("", "auto", "default", "topic", "topics"):
        return DEFAULT_EMBEDDING_PREFIX
    if raw.strip() in ("トピック:", "トピック："):
        return DEFAULT_EMBEDDING_PREFIX
    if key in ("none", "empty", "no", "off", "false"):
        return ""
    return raw


def validate_embedding_compat(meta_raw: Dict[str, Any], embedding_model: str, embedding_prefix: str, max_len: int) -> None:
    """baseline と現在実行の embedding 設定が一致するか検査する。

    prefix や max_len が違うと、同じモデル・同じ次元でも embedding 空間が
    変わり、lock/unlock の比較可能性が崩れるため停止する。
    """
    checks = [
        ("embedding_model", meta_raw.get("embedding_model"), embedding_model),
        ("embedding_prefix", meta_raw.get("embedding_prefix"), embedding_prefix),
        ("max_len", meta_raw.get("max_len"), int(max_len)),
    ]
    problems: List[str] = []
    for name, expected, current in checks:
        if expected is None:
            problems.append(f"baseline に {name} が記録されていません")
            continue
        if name == "max_len":
            try:
                ok = int(expected) == int(current)
            except (TypeError, ValueError):
                ok = False
        else:
            ok = str(expected) == str(current)
        if not ok:
            problems.append(f"{name}: baseline={expected!r} / current={current!r}")
    if problems:
        detail = "、".join(problems)
        legacy_hint = "v5.6以前のbaselineの場合は、v5.7では安全のため流用できません。初回実行でbaselineを再作成してください。"
        raise SystemExit(
            "baseline の embedding 設定と現在の設定が不一致です。"
            f"同じ基準で比較するには同じ設定で実行してください: {detail}。"
            f"{legacy_hint}"
        )


def l2_normalize(X: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    X = np.asarray(X, dtype=np.float32)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms = np.maximum(norms, eps)
    return X / norms


def cosine_distance_to_centroids(Xn: np.ndarray, Cn: np.ndarray) -> np.ndarray:
    sims = Xn @ Cn.T
    return 1.0 - sims


def autodetect_input() -> Tuple[Path, str]:
    here = Path(".")
    for name in ["入力.xlsx", "入力.csv"]:
        p = here / name
        if p.exists():
            return p.resolve(), p.suffix.lower().lstrip(".")
    cands = sorted(list(here.glob("*.xlsx")) + list(here.glob("*.csv")), key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        raise FileNotFoundError("入力ファイルが見つかりません（入力.xlsx / 入力.csv / *.xlsx / *.csv）")
    p = cands[0]
    return p.resolve(), p.suffix.lower().lstrip(".")


def read_table(path: Path, ext: str) -> pd.DataFrame:
    if ext == "xlsx":
        try:
            return pd.read_excel(path)
        except ImportError as e:
            raise RuntimeError("Excel読み込みには openpyxl が必要です。pip install openpyxl") from e
    try:
        return pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return pd.read_csv(path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            log.warning("UTF-8系で読めませんでした。CP932を試します。")
            return pd.read_csv(path, encoding="cp932")


def autodetect_columns(df: pd.DataFrame, text_col: Optional[str], id_col: Optional[str]) -> Tuple[str, Optional[str]]:
    cols = list(df.columns)
    lowers = {c.lower(): c for c in cols}
    if text_col is None:
        for cand in ["text", "テキスト", "本文", "content", "sentence", "comment", "body", "意味"]:
            if cand.lower() in lowers:
                text_col = lowers[cand.lower()]
                break
    if text_col is None:
        obj_cols = [c for c in cols if pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c])]
        if not obj_cols:
            raise ValueError("テキスト列の自動検出に失敗しました。--text_col で指定してください。")
        scored: List[Tuple[float, str]] = []
        for c in obj_cols:
            sample = df[c].astype(str).head(AUTODETECT_SAMPLE_SIZE)
            avg_len = float(sample.str.len().mean())
            url_rate = float(sample.str.contains(r"https?://", regex=True).mean())
            id_like_rate = float(sample.str.fullmatch(r"[0-9A-Za-z_\-]+", na=False).mean())
            score = avg_len - AUTODETECT_URL_PENALTY * url_rate - AUTODETECT_IDLIKE_PENALTY * id_like_rate
            scored.append((score, c))
        scored.sort(reverse=True)
        text_col = scored[0][1]
    if text_col not in df.columns:
        raise ValueError("テキスト列の自動検出に失敗しました。--text_col で指定してください。")
    if id_col is not None and id_col not in df.columns:
        id_col = None
    return text_col, id_col


def get_project_name(arg_project: Optional[str], infile: Path) -> str:
    if arg_project:
        return arg_project
    base = infile.stem or "PVM"
    return re.sub(r"[^0-9A-Za-z一-龥ぁ-んァ-ン_-]+", "", base)


# ---------------------------------------------------------------------------
# embedding
# ---------------------------------------------------------------------------

def ensure_ruri() -> None:
    try:
        import torch  # noqa
        from transformers import AutoTokenizer, AutoModel  # noqa
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"Ruri埋め込みには torch / transformers が必要です。詳細: {e}")


def compute_embeddings(texts: Sequence[str], model_name: str, batch: int, max_len: int, embedding_prefix: str = DEFAULT_EMBEDDING_PREFIX) -> Tuple[np.ndarray, str]:
    import torch
    from transformers import AutoTokenizer, AutoModel

    if torch.cuda.is_available():
        device = "cuda"
    elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
    log.info("埋め込みモデルを読み込み中: %s", model_name)
    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name).to(device).eval()

    reps: List[np.ndarray] = []
    truncated_total = 0
    show_bar = sys.stdout.isatty()
    total_docs = len(texts)
    log.info("Embedding開始: total=%d, batch=%d, max_len=%d, prefix=%r, device=%s", total_docs, batch, max_len, embedding_prefix, device)
    progress = tqdm(total=total_docs, desc="Embedding", unit="doc", disable=not show_bar)
    try:
        with torch.inference_mode():
            for i in range(0, total_docs, batch):
                raw_batch_texts = list(texts[i:i + batch])
                batch_texts = [f"{embedding_prefix}{t}" if embedding_prefix else str(t) for t in raw_batch_texts]
                enc = tok(
                    batch_texts,
                    padding=True,
                    truncation=True,
                    max_length=max_len,
                    return_tensors="pt",
                    return_length=True,
                )
                if "length" in enc:
                    try:
                        truncated_total += int((enc["length"] >= max_len).sum().item())
                    except Exception:
                        pass
                model_inputs = {k: v.to(device) for k, v in enc.items() if k in ("input_ids", "attention_mask", "token_type_ids")}
                out = model(**model_inputs).last_hidden_state
                mask = model_inputs["attention_mask"].unsqueeze(-1)
                emb = (out * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-6)
                reps.append(emb.detach().cpu().numpy().astype(np.float32))
                progress.update(len(raw_batch_texts))
    finally:
        progress.close()
    if truncated_total > 0:
        log.warning("Tokenizerで %d 文が max_len=%d 付近で切り詰められた可能性があります。", truncated_total, max_len)
    X = np.vstack(reps).astype(np.float32)
    log.info("埋め込み完了（%d文, device=%s）", total_docs, device)
    return X, device

def _fastica_safe(
    n_components: int,
    random_state: int,
    algorithm: str = "parallel",
    max_iter: int = 5000,
    tol: float = 1e-4,
) -> FastICA:
    try:
        return FastICA(
            n_components=n_components,
            whiten="unit-variance",
            algorithm=algorithm,
            max_iter=max_iter,
            tol=tol,
            random_state=random_state,
        )
    except TypeError:  # pragma: no cover
        return FastICA(
            n_components=n_components,
            whiten=True,
            algorithm=algorithm,
            max_iter=max_iter,
            tol=tol,
            random_state=random_state,
        )



@dataclass
class TransformBundle:
    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    pca_components: np.ndarray
    pca_mean: np.ndarray
    pca_n_components: int
    ica1_components: np.ndarray
    ica1_mean: np.ndarray
    ica1_n_components: int
    ica2_components: np.ndarray
    ica2_mean: np.ndarray
    ica2_n_components: int
    embed_dim: int
    transform_mode: str = "full_original_pvm"
    ica1_status: str = "converged"
    ica2_status: str = "converged"
    final_n_components: int = 0
    fallback_level: int = 0



@dataclass
class BaselineMeta:
    project: str
    mode: str
    embedding_model: str
    embedding_prefix: str
    max_len: int
    pca_var: float
    random_state: int
    protected_cluster_count: int
    base_threshold: float
    extra_accept_thresholds: List[float]
    base_distance_quantiles: Dict[str, float]
    unlock_q: float
    extra_relative_advantage: float
    extra_radius_multiplier: float
    created_at: str
    script_version: str
    environment: Dict[str, Any]
    chosen_plan: Optional[Dict[str, Any]] = None
    source_baseline: Optional[str] = None
    transform_mode: str = "full_original_pvm"
    ica1_status: str = "na"
    ica2_status: str = "na"
    fallback_level: int = 0
    quality_gate_passed: bool = True
    quality_gate_status: str = "pass"
    quality_summary: str = ""
    quality_metrics: Optional[Dict[str, Any]] = None
    retry_count: int = 0



def propose_ica1_dims_from_pca_base(pca_base: Dict[str, Any], pca_var: float) -> List[int]:
    """get_pca_base() の PCA 結果を再利用して ICA① 候補次元を作る。

    候補提案のためだけに StandardScaler + PCA を二重 fit しない。
    """
    explained = np.asarray(pca_base.get("explained_variance_ratio", []), dtype=np.float64)
    max_pcs = int(pca_base.get("n_pcs", len(explained)))
    max_pcs = max(2, min(max_pcs, int(len(explained)))) if len(explained) else 2
    cumulative = np.cumsum(explained[:max_pcs]) if len(explained) else np.array([1.0, 1.0])
    dims = set()
    for v in [0.70, 0.80, 0.90, 0.95, pca_var]:
        n = int(np.searchsorted(cumulative, float(v)) + 1)
        dims.add(max(2, min(n, max_pcs)))
    for d in [2, 4, 8, 16, 24, 32]:
        if d <= max_pcs:
            dims.add(d)
    dims = sorted(dims)
    if len(dims) > 6:
        idx = np.linspace(0, len(dims) - 1, 6).round().astype(int)
        dims = [dims[i] for i in idx]
    return sorted(set(dims))


def _fit_ica_with_retries(
    X: np.ndarray,
    n_components: int,
    random_state: int,
    stage_name: str,
    ica_retry_config: Optional[IcaRetryConfig] = None,
) -> Dict[str, Any]:
    retry_cfg = ica_retry_config or DEFAULT_ICA_RETRY
    min_components = 1 if stage_name == "ICA②" else 2
    start_components = max(min_components, int(n_components))
    component_candidates = list(range(start_components, min_components - 1, -1))
    component_candidates = component_candidates[: max(4, min(retry_cfg.max_dim_candidates, len(component_candidates)))]
    seed_candidates = [int(random_state + i) for i in retry_cfg.seed_offsets]
    algo_candidates = list(retry_cfg.algorithms)
    config_candidates = list(retry_cfg.configs)

    last_error: Optional[BaseException] = None
    attempts = 0
    seen = set()
    started = time.monotonic()

    for comps in component_candidates:
        for algo in algo_candidates:
            for max_iter, tol in config_candidates:
                for seed in seed_candidates:
                    if retry_cfg.max_attempts and attempts >= retry_cfg.max_attempts:
                        raise RuntimeError(f"{stage_name} retry上限に到達しました: attempts={attempts}")
                    if retry_cfg.max_seconds and (time.monotonic() - started) >= retry_cfg.max_seconds:
                        raise RuntimeError(f"{stage_name} retry時間上限に到達しました: seconds={retry_cfg.max_seconds:g}, attempts={attempts}")
                    key = (comps, algo, max_iter, tol, seed)
                    if key in seen:
                        continue
                    seen.add(key)
                    attempts += 1
                    ica = _fastica_safe(comps, seed, algorithm=algo, max_iter=max_iter, tol=tol)
                    try:
                        with warnings.catch_warnings(record=True) as captured:
                            warnings.simplefilter("always", ConvergenceWarning)
                            S = ica.fit_transform(X)
                        warned = any(issubclass(w.category, ConvergenceWarning) for w in captured)
                        finite_ok = np.isfinite(S).all() and np.isfinite(getattr(ica, "components_", np.array([]))).all()
                        if warned or (not finite_ok):
                            why = "convergence warning" if warned else "non-finite values"
                            last_error = RuntimeError(f"{stage_name} {why}")
                            log.debug(
                                "%s retry: n_components=%d algo=%s max_iter=%d tol=%g seed=%d reason=%s",
                                stage_name, comps, algo, max_iter, tol, seed, why,
                            )
                            continue
                        return {
                            "ica": ica,
                            "S": S.astype(np.float32),
                            "n_components": int(comps),
                            "status": "converged",
                            "retry_count": int(attempts - 1),
                            "attempts": int(attempts),
                            "algo": str(algo),
                            "max_iter": int(max_iter),
                            "tol": float(tol),
                            "seed": int(seed),
                            "retry_policy": {
                                "max_attempts": int(retry_cfg.max_attempts),
                                "max_seconds": float(retry_cfg.max_seconds),
                                "max_dim_candidates": int(retry_cfg.max_dim_candidates),
                            },
                        }
                    except (ValueError, RuntimeError, FloatingPointError, LinAlgError) as e:
                        last_error = e
                        log.debug(
                            "%s fit retry: n_components=%d algo=%s max_iter=%d tol=%g seed=%d err=%s",
                            stage_name, comps, algo, max_iter, tol, seed, e,
                        )
                        continue
    raise RuntimeError(f"{stage_name} の学習に失敗しました: {last_error}")


def get_pca_base(
    X: np.ndarray,
    pca_var: float,
    random_state: int,
    cache: Dict[Any, Any],
) -> Dict[str, Any]:
    key = ("pca_base", round(float(pca_var), 6), int(random_state))
    if key in cache:
        return cache[key]

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    max_pcs = max(2, min(Xs.shape[0] - 1, Xs.shape[1]))
    pca = PCA(n_components=max_pcs, random_state=random_state)
    Xp = pca.fit_transform(Xs)
    n_pcs = int(np.searchsorted(np.cumsum(pca.explained_variance_ratio_), pca_var) + 1)
    n_pcs = max(2, min(n_pcs, Xp.shape[1]))
    pca_bundle_base = dict(
        scaler_mean=np.asarray(scaler.mean_, dtype=np.float32),
        scaler_scale=np.asarray(scaler.scale_, dtype=np.float32),
        pca_components=np.asarray(pca.components_, dtype=np.float32),
        pca_mean=np.asarray(pca.mean_, dtype=np.float32),
        pca_n_components=int(n_pcs),
        embed_dim=int(X.shape[1]),
    )
    out = {
        "Xp": Xp[:, :n_pcs].astype(np.float32),
        "n_pcs": int(n_pcs),
        "pca_bundle_base": pca_bundle_base,
        "explained_variance_ratio": np.asarray(pca.explained_variance_ratio_, dtype=np.float32),
    }
    cache[key] = out
    return out


def get_stage1_result(
    X: np.ndarray,
    pca_var: float,
    ica1_dim: int,
    random_state: int,
    cache: Dict[Any, Any],
    ica_retry_config: Optional[IcaRetryConfig] = None,
) -> Dict[str, Any]:
    key = ("stage1", round(float(pca_var), 6), int(ica1_dim), int(random_state), ica_retry_cache_key(ica_retry_config or DEFAULT_ICA_RETRY))
    if key in cache:
        return cache[key]

    pca_base = get_pca_base(X, pca_var=pca_var, random_state=random_state, cache=cache)
    Xp = pca_base["Xp"]
    n_pcs = int(pca_base["n_pcs"])
    pca_bundle_base = pca_base["pca_bundle_base"]

    d1 = max(2, min(int(ica1_dim), n_pcs))
    try:
        ica1_res = _fit_ica_with_retries(Xp, d1, random_state, "ICA①", ica_retry_config)
        ica1 = ica1_res["ica"]
        Xi1 = ica1_res["S"]
        d1 = int(ica1_res["n_components"])
        out = {
            "pca_bundle_base": pca_bundle_base,
            "n_pcs": int(n_pcs),
            "Xp": Xp,
            "ica1_success": True,
            "ica1": ica1,
            "Xi1": Xi1.astype(np.float32),
            "d1": int(d1),
            "ica1_status": "converged",
            "ica1_retry_count": int(ica1_res["retry_count"]),
            "ica1_attempts": int(ica1_res.get("attempts", 1)),
            "ica1_algo": str(ica1_res.get("algo", "")),
            "ica1_max_iter": int(ica1_res.get("max_iter", 0)),
            "ica1_tol": float(ica1_res.get("tol", 0.0)),
            "ica1_seed": int(ica1_res.get("seed", random_state)),
            "ica1_error": None,
        }
    except RuntimeError as e1:
        out = {
            "pca_bundle_base": pca_bundle_base,
            "n_pcs": int(n_pcs),
            "Xp": Xp,
            "ica1_success": False,
            "ica1": None,
            "Xi1": None,
            "d1": 0,
            "ica1_status": "failed",
            "ica1_retry_count": 0,
            "ica1_attempts": 0,
            "ica1_algo": "",
            "ica1_max_iter": 0,
            "ica1_tol": 0.0,
            "ica1_seed": int(random_state),
            "ica1_error": str(e1),
        }
    cache[key] = out
    return out


def between_class_projection(
    Xi1: np.ndarray,
    labels: np.ndarray,
    svd_tol_ratio: float = 1e-6,
) -> Tuple[np.ndarray, np.ndarray, int, np.ndarray]:
    """クラスタ①の重心が張る between-class 方向を返す。

    Full / Original PVM の中核:
        PCA -> ICA① -> cluster① -> centroid projection -> cluster②

    ここでは「セントロイドに ICA をかける」のではなく、
    クラスタ①の重心差が張る判別的な線形部分空間を SVD で作る。

    数式:
        μ   = Xi1 全体平均
        μ_c = cluster c の平均
        M_c = sqrt(n_c) * (μ_c - μ)
        M^T M は between-class scatter に対応する。
        M の右特異ベクトル Vt がクラスタ間方向 W。

    rank は最大 k-1。これは k 個の重心が中心化後に張れる空間の上限。
    """
    Xi1 = np.asarray(Xi1, dtype=np.float32)
    labels = np.asarray(labels, dtype=int)

    if Xi1.ndim != 2:
        raise RuntimeError("centroid projection failed: Xi1 must be 2D.")
    if len(Xi1) != len(labels):
        raise RuntimeError("centroid projection failed: Xi1 and labels length mismatch.")

    classes = np.unique(labels)
    if len(classes) < 2:
        raise RuntimeError("centroid projection failed: fewer than 2 non-empty clusters.")

    mu = Xi1.mean(axis=0).astype(np.float64)
    rows: List[np.ndarray] = []
    for c in classes:
        idx = labels == c
        n_c = int(idx.sum())
        if n_c <= 0:
            continue
        mu_c = Xi1[idx].mean(axis=0).astype(np.float64)
        rows.append(np.sqrt(n_c) * (mu_c - mu))

    if len(rows) < 2:
        raise RuntimeError("centroid projection failed: fewer than 2 centroid rows.")

    M = np.vstack(rows)
    try:
        _, s, Vt = np.linalg.svd(M, full_matrices=False)
    except LinAlgError as e:
        raise RuntimeError(f"centroid projection SVD failed: {e}") from e

    if s.size == 0 or (not np.isfinite(s).all()) or float(s[0]) <= 1e-12:
        raise RuntimeError("centroid projection failed: degenerate between-class variance.")

    tol = float(svd_tol_ratio) * float(s[0])
    rank = int(np.sum(s > tol))
    rank = min(rank, len(classes) - 1, Xi1.shape[1])
    if rank < 1:
        raise RuntimeError("centroid projection failed: rank is zero.")

    W = Vt[:rank].astype(np.float32)
    return W, mu.astype(np.float32), int(rank), s.astype(np.float32)

def fit_transforms(
    X: np.ndarray,
    pca_var: float,
    ica1_dim: int,
    k: int,
    random_state: int,
    cache: Optional[Dict[Any, Any]] = None,
    ica_retry_config: Optional[IcaRetryConfig] = None
) -> Tuple[TransformBundle, np.ndarray, Dict[str, Any]]:
    """PVM変換をfitし、最終クラスタリング用の Xfinal を返す。

    PVM Standard 6.0.0:
        新規baselineの正式ルートは Full / Original PVM。

            PCA
            -> ICA①
            -> cluster① in ICA① space
            -> centroid projection / between-class projection
            -> Xfinal
            -> cluster② は get_pipeline_result() 側の spherical_kmeans で実行

    旧 full_pvm = 全文書ICA②(k-1) はこの版ではサポートしない。
    この版以降は Full / Original PVM をPVMの正式仕様として扱う。
    """
    cache = cache if cache is not None else {}
    stage1 = get_stage1_result(
        X,
        pca_var=pca_var,
        ica1_dim=ica1_dim,
        random_state=random_state,
        cache=cache,
        ica_retry_config=ica_retry_config,
    )
    pca_bundle_base = stage1["pca_bundle_base"]
    n_pcs = int(stage1["n_pcs"])
    Xp = stage1["Xp"]
    pca_final_dim = max(2, min(max(int(ica1_dim), int(k - 1), 2), n_pcs))

    if stage1["ica1_success"]:
        ica1 = stage1["ica1"]
        Xi1 = stage1["Xi1"]
        d1 = int(stage1["d1"])
        try:
            # Cluster①: ICA①空間で暫定クラスタを作る。
            # get_pipeline_result() 側で cluster② を行うため、ここでは Xfinal の作成に使う。
            cluster1 = spherical_kmeans(Xi1, k=k, random_state=random_state)
            lab1 = cluster1["labels"]

            # Centroid Projection:
            # クラスタ①の重心差が張る between-class 方向を取り出す。
            W, mu, rank, singular_values = between_class_projection(Xi1, lab1)
            Xfinal = ((Xi1 - mu) @ W.T).astype(np.float32)

            bundle = TransformBundle(
                **pca_bundle_base,
                ica1_components=np.asarray(ica1.components_, dtype=np.float32),
                ica1_mean=np.asarray(getattr(ica1, "mean_", np.zeros(n_pcs)), dtype=np.float32),
                ica1_n_components=int(d1),
                # 既存の TransformBundle 構造を大きく変えないため、ica2 スロットを射影用に流用する。
                # 中身は ICA② ではなく centroid projection の W, μ。
                ica2_components=np.asarray(W, dtype=np.float32),
                ica2_mean=np.asarray(mu, dtype=np.float32),
                ica2_n_components=int(rank),
                transform_mode="full_original_pvm",
                ica1_status="converged",
                ica2_status="centroid_projection",
                final_n_components=int(rank),
                fallback_level=0,
            )
            info = {
                "transform_mode": "full_original_pvm",
                "projection_type": "centroid_projection",
                "ica1_status": "converged",
                "ica2_status": "centroid_projection",
                "retry_count": int(stage1["ica1_retry_count"]),
                "fallback_level": 0,
                "final_dims": {"pca": int(n_pcs), "ica1": int(d1), "ica2": int(rank)},
                "quality_gate_passed": True,
                "ica1_attempts": int(stage1.get("ica1_attempts", 1)),
                "ica2_attempts": 0,
                "ica1_setup": {
                    "algo": stage1.get("ica1_algo"),
                    "max_iter": int(stage1.get("ica1_max_iter", 0)),
                    "tol": float(stage1.get("ica1_tol", 0.0)),
                    "seed": int(stage1.get("ica1_seed", random_state)),
                },
                "ica2_setup": {
                    "method": "between_class_projection",
                    "rank": int(rank),
                    "max_rank": int(max(1, min(k - 1, d1))),
                    "cluster1_objective": float(cluster1.get("objective", 0.0)),
                    "cluster1_k": int(k),
                    "singular_values": [float(x) for x in singular_values[: min(len(singular_values), 20)]],
                },
            }
            return bundle, Xfinal.astype(np.float32), info
        except RuntimeError as e2:
            # Centroid Projection が退化した場合は、ICA①空間そのものにfallbackする。
            bundle = TransformBundle(
                **pca_bundle_base,
                ica1_components=np.asarray(ica1.components_, dtype=np.float32),
                ica1_mean=np.asarray(getattr(ica1, "mean_", np.zeros(n_pcs)), dtype=np.float32),
                ica1_n_components=int(d1),
                ica2_components=np.zeros((0, 0), dtype=np.float32),
                ica2_mean=np.zeros((0,), dtype=np.float32),
                ica2_n_components=0,
                transform_mode="ica1_only_pvm",
                ica1_status="converged",
                ica2_status="centroid_projection_failed",
                final_n_components=int(d1),
                fallback_level=1,
            )
            info = {
                "transform_mode": "ica1_only_pvm",
                "projection_type": "none",
                "ica1_status": "converged",
                "ica2_status": "centroid_projection_failed",
                "retry_count": int(stage1["ica1_retry_count"]),
                "fallback_level": 1,
                "final_dims": {"pca": int(n_pcs), "ica1": int(d1), "ica2": 0},
                "quality_gate_passed": True,
                "ica1_attempts": int(stage1.get("ica1_attempts", 1)),
                "ica2_attempts": 0,
                "ica1_setup": {
                    "algo": stage1.get("ica1_algo"),
                    "max_iter": int(stage1.get("ica1_max_iter", 0)),
                    "tol": float(stage1.get("ica1_tol", 0.0)),
                    "seed": int(stage1.get("ica1_seed", random_state)),
                },
                "ica2_setup": None,
                "fallback_reason": str(e2),
            }
            return bundle, Xi1.astype(np.float32), info

    Xfinal = Xp[:, :pca_final_dim].astype(np.float32)
    bundle = TransformBundle(
        **pca_bundle_base,
        ica1_components=np.zeros((0, 0), dtype=np.float32),
        ica1_mean=np.zeros((0,), dtype=np.float32),
        ica1_n_components=0,
        ica2_components=np.zeros((0, 0), dtype=np.float32),
        ica2_mean=np.zeros((0,), dtype=np.float32),
        ica2_n_components=0,
        transform_mode="pca_pvm",
        ica1_status="failed",
        ica2_status="skipped",
        final_n_components=int(pca_final_dim),
        fallback_level=2,
    )
    info = {
        "transform_mode": "pca_pvm",
        "projection_type": "none",
        "ica1_status": "failed",
        "ica2_status": "skipped",
        "retry_count": 0,
        "fallback_level": 2,
        "final_dims": {"pca": int(pca_final_dim), "ica1": 0, "ica2": 0},
        "quality_gate_passed": True,
        "ica1_attempts": 0,
        "ica2_attempts": 0,
        "ica1_setup": None,
        "ica2_setup": None,
        "fallback_reason": str(stage1.get("ica1_error", "ICA① unavailable")),
    }
    return bundle, Xfinal, info


def apply_transforms(X: np.ndarray, bundle: TransformBundle) -> np.ndarray:
    if X.shape[1] != bundle.embed_dim:
        raise ValueError("埋め込み次元が baseline と不一致です。embedding model や前処理を確認してください。")
    safe_scale = np.where(bundle.scaler_scale == 0.0, 1.0, bundle.scaler_scale)
    Xs = (X - bundle.scaler_mean) / safe_scale
    Xp_full = (Xs - bundle.pca_mean) @ bundle.pca_components.T
    Xp = Xp_full[:, : bundle.pca_n_components]

    if bundle.transform_mode == "full_original_pvm":
        Xi1 = (Xp - bundle.ica1_mean) @ bundle.ica1_components.T
        Xi2 = (Xi1 - bundle.ica2_mean) @ bundle.ica2_components.T
        return Xi2[:, : bundle.final_n_components].astype(np.float32)

    if bundle.transform_mode == "ica1_only_pvm":
        Xi1 = (Xp - bundle.ica1_mean) @ bundle.ica1_components.T
        return Xi1[:, : bundle.final_n_components].astype(np.float32)

    if bundle.transform_mode == "pca_pvm":
        return Xp[:, : bundle.final_n_components].astype(np.float32)

    raise ValueError(f"未知の transform_mode です: {bundle.transform_mode}")


# ---------------------------------------------------------------------------
# clustering
# ---------------------------------------------------------------------------


def _pick_kmeanspp_rows(Xn: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    n = len(Xn)
    if k > n:
        raise ValueError("k がデータ件数を超えています。")
    centers = np.empty((k, Xn.shape[1]), dtype=np.float32)
    first = int(rng.integers(0, n))
    chosen_rows: List[int] = [first]
    centers[0] = Xn[first]
    min_dist = 1.0 - (Xn @ centers[0].T)
    min_dist = np.maximum(min_dist, 0.0)

    for i in range(1, k):
        probs = np.square(min_dist)
        total = float(probs.sum())
        if total <= 1e-12:
            # 縮退ケース（ほぼ全点が既存センターと一致）。
            # まだセンターに選ばれていない行から無作為に選ぶ。
            remaining = np.setdiff1d(np.arange(n), np.asarray(chosen_rows, dtype=int), assume_unique=False)
            if len(remaining) == 0:
                idx = int(rng.integers(0, n))
            else:
                idx = int(rng.choice(remaining))
        else:
            probs = probs / total
            idx = int(rng.choice(n, p=probs))
        chosen_rows.append(idx)
        centers[i] = Xn[idx]
        dist_i = 1.0 - (Xn @ centers[i].T)
        dist_i = np.maximum(dist_i, 0.0)
        min_dist = np.minimum(min_dist, dist_i)
    return centers.astype(np.float32)


def spherical_kmeans(X: np.ndarray, k: int, random_state: int, max_iter: int = 100) -> Dict[str, Any]:
    if len(X) < k:
        raise ValueError("k がデータ件数以上です。")
    Xn = l2_normalize(X)
    rng = np.random.default_rng(random_state)
    C = _pick_kmeanspp_rows(Xn, k, rng)
    prev_labels = None

    for _ in range(max_iter):
        sims = Xn @ C.T
        labels = sims.argmax(axis=1)

        C_new = np.zeros_like(C)
        for j in range(k):
            mask = labels == j
            if not np.any(mask):
                far_idx = int(np.argmin(np.max(sims, axis=1)))
                C_new[j] = Xn[far_idx]
            else:
                c = Xn[mask].mean(axis=0, dtype=np.float64)
                norm = np.linalg.norm(c)
                C_new[j] = C[j] if norm <= 1e-12 else (c / norm).astype(np.float32)

        if prev_labels is not None and np.array_equal(labels, prev_labels):
            C = C_new
            break
        if np.max(np.abs(C_new - C)) < 1e-6:
            C = C_new
            break
        C = C_new
        prev_labels = labels.copy()

    sims = Xn @ C.T
    labels = sims.argmax(axis=1)
    dists = 1.0 - sims[np.arange(len(Xn)), labels]
    objective = float(np.mean(sims[np.arange(len(Xn)), labels]))
    return {
        "labels": labels.astype(int),
        "centroids": C.astype(np.float32),
        "dists": dists.astype(np.float32),
        "objective": objective,
        "Xn": Xn,
    }


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------
@dataclass
class CandidateResult:
    rank: int
    ica1_dim: int
    k: int
    ic2_dim: int
    sil: float
    ch: float
    db: float
    db_inv: float
    entropy_balance: float
    stability: float
    k_penalty: float
    fallback_penalty: float
    total_score: float
    objective: float
    random_state: int
    transform_mode: str
    ica1_status: str
    ica2_status: str
    fallback_level: int
    retry_count: int
    quality_gate_passed: bool



@dataclass
class UnlockChoice:
    k: int
    total_score: float
    relative_gain: float
    objective: float
    balance: float
    sil: float
    db_inv: float
    min_count: int
    labels: Optional[np.ndarray]
    centroids: Optional[np.ndarray]
    dists: Optional[np.ndarray]
    accept_thresholds: Optional[List[float]]
    reason: str


def calc_internal_metrics(Xn: np.ndarray, labels: np.ndarray) -> Dict[str, float]:
    out: Dict[str, float] = {}
    try:
        out["sil"] = float(silhouette_score(Xn, labels, metric="cosine"))
    except ValueError:
        out["sil"] = -1.0
    try:
        out["ch"] = float(calinski_harabasz_score(Xn, labels))
    except ValueError:
        out["ch"] = 0.0
    try:
        db = float(davies_bouldin_score(Xn, labels))
        out["db"] = db
        out["db_inv"] = 1.0 / (1.0 + db)
    except ValueError:
        out["db"] = 10.0
        out["db_inv"] = 0.0
    return out


def entropy_balance(labels: np.ndarray, k: int) -> float:
    counts = np.bincount(labels, minlength=k).astype(np.float64)
    probs = counts / max(counts.sum(), 1.0)
    probs = probs[probs > 0]
    if len(probs) == 0 or k <= 1:
        return 0.0
    ent = float(-(probs * np.log(probs)).sum())
    return ent / math.log(k)


def k_penalty_value(k: int, n: int) -> float:
    target = max(3.0, min(12.0, math.sqrt(max(n, 1))))
    if k <= target:
        return 0.0
    return min(1.0, (k - target) / max(target, 1.0))


def relative_scale(values: List[float], higher_is_better: bool = True) -> List[float]:
    arr = np.array(values, dtype=np.float64)
    if not higher_is_better:
        arr = -arr
    vmin, vmax = float(arr.min()), float(arr.max())
    if abs(vmax - vmin) < 1e-12:
        return [0.5] * len(values)
    scaled = (arr - vmin) / (vmax - vmin)
    return [float(x) for x in scaled]


def seed_triplet(base_seed: int) -> List[int]:
    return [int(base_seed), int(base_seed + 10), int(base_seed + 20)]


def get_pipeline_result(
    X: np.ndarray,
    pca_var: float,
    ica1_dim: int,
    k: int,
    random_state: int,
    cache: Dict[Any, Any],
    ica_retry_config: Optional[IcaRetryConfig] = None,
    keep_arrays: bool = True,
) -> Dict[str, Any]:
    """変換+クラスタリングの結果を返す。

    keep_arrays=False のときは安定性評価などラベルしか使わない用途向けに、
    重い配列(Xfinal/Xn/centroids)と内部指標(silhouette等)の計算・保持を省く。
    フル結果が既にキャッシュされていればそれを再利用する。
    """
    key = (
        "pipeline",
        round(float(pca_var), 6),
        int(ica1_dim),
        int(k),
        int(random_state),
        ica_retry_cache_key(ica_retry_config or DEFAULT_ICA_RETRY),
    )
    if key in cache:
        return cache[key]
    light_key = ("pipeline_light",) + key[1:]
    if not keep_arrays and light_key in cache:
        return cache[light_key]

    bundle, Xfinal, tinfo = fit_transforms(
        X,
        pca_var=pca_var,
        ica1_dim=ica1_dim,
        k=k,
        random_state=random_state,
        cache=cache,
        ica_retry_config=ica_retry_config,
    )
    cluster = spherical_kmeans(Xfinal, k=k, random_state=random_state)

    if not keep_arrays:
        result = {
            "labels": cluster["labels"],
            "objective": cluster["objective"],
            "transform_mode": bundle.transform_mode,
        }
        cache[light_key] = result
        return result

    metrics = calc_internal_metrics(cluster["Xn"], cluster["labels"])
    result = {
        "bundle": bundle,
        "Xfinal": Xfinal,
        "Xn": cluster["Xn"],
        "labels": cluster["labels"],
        "centroids": cluster["centroids"],
        "dists": cluster["dists"],
        "objective": cluster["objective"],
        "transform_mode": bundle.transform_mode,
        "ica1_status": bundle.ica1_status,
        "ica2_status": bundle.ica2_status,
        "fallback_level": int(bundle.fallback_level),
        "retry_count": int(tinfo.get("retry_count", 0)),
        "quality_gate_passed": bool(tinfo.get("quality_gate_passed", True)),
        "final_dims": tinfo.get("final_dims", {}),
        "transform_info": tinfo,
        **metrics,
    }
    cache[key] = result
    return result


def candidate_stability(
    X: np.ndarray,
    pca_var: float,
    ica1_dim: int,
    k: int,
    base_seed: int,
    cache: Dict[Any, Any],
    ica_retry_config: Optional[IcaRetryConfig] = None,
) -> float:
    label_runs = []
    for seed in seed_triplet(base_seed):
        # 安定性評価ではラベルしか使わないため軽量モードで取得する。
        # base seed のフル結果がキャッシュ済みならそれが再利用される。
        res = get_pipeline_result(
            X,
            pca_var=pca_var,
            ica1_dim=ica1_dim,
            k=k,
            random_state=seed,
            cache=cache,
            ica_retry_config=ica_retry_config,
            keep_arrays=False,
        )
        label_runs.append(res["labels"])
    aris = []
    for i in range(len(label_runs)):
        for j in range(i + 1, len(label_runs)):
            aris.append(float(adjusted_rand_score(label_runs[i], label_runs[j])))
    return float(np.mean(aris)) if aris else 1.0


def fallback_penalty_value(transform_mode: str) -> float:
    if transform_mode == "full_original_pvm":
        return 0.0
    if transform_mode == "ica1_only_pvm":
        return 0.5
    return 1.0


def explore_candidates(
    X: np.ndarray,
    k_min: int,
    k_max: int,
    pca_var: float,
    random_state: int,
    cache: Dict[Any, Any],
    ica_retry_config: Optional[IcaRetryConfig] = None,
) -> List[CandidateResult]:
    n = len(X)
    if n < 3:
        raise ValueError("データが3件未満です。最低3件以上必要です。")

    pca_base = get_pca_base(X, pca_var=pca_var, random_state=random_state, cache=cache)
    dims = propose_ica1_dims_from_pca_base(pca_base, pca_var)
    k_hi = min(int(k_max), n - 1)
    k_lo = max(2, int(k_min))
    raw_rows: List[Dict[str, Any]] = []
    total_plans = max(0, len(dims) * max(0, k_hi - k_lo + 1))
    show_bar = sys.stdout.isatty()
    log.info("候補探索を開始します: dims=%s, k=%d..%d, plans=%d", dims, k_lo, k_hi, total_plans)
    progress = tqdm(total=total_plans, desc="Candidates", unit="plan", disable=not show_bar)

    try:
        for d in dims:
            for k in range(k_lo, k_hi + 1):
                res = get_pipeline_result(X, pca_var=pca_var, ica1_dim=d, k=k, random_state=random_state, cache=cache, ica_retry_config=ica_retry_config)
                bal = entropy_balance(res["labels"], k)
                stab = candidate_stability(X, pca_var=pca_var, ica1_dim=d, k=k, base_seed=random_state, cache=cache, ica_retry_config=ica_retry_config)
                counts = np.bincount(res["labels"], minlength=k)
                min_count = int(counts.min()) if len(counts) else 0
                gate_pass = bool(
                    res["sil"] > -0.05
                    and min_count >= max(3, int(math.ceil(DEFAULT_MIN_CLUSTER_SHARE * max(n, 1))))
                )
                raw_rows.append(
                    {
                        "ica1_dim": int(d),
                        "k": int(k),
                        "ic2_dim": int(res["bundle"].final_n_components),
                        "sil": float(res["sil"]),
                        "ch": float(res["ch"]),
                        "db": float(res["db"]),
                        "db_inv": float(res["db_inv"]),
                        "objective": float(res["objective"]),
                        "entropy_balance": float(bal),
                        "stability": float(stab),
                        "k_penalty": float(k_penalty_value(k, n)),
                        "fallback_penalty": float(fallback_penalty_value(str(res["transform_mode"]))),
                        "transform_mode": str(res["transform_mode"]),
                        "ica1_status": str(res["ica1_status"]),
                        "ica2_status": str(res["ica2_status"]),
                        "fallback_level": int(res["fallback_level"]),
                        "retry_count": int(res["retry_count"]),
                        "quality_gate_passed": gate_pass,
                    }
                )
                progress.update(1)
    finally:
        progress.close()

    if not raw_rows:
        raise RuntimeError("有効な候補が見つかりませんでした。")

    sep_base = [
        CANDIDATE_SCORING.sep_db_inv_weight * r["db_inv"]
        + CANDIDATE_SCORING.sep_sil_weight
        * max(0.0, min(1.0, (r["sil"] + CANDIDATE_SCORING.sep_sil_shift) / CANDIDATE_SCORING.sep_sil_scale))
        + CANDIDATE_SCORING.sep_objective_weight * max(0.0, min(1.0, r["objective"]))
        for r in raw_rows
    ]
    sep_scaled = relative_scale(sep_base, higher_is_better=True)
    bal_scaled = relative_scale([r["entropy_balance"] for r in raw_rows], higher_is_better=True)
    stab_scaled = relative_scale([r["stability"] for r in raw_rows], higher_is_better=True)
    pen_scaled = relative_scale([r["k_penalty"] for r in raw_rows], higher_is_better=False)
    fb_scaled = relative_scale([r["fallback_penalty"] for r in raw_rows], higher_is_better=False)

    results: List[CandidateResult] = []
    for i, row in enumerate(raw_rows):
        total = (
            CANDIDATE_SCORING.total_sep_weight * sep_scaled[i]
            + CANDIDATE_SCORING.total_stability_weight * stab_scaled[i]
            + CANDIDATE_SCORING.total_balance_weight * bal_scaled[i]
            + CANDIDATE_SCORING.total_k_penalty_weight * pen_scaled[i]
            + CANDIDATE_SCORING.total_fallback_penalty_weight * fb_scaled[i]
        )
        if not row["quality_gate_passed"]:
            total -= CANDIDATE_SCORING.failed_gate_penalty
        results.append(
            CandidateResult(
                rank=0,
                ica1_dim=row["ica1_dim"],
                k=row["k"],
                ic2_dim=row["ic2_dim"],
                sil=row["sil"],
                ch=row["ch"],
                db=row["db"],
                db_inv=row["db_inv"],
                entropy_balance=row["entropy_balance"],
                stability=row["stability"],
                k_penalty=row["k_penalty"],
                fallback_penalty=row["fallback_penalty"],
                total_score=float(total),
                objective=row["objective"],
                random_state=int(random_state),
                transform_mode=row["transform_mode"],
                ica1_status=row["ica1_status"],
                ica2_status=row["ica2_status"],
                fallback_level=row["fallback_level"],
                retry_count=row["retry_count"],
                quality_gate_passed=row["quality_gate_passed"],
            )
        )

    results.sort(key=lambda r: (not r.quality_gate_passed, -r.total_score, r.fallback_level, -r.stability, -r.entropy_balance, -r.sil))
    results = [replace(r, rank=i) for i, r in enumerate(results, start=1)]

    mode_counts: Dict[str, int] = {}
    for r in results:
        mode_counts[r.transform_mode] = mode_counts.get(r.transform_mode, 0) + 1
    mode_summary = ", ".join(f"{k}={v}" for k, v in sorted(mode_counts.items()))
    log.info("候補探索完了: plans=%d, modes=[%s]", len(results), mode_summary)
    return results


def baseline_root(result_root: Path, project: str) -> Path:
    return result_root / f"baseline_{project}"


def history_root(result_root: Path, project: str) -> Path:
    return baseline_root(result_root, project) / "history"


def list_versions(result_root: Path, project: str) -> List[str]:
    hroot = history_root(result_root, project)
    if not hroot.exists():
        return []
    return sorted([p.name for p in hroot.glob("v*") if p.is_dir()])


def latest_version(result_root: Path, project: str) -> Optional[str]:
    vers = list_versions(result_root, project)
    return vers[-1] if vers else None


def has_baseline(result_root: Path, project: str) -> bool:
    return latest_version(result_root, project) is not None


def list_baseline_projects(result_root: Path) -> List[str]:
    projects: List[Tuple[float, str]] = []
    for p in result_root.glob("baseline_*"):
        if not p.is_dir():
            continue
        name = p.name.replace("baseline_", "", 1)
        if has_baseline(result_root, name):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            projects.append((mtime, name))
    projects.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in projects]


def resolve_default_baseline_project(
    result_root: Path,
    current_project: str,
    explicit_project: Optional[str] = None,
) -> Tuple[str, bool, str]:
    """
    baseline 自動選択ルール（元のPVM寄り）
    1) --baseline-from 指定があればそれを使う
    2) 現在 project の baseline があればそれを使う
    3) フォルダ内に baseline が 1 系列だけあればそれを使う
    4) フォルダ内に baseline が複数あれば明示指定を要求
    5) baseline がなければ current_project を返し、exists=False
    """
    if explicit_project:
        return explicit_project, has_baseline(result_root, explicit_project), "explicit"

    if has_baseline(result_root, current_project):
        return current_project, True, "project"

    candidates = list_baseline_projects(result_root)
    if len(candidates) == 1:
        return candidates[0], True, "single_in_folder"
    if len(candidates) == 0:
        return current_project, False, "none"

    raise BaselineSelectionError(
        "フォルダ内に baseline が複数あります。--baseline-from で使用する baseline を指定してください。"
    )


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    tmp_path: Optional[Path] = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent), suffix=".tmp") as tf:
            # json.dump() 自体が失敗した場合も cleanup できるよう、
            # 書き込み前に一時ファイル名を確定させておく。
            tmp_path = Path(tf.name)
            json.dump(payload, tf, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _atomic_write_npz(path: Path, arrays: Dict[str, np.ndarray]) -> None:
    ensure_dir(path.parent)
    with NamedTemporaryFile("wb", delete=False, dir=str(path.parent), suffix=".npz") as tf:
        tmp_name = tf.name
    tmp_path = Path(tmp_name)
    try:
        np.savez_compressed(tmp_path, **arrays)
        tmp_path.replace(path)
    finally:
        # replace 成功時は tmp_path は既に存在しない。失敗時だけ残骸を掃除する。
        tmp_path.unlink(missing_ok=True)


def _bundle_to_arrays(bundle: TransformBundle, centroids: np.ndarray) -> Dict[str, np.ndarray]:
    return {
        "scaler_mean": np.asarray(bundle.scaler_mean, dtype=np.float32),
        "scaler_scale": np.asarray(bundle.scaler_scale, dtype=np.float32),
        "pca_components": np.asarray(bundle.pca_components, dtype=np.float32),
        "pca_mean": np.asarray(bundle.pca_mean, dtype=np.float32),
        "pca_n_components": np.asarray([bundle.pca_n_components], dtype=np.int32),
        "ica1_components": np.asarray(bundle.ica1_components, dtype=np.float32),
        "ica1_mean": np.asarray(bundle.ica1_mean, dtype=np.float32),
        "ica1_n_components": np.asarray([bundle.ica1_n_components], dtype=np.int32),
        "ica2_components": np.asarray(bundle.ica2_components, dtype=np.float32),
        "ica2_mean": np.asarray(bundle.ica2_mean, dtype=np.float32),
        "ica2_n_components": np.asarray([bundle.ica2_n_components], dtype=np.int32),
        "embed_dim": np.asarray([bundle.embed_dim], dtype=np.int32),
        "transform_mode": np.asarray([bundle.transform_mode], dtype="<U32"),
        "ica1_status": np.asarray([bundle.ica1_status], dtype="<U32"),
        "ica2_status": np.asarray([bundle.ica2_status], dtype="<U32"),
        "final_n_components": np.asarray([bundle.final_n_components], dtype=np.int32),
        "fallback_level": np.asarray([bundle.fallback_level], dtype=np.int32),
        "centroids": np.asarray(centroids, dtype=np.float32),
    }


def _npz_scalar(data: Any, key: str, default: Any = None) -> Any:
    if key in data:
        return np.asarray(data[key]).reshape(-1)[0]
    return default


def _bundle_from_npz(path: Path) -> Tuple[TransformBundle, np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        bundle = TransformBundle(
            scaler_mean=np.asarray(data["scaler_mean"], dtype=np.float32),
            scaler_scale=np.asarray(data["scaler_scale"], dtype=np.float32),
            pca_components=np.asarray(data["pca_components"], dtype=np.float32),
            pca_mean=np.asarray(data["pca_mean"], dtype=np.float32),
            pca_n_components=int(_npz_scalar(data, "pca_n_components")),
            ica1_components=np.asarray(data["ica1_components"], dtype=np.float32),
            ica1_mean=np.asarray(data["ica1_mean"], dtype=np.float32),
            ica1_n_components=int(_npz_scalar(data, "ica1_n_components")),
            ica2_components=np.asarray(data["ica2_components"], dtype=np.float32),
            ica2_mean=np.asarray(data["ica2_mean"], dtype=np.float32),
            ica2_n_components=int(_npz_scalar(data, "ica2_n_components")),
            embed_dim=int(_npz_scalar(data, "embed_dim")),
            transform_mode=str(_npz_scalar(data, "transform_mode", "full_original_pvm")),
            ica1_status=str(_npz_scalar(data, "ica1_status", "converged")),
            ica2_status=str(_npz_scalar(data, "ica2_status", "converged")),
            final_n_components=int(_npz_scalar(data, "final_n_components", _npz_scalar(data, "ica2_n_components"))),
            fallback_level=int(_npz_scalar(data, "fallback_level", 0)),
        )
        centroids = l2_normalize(np.asarray(data["centroids"], dtype=np.float32))
    return bundle, centroids


def save_baseline_version(
    result_root: Path,
    project: str,
    bundle: TransformBundle,
    centroids: np.ndarray,
    meta: BaselineMeta,
) -> str:
    broot = baseline_root(result_root, project)
    hroot = history_root(result_root, project)
    ensure_dir(hroot)

    existing = list_versions(result_root, project)
    next_idx = (int(existing[-1][1:]) + 1) if existing else 1
    ver = f"v{next_idx:03d}"
    vdir = hroot / ver
    ensure_dir(vdir)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "script_version": SCRIPT_VERSION,
        "project": project,
        "version": ver,
        "meta": asdict(meta),
    }
    arrays = _bundle_to_arrays(bundle, centroids)
    _atomic_write_npz(vdir / "baseline_model.npz", arrays)
    _atomic_write_json(vdir / "manifest.json", manifest)

    ledger_path = broot / "baseline_manifest.json"
    ledger: List[Dict[str, Any]] = []
    if ledger_path.exists():
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("entries"), list):
                ledger = loaded["entries"]
            elif isinstance(loaded, list):
                ledger = loaded
        except (json.JSONDecodeError, OSError):
            ledger = []
    ledger.append({"version": ver, **asdict(meta)})
    _atomic_write_json(ledger_path, {"schema_version": SCHEMA_VERSION, "entries": ledger})
    return ver


def load_baseline_version(result_root: Path, project: str, version: Optional[str] = None) -> Tuple[TransformBundle, np.ndarray, Dict[str, Any], str]:
    if version is not None:
        vers = list_versions(result_root, project)
        if version not in vers:
            raise FileNotFoundError(f"baseline version が見つかりません: {project}:{version}")
        ver = version
    else:
        ver = latest_version(result_root, project)
    if ver is None:
        raise FileNotFoundError(f"baseline が見つかりません: {project}")
    vdir = history_root(result_root, project) / ver
    manifest_path = vdir / "manifest.json"
    model_path = vdir / "baseline_model.npz"
    with open(manifest_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    schema = str(payload.get("schema_version", ""))
    # PVM Standard 6.0.0 は旧baseline互換を持たない。
    # 旧プロジェクトは新標準でbaselineを再作成する。
    if schema != SCHEMA_VERSION:
        raise RuntimeError(
            f"baseline schema version が不一致です。PVM Standard 6.0.0 requires {SCHEMA_VERSION}; "
            f"旧baselineは互換性がないため再作成してください。got={schema}"
        )
    bundle, centroids = _bundle_from_npz(model_path)
    return bundle, centroids, payload["meta"], ver


# ---------------------------------------------------------------------------
# exports
# ---------------------------------------------------------------------------

def next_run_dir(result_root: Path, project: str) -> Path:
    idx = 1
    while True:
        d = result_root / f"run_{project}_{idx:02d}"
        if not d.exists():
            ensure_dir(d)
            return d
        idx += 1


def export_candidates(run_dir: Path, results: List[CandidateResult]) -> None:
    df = pd.DataFrame([asdict(r) for r in results])
    df.to_csv(run_dir / "k_candidates.csv", index=False, encoding="utf-8-sig")
    log.info("候補一覧を出力: %s", run_dir / "k_candidates.csv")


def export_candidate_assignments(
    run_dir: Path,
    df_src: pd.DataFrame,
    keep_cols: List[str],
    X: np.ndarray,
    pca_var: float,
    top_results: List[CandidateResult],
    cache: Dict[Any, Any],
    ica_retry_config: Optional[IcaRetryConfig] = None,
) -> None:
    out = df_src[keep_cols].copy()
    rows = []
    for r in top_results:
        res = get_pipeline_result(X, pca_var=pca_var, ica1_dim=r.ica1_dim, k=r.k, random_state=r.random_state, cache=cache, ica_retry_config=ica_retry_config)
        out[f"cand{r.rank}_K{r.k}"] = res["labels"].astype(int)
        rows.append({
            "plan": r.rank,
            "ica1_dim": r.ica1_dim,
            "ic2_dim": r.ic2_dim,
            "k": r.k,
            "sil": r.sil,
            "ch": r.ch,
            "db_inv": r.db_inv,
            "entropy_balance": r.entropy_balance,
            "stability": r.stability,
            "k_penalty": r.k_penalty,
            "objective": r.objective,
            "total_score": r.total_score,
            "transform_mode": r.transform_mode,
            "fallback_level": r.fallback_level,
            "quality_gate_passed": r.quality_gate_passed,
        })
    out.to_csv(run_dir / "k_candidates_assignments.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(rows).to_csv(run_dir / "k_candidates_stage2.csv", index=False, encoding="utf-8-sig")
    log.info("候補比較を出力: %s", run_dir / "k_candidates_stage2.csv")
    log.info("候補割当を出力: %s", run_dir / "k_candidates_assignments.csv")


def export_run_csv(
    run_dir: Path,
    df_src: pd.DataFrame,
    keep_cols: List[str],
    Xfinal: np.ndarray,
    labels: np.ndarray,
    dists: np.ndarray,
    max_ic_cols: Optional[int],
    extra_cols: Optional[Dict[str, Any]] = None,
) -> None:
    out = df_src[keep_cols].copy()
    ic_total = Xfinal.shape[1]
    show_ic = ic_total if not max_ic_cols else min(ic_total, int(max_ic_cols))
    for i in range(show_ic):
        out[f"IC{i+1}"] = Xfinal[:, i]
    out["cluster"] = labels.astype(int)
    out["dist"] = dists.astype(float)
    if extra_cols:
        for k, v in extra_cols.items():
            out[k] = v
    out.to_csv(run_dir / "結果スコア.csv", index=False, encoding="utf-8-sig")
    log.info("スコア出力: %s", run_dir / "結果スコア.csv")


def export_report(run_dir: Path, report: Dict[str, Any]) -> None:
    with open(run_dir / "結果レポート.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log.info("レポート出力: %s", run_dir / "結果レポート.json")


def _safe_ratio(part: int, whole: int) -> float:
    return 0.0 if whole <= 0 else float(part) / float(whole)


def _pick_quantile_examples(sorted_indices: np.ndarray, positions: Sequence[float], count: int) -> List[int]:
    if len(sorted_indices) == 0:
        return []
    picked: List[int] = []
    for p in positions:
        pos = int(round((len(sorted_indices) - 1) * float(p)))
        pos = max(0, min(pos, len(sorted_indices) - 1))
        idx = int(sorted_indices[pos])
        if idx not in picked:
            picked.append(idx)
        if len(picked) >= count:
            break
    return picked[:count]


def _fmt_examples(df_src: pd.DataFrame, text_col: str, indices: Sequence[int], dist_map: Dict[int, float], margin_map: Optional[Dict[int, float]] = None) -> List[str]:
    rows: List[str] = []
    for j in indices:
        txt = normalize_text(df_src.iloc[int(j)][text_col])
        if margin_map is None:
            rows.append(f"- `dist={float(dist_map[int(j)]):.4f}` {txt}")
        else:
            rows.append(f"- `dist={float(dist_map[int(j)]):.4f}` `margin={float(margin_map[int(j)]):.4f}` {txt}")
    return rows


def quality_note_from_mode(transform_mode: str) -> str:
    if transform_mode == "full_original_pvm":
        return "PVM Standard 6.0.0 の標準経路で解析しています。"
    if transform_mode == "ica1_only_pvm":
        return "ICA①までは使用し、centroid projection を省略した経路です。安定性を優先しています。"
    if transform_mode == "pca_pvm":
        return "ICAを用いない安定性優先の代替経路です。比較可能性は維持しています。"
    return "解析経路情報が不明です。"


def compute_run_quality(
    Xfinal: np.ndarray,
    labels: np.ndarray,
    dists: np.ndarray,
    centroids: np.ndarray,
    protected_cluster_count: int,
    transform_mode: str,
    gate_mask: Optional[np.ndarray] = None,
    accepted_extra_mask: Optional[np.ndarray] = None,
    added_mask: Optional[np.ndarray] = None,
    base_threshold: Optional[float] = None,
    base_dists: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    labels = np.asarray(labels).astype(int)
    dists = np.asarray(dists, dtype=np.float32)
    Xn = l2_normalize(Xfinal)
    Cn = l2_normalize(centroids)
    D = cosine_distance_to_centroids(Xn, Cn)
    first = D[np.arange(len(D)), labels]
    D2 = D.copy()
    D2[np.arange(len(D2)), labels] = np.inf
    second = D2[np.arange(len(D2)), np.argmin(D2, axis=1)]
    margins = (second - first).astype(np.float32)

    boundary_mask = margins <= DEFAULT_BOUNDARY_MARGIN
    boundary_rate = float(np.mean(boundary_mask)) if len(boundary_mask) else 0.0

    cluster_rows = []
    q90s = []
    for k in range(int(Cn.shape[0])):
        idx = np.where(labels == k)[0]
        if len(idx) == 0:
            continue
        q90 = float(np.quantile(dists[idx], 0.90))
        q90s.append(q90)
        cluster_rows.append({
            "cluster": int(k),
            "size": int(len(idx)),
            "share": float(len(idx) / max(len(labels), 1)),
            "boundary_rate": float(np.mean(boundary_mask[idx])) if len(idx) else 0.0,
            "q90_dist": q90,
            "type": "base" if int(k) < int(protected_cluster_count) else "extra",
        })

    q90_arr = np.asarray(q90s, dtype=np.float32)
    if q90_arr.size:
        med = float(np.median(q90_arr))
        iqr = float(np.quantile(q90_arr, 0.75) - np.quantile(q90_arr, 0.25))
        wide_threshold = med + max(iqr, 1e-6)
    else:
        wide_threshold = float("inf")
    wide_clusters = [r["cluster"] for r in cluster_rows if r["q90_dist"] > wide_threshold]
    wide_count = len(wide_clusters)

    extra_rate = float(np.mean(labels >= int(protected_cluster_count))) if len(labels) else 0.0
    added_rate = float(np.mean(np.asarray(added_mask).astype(bool))) if added_mask is not None and len(labels) else 0.0
    accepted_extra_rate = float(np.mean(np.asarray(accepted_extra_mask).astype(bool))) if accepted_extra_mask is not None and len(labels) else 0.0
    gate_over_rate = float(np.mean(np.asarray(gate_mask).astype(bool))) if gate_mask is not None and len(labels) else 0.0

    threshold_exceed_rate = None
    if base_threshold is not None and base_dists is not None and len(base_dists):
        threshold_exceed_rate = float(np.mean(np.asarray(base_dists, dtype=np.float32) > float(base_threshold)))

    review_suggested = False
    if transform_mode != "full_original_pvm":
        review_suggested = True
    if threshold_exceed_rate is not None and threshold_exceed_rate >= DEFAULT_BASELINE_REVIEW_EXCEED_RATE:
        review_suggested = True
    if boundary_rate >= DEFAULT_BASELINE_REVIEW_BOUNDARY_RATE:
        review_suggested = True
    if extra_rate >= DEFAULT_BASELINE_REVIEW_EXTRA_RATE:
        review_suggested = True

    severe_quality_issue = False
    if transform_mode == "pca_pvm" and (
        (threshold_exceed_rate is not None and threshold_exceed_rate >= max(DEFAULT_BASELINE_REVIEW_EXCEED_RATE, 0.25))
        or boundary_rate >= max(DEFAULT_BASELINE_REVIEW_BOUNDARY_RATE, 0.35)
        or extra_rate >= max(DEFAULT_BASELINE_REVIEW_EXTRA_RATE, 0.20)
    ):
        severe_quality_issue = True

    summary_bits = []
    if transform_mode != "full_original_pvm":
        summary_bits.append("今回は標準経路ではなく代替経路を使用")
    if wide_count > 0:
        summary_bits.append(f"幅広クラスタ {wide_count} 件")
    if boundary_rate >= DEFAULT_BASELINE_REVIEW_BOUNDARY_RATE:
        summary_bits.append("境界例がやや多い")
    if extra_rate >= DEFAULT_BASELINE_REVIEW_EXTRA_RATE:
        summary_bits.append("追加クラスタ比率が高め")
    if not summary_bits:
        summary_bits.append("全体として比較的読みやすい構造")

    cluster_flags = {int(r["cluster"]): {
        "boundary_rate": float(r["boundary_rate"]),
        "wide_flag": bool(int(r["cluster"]) in wide_clusters),
        "quality_note": "幅が広いので命名は控えめに" if int(r["cluster"]) in wide_clusters else ("境界が近い例がやや多い" if r["boundary_rate"] >= DEFAULT_BASELINE_REVIEW_EXCEED_RATE else "中心は比較的明瞭"),
    } for r in cluster_rows}

    quality_gate_status = "fail" if severe_quality_issue else ("warn" if review_suggested else "pass")

    return {
        "transform_mode": transform_mode,
        "transform_note": quality_note_from_mode(transform_mode),
        "boundary_rate": boundary_rate,
        "wide_cluster_count": int(wide_count),
        "wide_clusters": [int(x) for x in wide_clusters],
        "extra_rate": extra_rate,
        "added_rate": added_rate,
        "accepted_extra_rate": accepted_extra_rate,
        "gate_over_rate": gate_over_rate,
        "threshold_exceed_rate": threshold_exceed_rate,
        "baseline_review_suggested": bool(review_suggested),
        "quality_gate_status": quality_gate_status,
        "quality_summary": " / ".join(summary_bits),
        "cluster_flags": cluster_flags,
    }


def emit_run_summary(mode: str, analysis_info: Optional[Dict[str, Any]]) -> None:
    if not analysis_info:
        return
    transform_mode = str(analysis_info.get("transform_mode", "unknown"))
    quality_summary = str(analysis_info.get("quality_summary", "")).strip()
    review_suggested = bool(analysis_info.get("baseline_review_suggested"))
    gate_status = str(analysis_info.get("quality_gate_status", "pass"))
    log.info("解析経路: %s", transform_mode)
    if quality_summary:
        log.info("品質メモ: %s", quality_summary)
    log.info("baseline見直し提案: %s", "あり" if review_suggested else "なし")
    if gate_status == "warn":
        log.warning("品質注意: 今回は確認ポイントがあります。結果解釈はやや慎重に行ってください。")
    elif gate_status == "fail":
        log.warning("品質警告: 今回は代替経路かつ境界の曖昧さが大きく、結果解釈はかなり慎重に行ってください。")


def enrich_baseline_meta(meta: BaselineMeta, bundle: TransformBundle, analysis_info: Optional[Dict[str, Any]] = None) -> BaselineMeta:
    analysis_info = analysis_info or {}
    gate_status = str(analysis_info.get("quality_gate_status", "pass"))
    retry_count = meta.retry_count
    if isinstance(meta.chosen_plan, dict) and "retry_count" in meta.chosen_plan:
        try:
            retry_count = int(meta.chosen_plan.get("retry_count", retry_count))
        except (TypeError, ValueError):
            pass
    return replace(
        meta,
        transform_mode=str(bundle.transform_mode),
        ica1_status=str(bundle.ica1_status),
        ica2_status=str(bundle.ica2_status),
        fallback_level=int(bundle.fallback_level),
        quality_gate_passed=(gate_status != "fail"),
        quality_gate_status=gate_status,
        quality_summary=str(analysis_info.get("quality_summary", "")),
        quality_metrics=(analysis_info or None),
        retry_count=int(retry_count),
    )


def export_ai_prompt_pack(
    run_dir: Path,
    df_src: pd.DataFrame,
    text_col: str,
    Xfinal: np.ndarray,
    labels: np.ndarray,
    dists: np.ndarray,
    centroids: np.ndarray,
    protected_cluster_count: int,
    mode: str,
    analysis_info: Optional[Dict[str, Any]] = None,
    fallback_transform_mode: str = "unknown",
    center_n: int = 5,
    middle_n: int = 3,
    peripheral_n: int = 3,
    boundary_n: int = 3,
    nearby_cluster_count: int = 2,
    nearby_examples_per_cluster: int = 2,
) -> None:
    """
    AI向けの単一パケットを出力する。
    主役は AI_解釈依頼.md だけで、依頼文とクラスタカードを同梱する。
    AI_クラスタ一覧.csv は人間確認用の補助出力。
    """
    labels = np.asarray(labels).astype(int)
    dists = np.asarray(dists).astype(np.float32)
    Xn = l2_normalize(Xfinal)
    Cn = l2_normalize(centroids)
    D = cosine_distance_to_centroids(Xn, Cn)
    assigned = labels.copy()
    first = D[np.arange(len(D)), assigned]
    D2 = D.copy()
    D2[np.arange(len(D2)), assigned] = np.inf
    second_labels = np.argmin(D2, axis=1).astype(int)
    second = D2[np.arange(len(D2)), second_labels]
    margins = (second - first).astype(np.float32)

    centroid_dist = cosine_distance_to_centroids(Cn, Cn)

    summary_rows: List[Dict[str, Any]] = []
    packet_lines: List[str] = []
    total_n = int(len(labels))
    analysis_info = analysis_info or compute_run_quality(
        Xfinal=Xfinal,
        labels=labels,
        dists=dists,
        centroids=centroids,
        protected_cluster_count=protected_cluster_count,
        transform_mode=str(fallback_transform_mode),
    )

    packet_lines.append('# AI_解釈依頼')
    packet_lines.append('')
    packet_lines.append('このファイルを、そのまま解釈・命名を行う大規模言語モデル（LLM）または生成AIに渡してください。')
    packet_lines.append('PVM の数理説明は最小限にし、命名・解釈に必要な情報だけを整理して載せています。')
    packet_lines.append('')
    packet_lines.append('## 前提')
    packet_lines.append(f'- 総件数: {total_n}')
    packet_lines.append(f'- クラスタ数: {int(Cn.shape[0])}')
    packet_lines.append(f'- 実行モード: {mode}')
    packet_lines.append(f'- base cluster 数: {int(protected_cluster_count)}')
    packet_lines.append(f'- transform_mode: {analysis_info.get("transform_mode", "unknown")}')
    packet_lines.append(f'- quality summary: {analysis_info.get("quality_summary", "")}')
    packet_lines.append('')
    packet_lines.append('## 今回の解析経路')
    packet_lines.append(f'- {analysis_info.get("transform_note", "")}')
    if analysis_info.get("baseline_review_suggested"):
        packet_lines.append('- 注意: 今回は境界が近い/幅広いクラスタが含まれる可能性があり、探索的に読むべきクラスタがあります。')
    packet_lines.append('')
    packet_lines.append('## PVMの読み方（AI向けの最小説明）')
    packet_lines.append('- `dist`: 割り当て先クラスタ中心までの距離。小さいほど中心的。')
    packet_lines.append('- `margin`: 2番目に近いクラスタとの差。小さいほど境界的で、見誤りやすい。')
    packet_lines.append('- `base`: 初期 baseline 由来のクラスタ。')
    packet_lines.append('- `extra`: unlock で追加されたクラスタ。')
    packet_lines.append('- クラスタは必ずしも純粋ではありません。中心だけでなく、中間・周辺・境界・近い他クラスタ比較も見て判断してください。')
    packet_lines.append('')
    packet_lines.append('## 依頼')
    packet_lines.append('各クラスタについて、次を出してください。')
    packet_lines.append('1. クラスタ名称案を3つ')
    packet_lines.append('2. 最有力の名称を1つ')
    packet_lines.append('3. その名称の根拠を1〜2行')
    packet_lines.append('4. クラスタの中心的な共通性を1〜2行')
    packet_lines.append('5. 周辺例や境界例を踏まえた解釈の幅・注意点を1行')
    packet_lines.append('6. 近い他クラスタとの違いを1行')
    packet_lines.append('')
    packet_lines.append('## 命名ルール')
    packet_lines.append('- これはデータの良し悪しの判定ではなく、今回のクラスタ構造の読みやすさ・曖昧さの解釈です')
    packet_lines.append('- 「データが悪い」「失敗」とは書かず、「境界が近い」「幅が広い」「探索的に読むべき」と表現する')
    packet_lines.append('- 抽象語だけの名前にしない')
    packet_lines.append('- できるだけ「何についての、どんな評価・行動・文脈か」が分かる名前にする')
    packet_lines.append('- 中心例を主に見つつ、周辺例・境界例で幅を補正する')
    packet_lines.append('- 無理に断定しすぎず、混在がある場合はそれを明記する')
    packet_lines.append('')
    packet_lines.append('## 出力フォーマット')
    packet_lines.append('- **Cluster k**')
    packet_lines.append('  - 名称案A: ...')
    packet_lines.append('  - 名称案B: ...')
    packet_lines.append('  - 名称案C: ...')
    packet_lines.append('  - 最有力: ...')
    packet_lines.append('  - 根拠: ...')
    packet_lines.append('  - 中心的な共通性: ...')
    packet_lines.append('  - 幅・注意点: ...')
    packet_lines.append('  - 近いクラスタとの違い: ...')
    packet_lines.append('')
    packet_lines.append('## クラスタ一覧サマリー')

    # クラスタごとの材料は一度だけ作る。
    # ここから一覧サマリー、詳細カード、CSVを同じ source of truth で出力する。
    cluster_cards: List[Dict[str, Any]] = []
    for k in range(int(Cn.shape[0])):
        idx = np.where(labels == k)[0]
        if len(idx) == 0:
            continue
        own_d = dists[idx]
        order = idx[np.argsort(own_d)]
        center_idx = [int(x) for x in order[:center_n]]
        middle_idx = _pick_quantile_examples(order, [0.35, 0.50, 0.65], middle_n)
        peripheral_idx = [int(x) for x in order[::-1][:peripheral_n]]
        boundary_order = idx[np.argsort(margins[idx])]
        boundary_idx = [int(x) for x in boundary_order[:boundary_n]]
        near_order = np.argsort(centroid_dist[k])
        nearby_clusters = [int(c) for c in near_order if int(c) != k][:nearby_cluster_count]

        dist_map = {int(i): float(dists[int(i)]) for i in idx}
        margin_map = {int(i): float(margins[int(i)]) for i in idx}
        cluster_type = 'base' if int(k) < int(protected_cluster_count) else 'extra'
        flags = analysis_info.get("cluster_flags", {}).get(int(k), {})
        summary = {
            'cluster': int(k),
            'type': cluster_type,
            'size': int(len(idx)),
            'share': round(_safe_ratio(len(idx), total_n), 6),
            'nearest_clusters': ','.join(str(x) for x in nearby_clusters),
            'median_dist': float(np.median(own_d)),
            'q90_dist': float(np.quantile(own_d, 0.90)),
            'min_margin': float(np.min(margins[idx])) if len(idx) else float('nan'),
            'boundary_rate': float(flags.get("boundary_rate", 0.0)),
            'wide_flag': bool(flags.get("wide_flag", False)),
            'quality_note': str(flags.get("quality_note", "")),
        }
        cluster_cards.append({
            'cluster': int(k),
            'idx': idx,
            'center_idx': center_idx,
            'middle_idx': middle_idx,
            'peripheral_idx': peripheral_idx,
            'boundary_idx': boundary_idx,
            'nearby_clusters': nearby_clusters,
            'dist_map': dist_map,
            'margin_map': margin_map,
            'cluster_type': cluster_type,
            'flags': flags,
            'summary': summary,
        })

    for card in cluster_cards:
        summary_rows.append(card['summary'])
        packet_lines.append(
            f'- Cluster {card["cluster"]} | type={card["cluster_type"]} | size={card["summary"]["size"]} | share={_safe_ratio(card["summary"]["size"], total_n):.1%} | '
            f'nearby={", ".join(str(x) for x in card["nearby_clusters"]) if card["nearby_clusters"] else "なし"} | '
            f'boundary_rate={float(card["flags"].get("boundary_rate", 0.0)):.1%} | wide={bool(card["flags"].get("wide_flag", False))}'
        )

    packet_lines.append('')
    packet_lines.append('---')
    packet_lines.append('')

    for card in cluster_cards:
        k = int(card['cluster'])
        idx = card['idx']
        packet_lines.append(f'## Cluster {k}')
        packet_lines.append(f'- タイプ: {card["cluster_type"]}')
        packet_lines.append(f'- 件数: {int(len(idx))}')
        packet_lines.append(f'- 構成比: {_safe_ratio(len(idx), total_n):.1%}')
        packet_lines.append(f'- 近いクラスタ: {", ".join(str(x) for x in card["nearby_clusters"]) if card["nearby_clusters"] else "なし"}')
        packet_lines.append(f'- quality note: {str(card["flags"].get("quality_note", "特記事項なし"))}')
        packet_lines.append('')
        packet_lines.append('### 中心例（核）')
        packet_lines.extend(_fmt_examples(df_src, text_col, card['center_idx'], card['dist_map']))
        packet_lines.append('')
        packet_lines.append('### 中間例（広がり）')
        packet_lines.extend(_fmt_examples(df_src, text_col, card['middle_idx'], card['dist_map']))
        packet_lines.append('')
        packet_lines.append('### 周辺例（端・幅）')
        packet_lines.extend(_fmt_examples(df_src, text_col, card['peripheral_idx'], card['dist_map']))
        packet_lines.append('')
        packet_lines.append('### 境界例（他クラスタと競っている例）')
        packet_lines.extend(_fmt_examples(df_src, text_col, card['boundary_idx'], card['dist_map'], margin_map=card['margin_map']))
        packet_lines.append('')
        for near_k in card['nearby_clusters']:
            near_idx = np.where(labels == near_k)[0]
            near_order2 = near_idx[np.argsort(dists[near_idx])][:nearby_examples_per_cluster]
            near_type = 'base' if int(near_k) < int(protected_cluster_count) else 'extra'
            packet_lines.append(f'### 近いクラスタ {int(near_k)} の中心例（比較用 / {near_type}）')
            near_dist_map = {int(i): float(dists[int(i)]) for i in near_idx}
            packet_lines.extend(_fmt_examples(df_src, text_col, [int(x) for x in near_order2], near_dist_map))
            packet_lines.append('')

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / 'AI_解釈依頼.md').write_text('\n'.join(packet_lines) + '\n', encoding='utf-8')
    pd.DataFrame(summary_rows).to_csv(run_dir / 'AI_クラスタ一覧.csv', index=False, encoding='utf-8-sig')
    log.info('AI向け依頼を出力: %s', run_dir / 'AI_解釈依頼.md')
    log.info('AI向け一覧を出力: %s', run_dir / 'AI_クラスタ一覧.csv')


# ---------------------------------------------------------------------------
# lock / unlock
# ---------------------------------------------------------------------------

def quantile_table(values: np.ndarray, qs: Sequence[float] = (0.80, 0.85, 0.90, 0.95, 0.975, 0.99)) -> Dict[str, float]:
    arr = np.asarray(values, dtype=np.float32)
    if arr.size == 0:
        return {f"{float(q):.3f}": float("inf") for q in qs}
    return {f"{float(q):.3f}": float(np.quantile(arr, float(q))) for q in qs}


def resolve_quantile_threshold(meta_raw: Dict[str, Any], requested_q: float) -> float:
    qmap = meta_raw.get("base_distance_quantiles") or {}
    try:
        items = sorted((float(k), float(v)) for k, v in qmap.items())
    except (TypeError, ValueError, AttributeError):
        items = []
    if items:
        xs = np.array([k for k, _ in items], dtype=np.float64)
        ys = np.array([v for _, v in items], dtype=np.float64)
        q = float(min(max(requested_q, xs.min()), xs.max()))
        return float(np.interp(q, xs, ys))
    return float(meta_raw.get("base_threshold", float("inf")))


def compute_extra_accept_thresholds(labels: np.ndarray, dists: np.ndarray, k: int, radius_multiplier: float) -> List[float]:
    out: List[float] = []
    for j in range(k):
        mask = labels == j
        if not np.any(mask):
            out.append(float("inf"))
        else:
            q95 = float(np.quantile(dists[mask], 0.95))
            out.append(float(max(1e-6, q95 * radius_multiplier)))
    return out


def gated_lock_assign(
    Xfinal: np.ndarray,
    centroids: np.ndarray,
    protected_cluster_count: int,
    base_threshold: float,
    extra_accept_thresholds: Sequence[float],
    extra_relative_advantage: float,
) -> Dict[str, Any]:
    Xn = l2_normalize(Xfinal)
    Cn = l2_normalize(centroids)
    protected_cluster_count = max(1, min(int(protected_cluster_count), int(Cn.shape[0])))

    C_base = Cn[:protected_cluster_count]
    D_base = cosine_distance_to_centroids(Xn, C_base)
    base_labels = np.argmin(D_base, axis=1).astype(int)
    base_dists = D_base[np.arange(len(D_base)), base_labels].astype(np.float32)

    labels = base_labels.copy()
    dists = base_dists.copy()
    gate_mask = base_dists > float(base_threshold)
    accepted_extra_mask = np.zeros(len(Xn), dtype=np.int8)

    extra_count = int(Cn.shape[0] - protected_cluster_count)
    if extra_count > 0 and np.any(gate_mask):
        C_extra = Cn[protected_cluster_count:]
        D_extra = cosine_distance_to_centroids(Xn[gate_mask], C_extra)
        idxs = np.where(gate_mask)[0]
        extra_thresholds = np.asarray(list(extra_accept_thresholds), dtype=np.float32)
        if len(extra_thresholds) != extra_count:
            raise ValueError("extra_accept_thresholds の数が extra cluster 数と一致しません。")
        for row_i, src_i in enumerate(idxs):
            best_j = int(np.argmin(D_extra[row_i]))
            d_extra = float(D_extra[row_i, best_j])
            d_base = float(base_dists[src_i])
            accept = (d_extra <= float(extra_thresholds[best_j])) and (d_extra <= d_base * float(extra_relative_advantage))
            if accept:
                labels[src_i] = protected_cluster_count + best_j
                dists[src_i] = d_extra
                accepted_extra_mask[src_i] = 1

    return {
        "labels": labels.astype(int),
        "dists": dists.astype(np.float32),
        "base_labels": base_labels.astype(int),
        "base_dists": base_dists.astype(np.float32),
        "gate_mask": gate_mask.astype(np.int8),
        "accepted_extra_mask": accepted_extra_mask.astype(np.int8),
    }


def choose_unlock_option(
    Xsubset: np.ndarray,
    base_dists_subset: np.ndarray,
    max_add_k: int,
    n_total: int,
    min_points: int,
    random_state: int,
    radius_multiplier: float,
) -> UnlockChoice:
    n = len(Xsubset)
    hard_min = max(int(min_points), int(math.ceil(UNLOCK_SCORING.hard_min_ratio * max(n_total, 1))))
    min_cluster_size = max(4, int(math.ceil(UNLOCK_SCORING.min_cluster_share * max(n_total, 1))))
    if n < hard_min:
        return UnlockChoice(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, None, None, None, None, "below_min_points")

    candidates: List[UnlockChoice] = [UnlockChoice(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, None, None, None, None, "no_add")]
    kmax = max(1, min(int(max_add_k), n))
    for k in range(1, kmax + 1):
        res = spherical_kmeans(Xsubset, k=k, random_state=random_state)
        labels = res["labels"]
        counts = np.bincount(labels, minlength=k)
        min_count = int(counts.min()) if len(counts) else 0
        if min_count < min_cluster_size:
            continue

        metrics = calc_internal_metrics(res["Xn"], labels) if k >= 2 else {"sil": 0.0, "db_inv": 0.5}
        relative_gain = float((float(np.mean(base_dists_subset)) - float(np.mean(res["dists"]))) / max(float(np.mean(base_dists_subset)), 1e-12))
        if relative_gain < UNLOCK_SCORING.min_rel_gain:
            continue
        if k >= 2 and metrics.get("sil", -1.0) <= UNLOCK_SCORING.min_sil:
            continue

        balance = entropy_balance(labels, k)
        gain_score = max(0.0, min(1.0, relative_gain / UNLOCK_SCORING.gain_scale))
        cohesion_score = max(0.0, min(1.0, float(res["objective"])))
        penalty = 0.0 if k <= 1 else min(1.0, (k - 1) / max(1, max_add_k - 1))
        total = (
            UNLOCK_SCORING.gain_weight * gain_score
            + UNLOCK_SCORING.cohesion_weight * cohesion_score
            + UNLOCK_SCORING.balance_weight * balance
            - UNLOCK_SCORING.penalty_weight * penalty
        )
        thresholds = compute_extra_accept_thresholds(labels, res["dists"], k, radius_multiplier)
        candidates.append(
            UnlockChoice(
                k=int(k),
                total_score=float(total),
                relative_gain=float(relative_gain),
                objective=float(res["objective"]),
                balance=float(balance),
                sil=float(metrics.get("sil", 0.0)),
                db_inv=float(metrics.get("db_inv", 0.0)),
                min_count=int(min_count),
                labels=labels.astype(int),
                centroids=res["centroids"].astype(np.float32),
                dists=res["dists"].astype(np.float32),
                accept_thresholds=thresholds,
                reason="candidate",
            )
        )

    candidates.sort(key=lambda c: (-c.total_score, -c.relative_gain, -c.balance, c.k))
    return candidates[0]


def conservative_unlock(
    Xfinal: np.ndarray,
    centroids: np.ndarray,
    protected_cluster_count: int,
    base_threshold: float,
    extra_accept_thresholds: Sequence[float],
    unlock_q: float,
    unlock_add_k: int,
    unlock_min_points: int,
    extra_relative_advantage: float,
    extra_radius_multiplier: float,
    random_state: int,
) -> Dict[str, Any]:
    lock_res = gated_lock_assign(
        Xfinal=Xfinal,
        centroids=centroids,
        protected_cluster_count=protected_cluster_count,
        base_threshold=base_threshold,
        extra_accept_thresholds=extra_accept_thresholds,
        extra_relative_advantage=extra_relative_advantage,
    )
    labels = lock_res["labels"].copy()
    dists = lock_res["dists"].copy()
    base_dists = lock_res["base_dists"]
    gate_mask = lock_res["gate_mask"].astype(bool)
    accepted_extra_mask = lock_res["accepted_extra_mask"].astype(bool)

    # Existing extra-accepted points are not candidates for new clusters.
    novel_idx = np.where(gate_mask & (~accepted_extra_mask))[0]
    choice = choose_unlock_option(
        Xsubset=Xfinal[novel_idx],
        base_dists_subset=base_dists[novel_idx],
        max_add_k=unlock_add_k,
        n_total=len(Xfinal),
        min_points=unlock_min_points,
        random_state=random_state,
        radius_multiplier=extra_radius_multiplier,
    )

    all_centroids = l2_normalize(np.asarray(centroids, dtype=np.float32))
    extra_thresholds_out = list(extra_accept_thresholds)
    added_mask = np.zeros(len(Xfinal), dtype=np.int8)

    if choice.k > 0 and choice.labels is not None and choice.centroids is not None and choice.dists is not None and choice.accept_thresholds is not None:
        start_label = int(all_centroids.shape[0])
        labels[novel_idx] = start_label + choice.labels
        dists[novel_idx] = choice.dists
        added_mask[novel_idx] = 1
        all_centroids = np.vstack([all_centroids, l2_normalize(choice.centroids)]).astype(np.float32)
        extra_thresholds_out.extend([float(x) for x in choice.accept_thresholds])
        triggered = True
        reason = "novel_cluster_added"
    else:
        triggered = False
        reason = choice.reason

    # Threshold update must use base distances only, and only on points finally kept in protected base clusters.
    final_base_mask = labels < int(protected_cluster_count)
    base_threshold_new = float(base_threshold)
    base_threshold_raw = float(base_threshold)
    threshold_clamped = False
    base_distance_quantiles_new = quantile_table(base_dists[final_base_mask]) if np.any(final_base_mask) else quantile_table(np.array([], dtype=np.float32))
    if np.any(final_base_mask):
        q_new = float(np.quantile(base_dists[final_base_mask], unlock_q))
        base_threshold_raw = q_new
        # 1回の unlock で閾値が動きすぎないようにドリフト上限を適用する。
        # 偏ったバッチを1回流しただけで基準が崩れるのを防ぐ保護機構。
        if math.isfinite(float(base_threshold)) and float(base_threshold) > 0.0:
            lo = float(base_threshold) * (1.0 - DEFAULT_UNLOCK_THRESHOLD_DRIFT)
            hi = float(base_threshold) * (1.0 + DEFAULT_UNLOCK_THRESHOLD_DRIFT)
            clamped = float(min(max(q_new, lo), hi))
            if abs(clamped - q_new) > 1e-12:
                threshold_clamped = True
                log.info(
                    "base_threshold の更新幅を制限しました: raw=%.4f → adopted=%.4f (前回=%.4f, 上限±%d%%)",
                    q_new,
                    clamped,
                    float(base_threshold),
                    int(DEFAULT_UNLOCK_THRESHOLD_DRIFT * 100),
                )
            base_threshold_new = clamped
        else:
            base_threshold_new = q_new

    info = {
        "triggered": bool(triggered),
        "reason": reason,
        "novel_count": int(len(novel_idx)),
        "added_clusters": int(choice.k),
        "choice_total_score": float(choice.total_score),
        "choice_relative_gain": float(choice.relative_gain),
        "choice_min_count": int(choice.min_count),
        "base_threshold_before": float(base_threshold),
        "base_threshold_after": float(base_threshold_new),
        "base_threshold_raw_quantile": float(base_threshold_raw),
        "base_threshold_clamped": bool(threshold_clamped),
        "protected_cluster_count": int(protected_cluster_count),
    }
    return {
        "labels": labels.astype(int),
        "dists": dists.astype(np.float32),
        "base_dists": base_dists.astype(np.float32),
        "all_centroids": all_centroids.astype(np.float32),
        "extra_accept_thresholds": extra_thresholds_out,
        "base_threshold_new": float(base_threshold_new),
        "base_distance_quantiles_new": base_distance_quantiles_new,
        "added_mask": added_mask.astype(np.int8),
        "accepted_extra_mask": accepted_extra_mask.astype(np.int8),
        "gate_mask": gate_mask.astype(np.int8),
        "info": info,
    }


# ---------------------------------------------------------------------------
# smoke checks (offline internal)
# ---------------------------------------------------------------------------

def _smoke_internal() -> None:
    import tempfile

    rng = np.random.default_rng(123)

    # embedding prefix / compatibility guard
    assert resolve_embedding_prefix(None) == DEFAULT_EMBEDDING_PREFIX
    assert resolve_embedding_prefix("topic") == DEFAULT_EMBEDDING_PREFIX
    assert resolve_embedding_prefix("トピック:") == DEFAULT_EMBEDDING_PREFIX
    assert resolve_embedding_prefix("none") == ""
    validate_embedding_compat(
        {"embedding_model": "dummy", "embedding_prefix": "", "max_len": 128},
        "dummy",
        "",
        128,
    )
    try:
        validate_embedding_compat(
            {"embedding_model": "dummy", "embedding_prefix": "", "max_len": 128},
            "dummy",
            DEFAULT_EMBEDDING_PREFIX,
            128,
        )
        raise AssertionError("embedding prefix mismatch should stop")
    except SystemExit:
        pass

    # autodetect_columns: prefer rich text over URLs / id-like cols
    df_test = pd.DataFrame({
        "id_like": ["A001", "A002", "A003"],
        "url": ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        "textish": ["これはかなり長い自由記述テキストです。", "意味のある長文コメントです。", "テキスト列として選ばれるべきです。"],
    })
    tc, _ = autodetect_columns(df_test, None, None)
    assert tc == "textish"

    X = np.vstack([
        rng.normal(0, 0.3, size=(40, 8)),
        rng.normal(2, 0.3, size=(40, 8)),
        rng.normal(-2, 0.3, size=(40, 8)),
    ]).astype(np.float32)
    cache: Dict[Any, Any] = {}
    cands = explore_candidates(X, k_min=2, k_max=6, pca_var=0.9, random_state=42, cache=cache)
    assert cands, "candidate search failed"
    best = cands[0]
    fit = get_pipeline_result(X, pca_var=0.9, ica1_dim=best.ica1_dim, k=best.k, random_state=best.random_state, cache=cache)
    protected_count = fit["centroids"].shape[0]
    base_threshold = float(np.quantile(fit["dists"], DEFAULT_UNLOCK_Q))
    meta = BaselineMeta(
        project="smoke",
        mode="first",
        embedding_model="dummy",
        embedding_prefix="",
        max_len=128,
        pca_var=0.9,
        random_state=42,
        protected_cluster_count=int(protected_count),
        base_threshold=base_threshold,
        extra_accept_thresholds=[],
        base_distance_quantiles=quantile_table(fit["dists"]),
        unlock_q=DEFAULT_UNLOCK_Q,
        extra_relative_advantage=DEFAULT_EXTRA_REL_ADV,
        extra_radius_multiplier=DEFAULT_EXTRA_RADIUS_MULT,
        created_at=pd.Timestamp.now().isoformat(),
        script_version=SCRIPT_VERSION,
        environment={"self_check": True},
    )
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # v001 baseline
        ver1 = save_baseline_version(root, "smoke", fit["bundle"], fit["centroids"], meta)
        bundle1, cent1, meta1, _ = load_baseline_version(root, "smoke", ver1)
        Xf1 = apply_transforms(X, bundle1)
        lock1 = gated_lock_assign(
            Xfinal=Xf1,
            centroids=cent1,
            protected_cluster_count=int(meta1["protected_cluster_count"]),
            base_threshold=float(meta1["base_threshold"]),
            extra_accept_thresholds=meta1.get("extra_accept_thresholds", []),
            extra_relative_advantage=float(meta1.get("extra_relative_advantage", DEFAULT_EXTRA_REL_ADV)),
        )
        assert len(lock1["labels"]) == len(X)

        # unlock to v002
        X2 = np.vstack([X, rng.normal(5, 0.2, size=(20, 8)).astype(np.float32)])
        Xf2 = apply_transforms(X2, bundle1)
        un = conservative_unlock(
            Xfinal=Xf2,
            centroids=cent1,
            protected_cluster_count=int(meta1["protected_cluster_count"]),
            base_threshold=float(meta1["base_threshold"]),
            extra_accept_thresholds=meta1.get("extra_accept_thresholds", []),
            unlock_q=DEFAULT_UNLOCK_Q,
            unlock_add_k=2,
            unlock_min_points=8,
            extra_relative_advantage=float(meta1.get("extra_relative_advantage", DEFAULT_EXTRA_REL_ADV)),
            extra_radius_multiplier=float(meta1.get("extra_radius_multiplier", DEFAULT_EXTRA_RADIUS_MULT)),
            random_state=42,
        )
        assert len(un["labels"]) == len(X2)
        meta2 = BaselineMeta(
            project="smoke",
            mode="unlock",
            embedding_model="dummy",
            embedding_prefix="",
            max_len=128,
            pca_var=0.9,
            random_state=42,
            protected_cluster_count=int(meta1["protected_cluster_count"]),
            base_threshold=float(un["base_threshold_new"]),
            extra_accept_thresholds=[float(x) for x in un["extra_accept_thresholds"]],
            base_distance_quantiles=un["base_distance_quantiles_new"],
            unlock_q=DEFAULT_UNLOCK_Q,
            extra_relative_advantage=float(meta1.get("extra_relative_advantage", DEFAULT_EXTRA_REL_ADV)),
            extra_radius_multiplier=float(meta1.get("extra_radius_multiplier", DEFAULT_EXTRA_RADIUS_MULT)),
            created_at=pd.Timestamp.now().isoformat(),
            script_version=SCRIPT_VERSION,
            environment={"self_check": True},
            source_baseline=f"smoke:{ver1}",
        )
        ver2 = save_baseline_version(root, "smoke", bundle1, un["all_centroids"], meta2)
        bundle2, cent2, meta_loaded2, _ = load_baseline_version(root, "smoke", ver2)
        Xf3 = apply_transforms(X2, bundle2)
        lock2 = gated_lock_assign(
            Xfinal=Xf3,
            centroids=cent2,
            protected_cluster_count=int(meta_loaded2["protected_cluster_count"]),
            base_threshold=float(meta_loaded2["base_threshold"]),
            extra_accept_thresholds=meta_loaded2.get("extra_accept_thresholds", []),
            extra_relative_advantage=float(meta_loaded2.get("extra_relative_advantage", DEFAULT_EXTRA_REL_ADV)),
        )
        assert len(lock2["labels"]) == len(X2)

        # baseline-from semantics: another project can load smoke baseline and lock with it
        bundle_cross, cent_cross, meta_cross, _ = load_baseline_version(root, "smoke", ver2)
        Xf_cross = apply_transforms(X2, bundle_cross)
        lock_cross = gated_lock_assign(
            Xfinal=Xf_cross,
            centroids=cent_cross,
            protected_cluster_count=int(meta_cross["protected_cluster_count"]),
            base_threshold=float(meta_cross["base_threshold"]),
            extra_accept_thresholds=meta_cross.get("extra_accept_thresholds", []),
            extra_relative_advantage=float(meta_cross.get("extra_relative_advantage", DEFAULT_EXTRA_REL_ADV)),
        )
        assert len(lock_cross["labels"]) == len(X2)

        # export AI packet smoke
        ai_dir = root / "ai_smoke"
        export_ai_prompt_pack(
            ai_dir,
            pd.DataFrame({"text": [f"sample {i}" for i in range(len(X2))]}),
            "text",
            Xf2,
            lock2["labels"],
            lock2["dists"],
            cent2,
            int(meta_loaded2["protected_cluster_count"]),
            "lock",
            analysis_info=compute_run_quality(
                Xfinal=Xf2,
                labels=lock2["labels"],
                dists=lock2["dists"],
                centroids=cent2,
                protected_cluster_count=int(meta_loaded2["protected_cluster_count"]),
                transform_mode=str(bundle2.transform_mode),
                gate_mask=lock2["gate_mask"],
                accepted_extra_mask=lock2["accepted_extra_mask"],
                base_threshold=float(meta_loaded2["base_threshold"]),
                base_dists=lock2["base_dists"],
            ),
        )
        assert (ai_dir / "AI_解釈依頼.md").exists()
        assert (ai_dir / "AI_クラスタ一覧.csv").exists()

        # restore v001 as a new latest version and ensure it becomes loadable
        ver3 = save_baseline_version(root, "smoke", bundle1, cent1, BaselineMeta(
            project="smoke",
            mode="restore",
            embedding_model="dummy",
            embedding_prefix="",
            max_len=128,
            pca_var=0.9,
            random_state=42,
            protected_cluster_count=int(meta1["protected_cluster_count"]),
            base_threshold=float(meta1["base_threshold"]),
            extra_accept_thresholds=[float(x) for x in meta1.get("extra_accept_thresholds", [])],
            base_distance_quantiles={str(k): float(v) for k, v in (meta1.get("base_distance_quantiles") or {}).items()},
            unlock_q=float(meta1.get("unlock_q", DEFAULT_UNLOCK_Q)),
            extra_relative_advantage=float(meta1.get("extra_relative_advantage", DEFAULT_EXTRA_REL_ADV)),
            extra_radius_multiplier=float(meta1.get("extra_radius_multiplier", DEFAULT_EXTRA_RADIUS_MULT)),
            created_at=pd.Timestamp.now().isoformat(),
            script_version=SCRIPT_VERSION,
            environment={"self_check": True},
            source_baseline=f"smoke:{ver1}",
        ))
        assert ver3 == "v003"
        _, _, _, latest = load_baseline_version(root, "smoke", None)
        assert latest == "v003"
        assert list_versions(root, "smoke") == ["v001", "v002", "v003"]

        # PVM Standard 6.0.0 は旧baselineを読み込まない。
        # 旧プロジェクトは新標準でbaselineを再作成する。
        legacy_dir = history_root(root, "legacy") / "v001"
        ensure_dir(legacy_dir)
        (legacy_dir / "baseline_model.npz").write_bytes((history_root(root, "smoke") / ver1 / "baseline_model.npz").read_bytes())
        legacy_meta = asdict(meta)
        legacy_meta.pop("embedding_prefix", None)
        legacy_meta.pop("max_len", None)
        _atomic_write_json(legacy_dir / "manifest.json", {
            "schema_version": LEGACY_SCHEMA_VERSION,
            "script_version": "PVM-complete-5.6.x",
            "project": "legacy",
            "version": "v001",
            "meta": legacy_meta,
        })
        try:
            load_baseline_version(root, "legacy", "v001")
            raise AssertionError("legacy baseline should not load in PVM Standard 6.0.0")
        except RuntimeError as e:
            assert "旧baseline" in str(e)

    # threshold drift guard: a single biased unlock batch must not loosen the baseline too much
    X_far = np.array([
        [0.5, 0.8660254],
        [0.5, -0.8660254],
        [0.5, 0.8660254],
        [0.5, -0.8660254],
    ], dtype=np.float32)
    clamp_res = conservative_unlock(
        Xfinal=X_far,
        centroids=np.array([[1.0, 0.0]], dtype=np.float32),
        protected_cluster_count=1,
        base_threshold=0.10,
        extra_accept_thresholds=[],
        unlock_q=0.95,
        unlock_add_k=1,
        unlock_min_points=999,
        extra_relative_advantage=DEFAULT_EXTRA_REL_ADV,
        extra_radius_multiplier=DEFAULT_EXTRA_RADIUS_MULT,
        random_state=42,
    )
    assert bool(clamp_res["info"]["base_threshold_clamped"])
    assert abs(float(clamp_res["info"]["base_threshold_raw_quantile"]) - 0.5) < 1e-5
    assert abs(float(clamp_res["base_threshold_new"]) - 0.12) < 1e-6


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "PVM single-file production edition. "
            "未指定実行では入力ファイルを自動検出し、baseline 自動選択ルールは "
            "1) --baseline-from 指定 2) 現在 project の baseline 3) フォルダ内 baseline が1系列だけならそれ "
            "4) 複数あるなら明示指定要求 です。"
        ),
        epilog=(
            "よく使う実行例:\n"
            "  初回（基準づくり）      : python %(prog)s\n"
            "      → フォルダ内の 入力.xlsx / 入力.csv（無ければ最新の xlsx/csv）を自動検出し、\n"
            "        最適なクラスタ構成を探索して baseline を自動作成します\n"
            "  2回目以降（基準で固定）: python %(prog)s\n"
            "      → 既存 baseline をそのまま適用（lock）。クラスタ体系は変わりません\n"
            "  新しい話題も拾いたい   : python %(prog)s --unlock\n"
            "      → 既存クラスタは守ったまま、新規話題だけ追加（add-only）します\n"
            "  候補を眺めるだけ       : python %(prog)s --show-candidates\n"
            "  気に入った候補を採用   : python %(prog)s --use-plan 2\n"
            "  過去の版に戻す         : python %(prog)s --restore-version v002\n"
            "\n"
            "結果は PVMresult/ 以下に出力されます。困ったら --log_level DEBUG で詳細を確認できます。"
        ),
    )
    ap.add_argument("--input_xlsx", type=str, default=None)
    ap.add_argument("--input_csv", type=str, default=None)
    ap.add_argument("--text_col", type=str, default=None)
    ap.add_argument("--id_col", type=str, default=None)
    ap.add_argument("--project", type=str, default=None)
    ap.add_argument("--auto-input", action="store_true", help="入力ファイルの自動検出を明示する互換用オプション。現在は未指定でも自動検出が既定です")

    ap.add_argument("--embedding_model", type=str, default="cl-nagoya/ruri-v3-310m")
    ap.add_argument("--embedding-prefix", dest="embedding_prefix", type=str, default=DEFAULT_EMBEDDING_PREFIX,
                    help="embedding前に各テキストへ付けるprefix。Ruri v3のクラスタリング用途では既定の『トピック: 』を推奨。noneで空prefix")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--max_len", type=int, default=DEFAULT_MAX_LEN)

    ap.add_argument("--pca_var", type=float, default=0.90)
    ap.add_argument("--k_min", type=int, default=3)
    ap.add_argument("--k_max", type=int, default=12)
    ap.add_argument("--random_state", type=int, default=42)

    ap.add_argument("--show-candidates", dest="show_candidates", action="store_true", help="今回データだけで候補探索を行い、baseline は更新しません")
    ap.add_argument("--候補表示", dest="show_candidates", action="store_true")
    ap.add_argument("--use-plan", dest="use_plan", type=int, default=None, help="候補探索結果の plan 番号を採用して baseline を作成/更新します")
    ap.add_argument("--採用プラン", dest="use_plan", type=int, default=None)
    ap.add_argument("--baseline-from", dest="baseline_from", type=str, default=None,
                    help="別 project の baseline を参照して lock / unlock / restore するときに指定。未指定時は current project 優先、その次にフォルダ内 baseline 1系列を自動採用")
    ap.add_argument("--基準流用", dest="baseline_from", type=str, default=None)
    ap.add_argument("--baseline-version", dest="baseline_version", type=str, default=None,
                    help="lock / unlock 時に使う baseline version を指定 (例: v002)。未指定なら最新版。baseline-from と併用可")
    ap.add_argument("--restore-version", dest="restore_version", type=str, default=None,
                    help="指定 version を復元保存して終了。--baseline-from があればそれを復元元にし、--project は復元先として扱います。--baseline-from が無い場合は同名 project、無ければフォルダ内 baseline 1系列を復元元に使います")

    ap.add_argument("--unlock", dest="unlock", action="store_true", help="既存 baseline を前提に、新規話題だけ add-only で拡張します")
    ap.add_argument("--柔軟適用", dest="unlock", action="store_true")
    ap.add_argument("--unlock-q", dest="unlock_q", type=float, default=DEFAULT_UNLOCK_Q,
                    help="unlock 実行時に base から十分遠い点を novel 候補とみなす quantile。初回/ unlock 時の baseline threshold 更新にも使う。通常 lock は保存済み threshold を使う")
    ap.add_argument("--unlock-add-k", dest="unlock_add_k", type=int, default=2)
    ap.add_argument("--unlock-min-points", dest="unlock_min_points", type=int, default=8)

    ap.add_argument("--max_ic_cols", type=int, default=None)
    ap.add_argument("--ica-max-attempts", dest="ica_max_attempts", type=int, default=0,
                    help="ICA retry の最大試行数。0なら標準候補グリッドを最後まで試す")
    ap.add_argument("--ica-timeout-sec", dest="ica_timeout_sec", type=float, default=0.0,
                    help="ICA retry の時間上限秒。0なら時間上限なし")
    ap.add_argument("--log_level", type=str, default="INFO")
    ap.add_argument("--self-check", action="store_true", help="埋め込み無しの内部スモークチェック")
    ap.add_argument("--version", action="store_true")
    return ap


def choose_result_by_plan(results: List[CandidateResult], use_plan: Optional[int]) -> CandidateResult:
    if not results:
        raise RuntimeError("候補がありません。")
    if use_plan is None:
        return results[0]
    for r in results:
        if r.rank == int(use_plan):
            return r
    raise ValueError(f"指定した plan が見つかりません: {use_plan}")


def build_environment(device: str) -> Dict[str, Any]:
    env = {
        "python": platform.python_version(),
        "os": platform.platform(),
        "device": device,
    }
    try:  # pragma: no cover
        import sklearn, torch, transformers  # type: ignore
        env.update({
            "sklearn": sklearn.__version__,
            "torch": torch.__version__,
            "transformers": transformers.__version__,
        })
    except (ImportError, AttributeError):
        pass
    return env


def _restore_only(result_root: Path, args: argparse.Namespace) -> None:
    """
    restore 時の source / target ルール:
    - --baseline-from があれば、それを復元元(source)に固定する
    - --project は復元先(target)として扱う
    - --baseline-from が無い場合:
        * --project に baseline が存在すれば source=target=project（同名巻き戻し）
        * --project に baseline が無く、フォルダ内 baseline が 1 系列だけならそれを source にし、project を target に使う
        * --project 未指定なら、フォルダ内 baseline が 1 系列だけあるとき source=target=それ
        * それ以外は曖昧なので明示指定を要求する
    """
    candidates = list_baseline_projects(result_root)

    if args.baseline_from:
        source_project = args.baseline_from
        target_project = args.project or source_project
    else:
        if args.project:
            if has_baseline(result_root, args.project):
                source_project = args.project
                target_project = args.project
            elif len(candidates) == 1:
                source_project = candidates[0]
                target_project = args.project
            else:
                raise BaselineSelectionError(
                    "--restore-version では復元元が曖昧です。"
                    "同名 project を巻き戻すならその project に baseline が必要です。"
                    "別名へ複製するなら --baseline-from SOURCE --project TARGET を指定してください。"
                )
        else:
            if len(candidates) == 1:
                source_project = candidates[0]
                target_project = source_project
            else:
                raise BaselineSelectionError(
                    "--restore-version では復元元を特定できません。"
                    "--baseline-from を指定するか、baseline が 1 系列だけのフォルダで実行してください。"
                )

    if not has_baseline(result_root, source_project):
        raise BaselineSelectionError(f"restore する baseline がありません: {source_project}")
    bundle_r, centroids_r, meta_r, src_ver = load_baseline_version(result_root, source_project, args.restore_version)
    missing_embedding_meta = [k for k in ("embedding_prefix", "max_len") if meta_r.get(k) is None]
    if missing_embedding_meta:
        missing = ", ".join(missing_embedding_meta)
        raise BaselineSelectionError(
            "この baseline は v5.6以前の形式です。"
            f"v5.7 では embedding 設定({missing})を安全に確認できないため、--restore-version では復元できません。"
            "初回実行で baseline を再作成してください。"
        )
    run_dir = next_run_dir(result_root, target_project)
    restore_meta = BaselineMeta(
        project=target_project,
        mode="restore",
        embedding_model=meta_r["embedding_model"],
        embedding_prefix=str(meta_r["embedding_prefix"]),
        max_len=int(meta_r["max_len"]),
        pca_var=float(meta_r["pca_var"]),
        random_state=int(meta_r["random_state"]),
        protected_cluster_count=int(meta_r["protected_cluster_count"]),
        base_threshold=float(meta_r["base_threshold"]),
        extra_accept_thresholds=[float(x) for x in meta_r.get("extra_accept_thresholds", [])],
        base_distance_quantiles={str(k): float(v) for k, v in (meta_r.get("base_distance_quantiles") or {}).items()},
        unlock_q=float(meta_r.get("unlock_q", DEFAULT_UNLOCK_Q)),
        extra_relative_advantage=float(meta_r.get("extra_relative_advantage", DEFAULT_EXTRA_REL_ADV)),
        extra_radius_multiplier=float(meta_r.get("extra_radius_multiplier", DEFAULT_EXTRA_RADIUS_MULT)),
        created_at=pd.Timestamp.now().isoformat(),
        script_version=SCRIPT_VERSION,
        environment=build_environment("restore"),
        chosen_plan=None,
        source_baseline=f"{source_project}:{src_ver}",
    )
    new_ver = save_baseline_version(result_root, target_project, bundle_r, centroids_r, restore_meta)
    export_report(run_dir, {
        "project": target_project,
        "mode": "restore",
        "embedding_model": restore_meta.embedding_model,
        "embedding_prefix": restore_meta.embedding_prefix,
        "max_len": restore_meta.max_len,
        "source_baseline": f"{source_project}:{src_ver}",
        "used_version": new_ver,
    })
    log.info("baseline 復元: %s", history_root(result_root, target_project) / new_ver)


def main() -> None:
    ap = build_argparser()
    args = ap.parse_args()
    setup_logging(args.log_level)
    ica_retry_config = build_ica_retry_config(args.ica_max_attempts, args.ica_timeout_sec)
    embedding_prefix = resolve_embedding_prefix(args.embedding_prefix)

    if args.version:
        print(SCRIPT_VERSION)
        return
    if args.self_check:
        _smoke_internal()
        print("self-check: ok")
        return

    if args.show_candidates and args.unlock:
        raise SystemExit("--show-candidates と --unlock は同時指定できません。")
    if args.show_candidates and args.use_plan is not None:
        raise SystemExit("--show-candidates と --use-plan は同時指定できません。")
    if args.use_plan is not None and args.unlock:
        raise SystemExit("--use-plan と --unlock は同時指定できません。")
    if args.restore_version and (args.show_candidates or args.use_plan is not None or args.unlock):
        raise SystemExit("--restore-version は探索/採用/unlock と同時指定できません。")
    if args.baseline_from and (args.show_candidates or args.use_plan is not None):
        raise SystemExit("--baseline-from は lock / unlock / restore でのみ指定できます。")
    if args.baseline_version and (args.show_candidates or args.use_plan is not None or args.restore_version):
        raise SystemExit("--baseline-version は lock / unlock でのみ指定できます。")
    if not (0.0 < float(args.unlock_q) < 1.0):
        raise SystemExit("--unlock-q は (0, 1) の範囲で指定してください。")

    result_root = Path("PVMresult")
    ensure_dir(result_root)

    if args.restore_version:
        try:
            _restore_only(result_root, args)
        except BaselineSelectionError as e:
            raise SystemExit(str(e)) from None
        return

    if args.input_xlsx:
        infile, ext = Path(args.input_xlsx), "xlsx"
    elif args.input_csv:
        infile, ext = Path(args.input_csv), "csv"
    else:
        # 元のPVM互換: 未指定時は自動検出を既定とする
        infile, ext = autodetect_input()

    df0 = read_table(infile, ext)
    text_col, id_col = autodetect_columns(df0, args.text_col, args.id_col)
    log.info('columns: text_col="%s"%s', text_col, f', id_col="{id_col}"' if id_col else "")

    df = df0[[c for c in [id_col, text_col] if c is not None]].copy()
    del df0
    if id_col is None:
        df["id"] = np.arange(len(df))
        df.rename(columns={text_col: "text"}, inplace=True)
        keep_cols = ["id", "text"]
        effective_text_col = "text"
    else:
        df.rename(columns={text_col: "text", id_col: "id"}, inplace=True)
        keep_cols = ["id", "text"]
        effective_text_col = "text"
    df["text"] = df["text"].astype(str).map(normalize_text)
    n = len(df)
    log.info("データ件数: %d", n)

    project = get_project_name(args.project, infile)
    run_dir = next_run_dir(result_root, project)
    cache: Dict[Any, Any] = {}

    # default baseline selection rule
    # 1) explicit --baseline-from
    # 2) current project baseline
    # 3) single baseline series in the current folder
    # 4) if multiple baseline series exist, require explicit selection
    try:
        baseline_project, baseline_exists, baseline_resolution = resolve_default_baseline_project(
            result_root, project, args.baseline_from
        )
    except BaselineSelectionError as e:
        raise SystemExit(str(e)) from None
    if baseline_exists and baseline_resolution == "single_in_folder" and baseline_project != project:
        log.info("フォルダ内の既存 baseline を自動採用します: %s", baseline_project)
    elif baseline_exists and baseline_resolution == "project":
        log.info("現在 project の baseline を使用します: %s", baseline_project)
    elif baseline_exists and baseline_resolution == "explicit":
        log.info("--baseline-from で baseline を指定しています: %s", baseline_project)

    loaded_baseline_cache: Optional[Tuple[TransformBundle, np.ndarray, Dict[str, Any], str]] = None
    if baseline_exists and args.use_plan is None and not args.show_candidates:
        loaded_baseline_cache = load_baseline_version(result_root, baseline_project, args.baseline_version)
        validate_embedding_compat(loaded_baseline_cache[2], args.embedding_model, embedding_prefix, args.max_len)

    ensure_ruri()
    X, device = compute_embeddings(df["text"].tolist(), args.embedding_model, args.batch, args.max_len, embedding_prefix=embedding_prefix)
    if n < max(30, args.k_max * 3):
        log.warning("データ件数が少なめです（n=%d）。k_max=%d は粗めの探索になります。", n, args.k_max)

    # exploration-only path
    if args.show_candidates:
        log.info("🧭 候補探索のみを実行します。baseline は更新しません。")
        results = explore_candidates(X, args.k_min, args.k_max, args.pca_var, args.random_state, cache, ica_retry_config=ica_retry_config)
        export_candidates(run_dir, results)
        export_candidate_assignments(run_dir, df, keep_cols, X, args.pca_var, results[:5], cache, ica_retry_config=ica_retry_config)
        export_report(run_dir, {
            "n": n,
            "project": project,
            "mode": "show_candidates",
            "baseline_context": baseline_project if baseline_exists else None,
            "embedding_model": args.embedding_model,
            "embedding_prefix": embedding_prefix,
            "max_len": int(args.max_len),
            "top5": [asdict(r) for r in results[:5]],
        })
        return

    # initial or manual commit path
    if (not baseline_exists and not args.unlock) or (args.use_plan is not None):
        if not baseline_exists and args.baseline_from:
            raise SystemExit("--baseline-from は既存 baseline を使うときだけ指定してください。")
        if not baseline_exists:
            log.info("🧭 初回（自動基準作成）: ベスト Plan を自動採用して baseline を作成します。")
        else:
            log.info("🧭 明示採用（baseline 更新）: 指定 Plan で新しい baseline 版を作成します。")
        results = explore_candidates(X, args.k_min, args.k_max, args.pca_var, args.random_state, cache, ica_retry_config=ica_retry_config)
        export_candidates(run_dir, results)
        export_candidate_assignments(run_dir, df, keep_cols, X, args.pca_var, results[:5], cache, ica_retry_config=ica_retry_config)
        chosen = choose_result_by_plan(results, args.use_plan)
        fit = get_pipeline_result(X, args.pca_var, chosen.ica1_dim, chosen.k, chosen.random_state, cache, ica_retry_config=ica_retry_config)
        centroids = l2_normalize(fit["centroids"])  # base only on commit
        base_threshold = float(np.quantile(fit["dists"], args.unlock_q))
        meta = BaselineMeta(
            project=project,
            mode="first" if not baseline_exists else "manual_commit",
            embedding_model=args.embedding_model,
            embedding_prefix=embedding_prefix,
            max_len=int(args.max_len),
            pca_var=float(args.pca_var),
            random_state=int(args.random_state),
            protected_cluster_count=int(centroids.shape[0]),
            base_threshold=float(base_threshold),
            extra_accept_thresholds=[],
            base_distance_quantiles=quantile_table(fit["dists"]),
            unlock_q=float(args.unlock_q),
            extra_relative_advantage=float(DEFAULT_EXTRA_REL_ADV),
            extra_radius_multiplier=float(DEFAULT_EXTRA_RADIUS_MULT),
            created_at=pd.Timestamp.now().isoformat(),
            script_version=SCRIPT_VERSION,
            environment=build_environment(device),
            chosen_plan=asdict(chosen),
            source_baseline=None,
        )
        export_run_csv(run_dir, df, keep_cols, fit["Xfinal"], fit["labels"], fit["dists"], args.max_ic_cols)
        analysis_info = compute_run_quality(
            Xfinal=fit["Xfinal"],
            labels=fit["labels"],
            dists=fit["dists"],
            centroids=centroids,
            protected_cluster_count=int(centroids.shape[0]),
            transform_mode=str(fit["bundle"].transform_mode),
            base_threshold=base_threshold,
            base_dists=fit["dists"],
        )
        # Persist the committed baseline before producing reports so used_version is real.
        # This path is used by both first-run auto commit and --use-plan manual commit.
        meta = enrich_baseline_meta(meta, fit["bundle"], analysis_info)
        ver = save_baseline_version(result_root, project, fit["bundle"], centroids, meta)
        export_report(run_dir, {
            "n": n,
            "project": project,
            "mode": meta.mode,
            "embedding_model": args.embedding_model,
            "embedding_prefix": embedding_prefix,
            "max_len": int(args.max_len),
            "used_version": ver,
            "chosen_plan": asdict(chosen),
            "base_threshold": base_threshold,
            "protected_cluster_count": int(centroids.shape[0]),
            "transform_mode": str(fit["bundle"].transform_mode),
            "fallback_level": int(fit["bundle"].fallback_level),
            "quality_gate_status": analysis_info.get("quality_gate_status", "pass"),
            "quality": analysis_info,
        })
        export_ai_prompt_pack(
            run_dir,
            df,
            effective_text_col,
            fit["Xfinal"],
            fit["labels"],
            fit["dists"],
            centroids,
            int(centroids.shape[0]),
            meta.mode,
            analysis_info=analysis_info,
        )
        emit_run_summary(meta.mode, analysis_info)
        log.info("baseline 作成/更新: %s", history_root(result_root, project) / ver)
        return

    # lock / unlock require existing baseline
    if not baseline_exists:
        raise SystemExit("baseline がありません。まず初回実行で baseline を作成してください。")

    if loaded_baseline_cache is not None:
        bundle, centroids, meta_raw, ver = loaded_baseline_cache
    else:
        bundle, centroids, meta_raw, ver = load_baseline_version(result_root, baseline_project, args.baseline_version)
        validate_embedding_compat(meta_raw, args.embedding_model, embedding_prefix, args.max_len)

    Xfinal = apply_transforms(X, bundle)

    if args.unlock:
        log.info("=== 実行モード: 柔軟適用（add-only unlock） ===")
        # ゲートに使う閾値は一度だけ解決し、品質計算とレポートにも同じ値を使う。
        gate_threshold = resolve_quantile_threshold(meta_raw, float(args.unlock_q))
        unlock_res = conservative_unlock(
            Xfinal=Xfinal,
            centroids=centroids,
            protected_cluster_count=int(meta_raw["protected_cluster_count"]),
            base_threshold=gate_threshold,
            extra_accept_thresholds=meta_raw.get("extra_accept_thresholds", []),
            unlock_q=float(args.unlock_q),
            unlock_add_k=int(args.unlock_add_k),
            unlock_min_points=int(args.unlock_min_points),
            extra_relative_advantage=float(meta_raw.get("extra_relative_advantage", DEFAULT_EXTRA_REL_ADV)),
            extra_radius_multiplier=float(meta_raw.get("extra_radius_multiplier", DEFAULT_EXTRA_RADIUS_MULT)),
            random_state=int(args.random_state),
        )
        analysis_info = compute_run_quality(
            Xfinal=Xfinal,
            labels=unlock_res["labels"],
            dists=unlock_res["dists"],
            centroids=unlock_res["all_centroids"],
            protected_cluster_count=int(meta_raw["protected_cluster_count"]),
            transform_mode=str(bundle.transform_mode),
            gate_mask=unlock_res["gate_mask"],
            accepted_extra_mask=unlock_res["accepted_extra_mask"],
            added_mask=unlock_res["added_mask"],
            base_threshold=float(gate_threshold),
            base_dists=unlock_res["base_dists"],
        )
        new_meta = BaselineMeta(
            project=project,
            mode="unlock",
            embedding_model=args.embedding_model,
            embedding_prefix=embedding_prefix,
            max_len=int(args.max_len),
            pca_var=float(meta_raw["pca_var"]),
            random_state=int(args.random_state),
            protected_cluster_count=int(meta_raw["protected_cluster_count"]),
            base_threshold=float(unlock_res["base_threshold_new"]),
            extra_accept_thresholds=[float(x) for x in unlock_res["extra_accept_thresholds"]],
            base_distance_quantiles=unlock_res["base_distance_quantiles_new"],
            unlock_q=float(args.unlock_q),
            extra_relative_advantage=float(meta_raw.get("extra_relative_advantage", DEFAULT_EXTRA_REL_ADV)),
            extra_radius_multiplier=float(meta_raw.get("extra_radius_multiplier", DEFAULT_EXTRA_RADIUS_MULT)),
            created_at=pd.Timestamp.now().isoformat(),
            script_version=SCRIPT_VERSION,
            environment=build_environment(device),
            chosen_plan=None,
            source_baseline=f"{baseline_project}:{ver}",
        )
        # transform_mode / ica*_status / fallback_level / quality_* は
        # 直後の enrich_baseline_meta() が bundle と analysis_info から再設定するため、
        # ここでは設定しない（commit パスと同じ流儀に揃える）。
        new_meta = enrich_baseline_meta(new_meta, bundle, analysis_info)
        save_project = project
        if save_project == baseline_project:
            log.info("unlock 保存先: 読み込み元 baseline と同じ project に更新します (%s:%s)", baseline_project, ver)
        else:
            log.info("unlock 保存先: 読み込み元 baseline=%s:%s / 保存先 project=%s", baseline_project, ver, save_project)
        ver2 = save_baseline_version(result_root, save_project, bundle, unlock_res["all_centroids"], new_meta)
        extra_cols = {
            "gate_over_base": unlock_res["gate_mask"],
            "accepted_existing_extra": unlock_res["accepted_extra_mask"],
            "unlock_added": unlock_res["added_mask"],
        }
        export_run_csv(run_dir, df, keep_cols, Xfinal, unlock_res["labels"], unlock_res["dists"], args.max_ic_cols, extra_cols=extra_cols)
        export_report(run_dir, {
            "n": n,
            "project": project,
            "mode": "unlock",
            "embedding_model": args.embedding_model,
            "embedding_prefix": embedding_prefix,
            "max_len": int(args.max_len),
            "source_baseline": f"{baseline_project}:{ver}",
            "used_version": ver2,
            "unlock_info": unlock_res["info"],
            "gate_threshold": float(gate_threshold),
            "protected_cluster_count": new_meta.protected_cluster_count,
            "base_threshold": new_meta.base_threshold,
            "extra_cluster_count": len(new_meta.extra_accept_thresholds),
            "transform_mode": str(bundle.transform_mode),
            "ica1_status": str(bundle.ica1_status),
            "ica2_status": str(bundle.ica2_status),
            "fallback_level": int(bundle.fallback_level),
            "quality_gate_status": analysis_info.get("quality_gate_status", "pass"),
            "quality": analysis_info,
        })
        export_ai_prompt_pack(run_dir, df, effective_text_col, Xfinal, unlock_res["labels"], unlock_res["dists"], unlock_res["all_centroids"], int(new_meta.protected_cluster_count), "unlock", analysis_info=analysis_info)
        emit_run_summary("unlock", analysis_info)
        log.info("unlock baseline 更新: %s", history_root(result_root, save_project) / ver2)
        return

    # normal lock
    log.info("=== 実行モード: クラスターロック（baseline: %s, ver: %s） ===", baseline_project, ver)
    lock_res = gated_lock_assign(
        Xfinal=Xfinal,
        centroids=centroids,
        protected_cluster_count=int(meta_raw["protected_cluster_count"]),
        base_threshold=float(meta_raw["base_threshold"]),
        extra_accept_thresholds=meta_raw.get("extra_accept_thresholds", []),
        extra_relative_advantage=float(meta_raw.get("extra_relative_advantage", DEFAULT_EXTRA_REL_ADV)),
    )
    extra_cols = {
        "gate_over_base": lock_res["gate_mask"],
        "accepted_existing_extra": lock_res["accepted_extra_mask"],
    }
    export_run_csv(run_dir, df, keep_cols, Xfinal, lock_res["labels"], lock_res["dists"], args.max_ic_cols, extra_cols=extra_cols)
    analysis_info = compute_run_quality(
        Xfinal=Xfinal,
        labels=lock_res["labels"],
        dists=lock_res["dists"],
        centroids=centroids,
        protected_cluster_count=int(meta_raw["protected_cluster_count"]),
        transform_mode=str(bundle.transform_mode),
        gate_mask=lock_res["gate_mask"],
        accepted_extra_mask=lock_res["accepted_extra_mask"],
        base_threshold=float(meta_raw["base_threshold"]),
        base_dists=lock_res["base_dists"],
    )
    export_report(run_dir, {
        "n": n,
        "project": project,
        "mode": "lock",
        "embedding_model": args.embedding_model,
        "embedding_prefix": embedding_prefix,
        "max_len": int(args.max_len),
        "source_baseline": f"{baseline_project}:{ver}",
        "protected_cluster_count": int(meta_raw["protected_cluster_count"]),
        "base_threshold": float(meta_raw["base_threshold"]),
        "extra_cluster_count": len(meta_raw.get("extra_accept_thresholds", [])),
        "transform_mode": str(bundle.transform_mode),
        "ica1_status": str(bundle.ica1_status),
        "ica2_status": str(bundle.ica2_status),
        "fallback_level": int(bundle.fallback_level),
        "quality": analysis_info,
    })
    export_ai_prompt_pack(run_dir, df, effective_text_col, Xfinal, lock_res["labels"], lock_res["dists"], centroids, int(meta_raw["protected_cluster_count"]), "lock", analysis_info=analysis_info)
    emit_run_summary("lock", analysis_info)
    log.info("完了。")


def _run_cli() -> None:
    """main() を包む初心者向けエラーハンドラ。

    - 操作起因のエラーは短い日本語メッセージで表示
    - 予期しないエラーは要点のみ表示し、全文 traceback を
      PVMresult/last_error.log に保存して調査可能にする
    """
    try:
        main()
    except KeyboardInterrupt:
        print("\n中断しました。途中結果は保存されていない場合があります。")
        sys.exit(130)
    except SystemExit:
        raise
    except (PVMUserError, FileNotFoundError) as e:
        print(f"エラー: {e}")
        sys.exit(2)
    except Exception as e:  # pragma: no cover
        import traceback

        detail = traceback.format_exc()
        saved_to = None
        try:
            err_path = Path("PVMresult") / "last_error.log"
            ensure_dir(err_path.parent)
            err_path.write_text(detail, encoding="utf-8")
            saved_to = str(err_path)
        except OSError:
            pass
        print(f"予期しないエラーが発生しました: {type(e).__name__}: {e}")
        if saved_to:
            print(f"詳細な記録を保存しました: {saved_to}")
        print("対処のヒント: 入力ファイルの中身（テキスト列があるか）と、ライブラリの導入状況を確認してください。")
        print("再実行時に --log_level DEBUG を付けると、より詳しい経過が表示されます。")
        sys.exit(1)


if __name__ == "__main__":
    _run_cli()
