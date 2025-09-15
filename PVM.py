# -*- coding: utf-8 -*-

from __future__ import annotations

# SPDX-License-Identifier: LicenseRef-PVM-1.2
# Copyright (c) 2025 AI-NOSUKE（透明ペインター/Phantom Color Painter）
#
# 使用許諾（要約）:
# - 個人利用・研究利用は自由に使用・改変・再配布できます。
# - 社内PoC（評価）利用は 30 日以内、同一組織最大 3 名まで、5,000 レコードまたは 50MB の入力データまで無償で許容されます。
# - 商用利用は年額ライセンス契約が必要です。詳細はお問い合わせください。
# - 本ソフトウェアは「現状のまま」（AS IS）提供されます。詳細はリポジトリ同梱の LICENSE ファイルを参照してください。

__version__ = "1.0.0"
"""
==============================
# PVMとは（まずは全体像）
高次元のテキスト埋め込みを PCA→ICA で人に解釈しやすい“独立軸”へ再構成し、
**cosine** 一貫で安定にクラスタリングする二段法（PCA→ICA①→KMeans→ICA②→KMeans）。
初回に「意味座標（軸）とセントロイド」を baseline_* として保存し、以後は同一物差しで比較（ロック）。
必要ならアンロックで“新話題だけ”吸収し、**版追加**で基準を増やします（上書きなし）。

==============================
# デフォルト挙動（初回→2回目以降）
- 初回: 内部探索のベストPlanを自動採用して **baseline_{project}/history/v001** を作成
- 2回目以降（既定）: 既存基準に投影する **🔒クラスターロック**
- 2回目以降（任意）: **🔓アンロック**で基準準拠＋新話題だけ追加し、現プロジェクト側に **版追加保存**

※ baseline の選択優先度：`--baseline-from NAME` ＞ baseline_{project} ＞ ワークスペース内の最新 baseline_*

==============================
# 最短手順（コマンド）
- 初回（自動採用）                 : `python PVM.py`
- 候補だけ見て決めたい              : `python PVM.py --show-candidates`
  → 評価表と「②最終」比較用CSVを出力（この回では基準は作らない）
  → 良さそうな案を選んだら `python PVM.py --use-plan N`
     （N は **k_candidates.csv の rank 列＝global_plan** の番号）
- 2回目以降（既定＝ロック）        : `python PVM.py`
- 2回目以降（柔軟適用＝アンロック）: `python PVM.py --unlock`

（ログの予告記号：🧭 初回 / 🔒 ロック予定 / 🔓 柔軟適用予定）

==============================
# 候補の見方（②最終で比較して選ぶ）
`--show-candidates` は次の3つを出力します：
1) **k_candidates.csv** … ICA①の **d × K** 全候補の評価（sil↑/CH↑/1/(1+DB)↑ の**平均ランク**）
2) **k_candidates_stage2.csv** … **d\***固定・**Kだけ**変えたTOP5の **②最終**評価
3) **k_candidates_assignments.csv** … **全テキスト × TOP5候補の “②最終クラスタID”**（意味の切れ方を比較）
→ 基本は 2) と 3) を見て「分かれ方が良い K」を決め、1) の **rank（global_plan）** 番号を `--use-plan N` に指定

※ d\* は 1) の平均ランクを **d ごと**に集計し最良の d を自動選定（実装に一致）

==============================
# 処理フロー（俯瞰）
Embedding (Ruri) → PCA → ICA① → クラスタリング①（候補探索）
                    ↓（Plan選択：d*, K*）
                  ICA②（= min(K*-1, d*)） → クラスタリング②（最終）
                                ↓
             基準保存（初回） / 基準適用（ロック）/ 柔軟適用（アンロック）

==============================
# アンロック（柔軟適用）
- 既存基準に投影し、**基準から遠い集合だけ**を outlier（距離分位点 q）と判断
- その集合の中で最大 `add_k` 個の新クラスタを追加
- 追加後の基準は **baseline_{現在project}** に **history 版追加**（元の基準はそのまま）
- 代表パラメータ：`--unlock-q 0.90`（0<q<1） / `--unlock-add-k 2`

==============================
# 出力（毎回統一）
- 結果スコア.csv        : [id, text, IC1..ICm（ICA②）, cluster, dist]（アンロック時はフラグ列付き）
- 結果レポート.json     : 実行設定/Plan/次元数/K/metric_family/環境/使用baseline/モード
- AI_命名依頼.md        : クラスタ名称付けテンプレ（代表文）
- k_candidates.csv       : ①全候補（d×K）の評価一覧（**rank＝global_plan** を採番）
- k_candidates_stage2.csv: **d\***固定で K だけ比較したTOP5の **②最終**評価
- k_candidates_assignments.csv: 全テキスト × TOP5候補の **②最終**クラスタID

==============================
# 評価と距離（cosineで一貫）
- 指標：sil（↑）/ CH（↑）/ DB_inv=1/(1+DB)（↑）の平均ランクで総合評価
- 割当：L2正規化ベクトルの **cosine距離**（セントロイドも正規化）

==============================
# 実装メモ（安定化）
- FastICA（①②）: `max_iter=5000`, `tol=1e-4`（収束安定化）。収束しない場合はシード変更や次元-1で再試行
- Embedding は Ruri（cl-nagoya/ruri-v3-310m）想定。モデル変更時は baseline と不一致を警告（比較厳密性が低下）

==============================
# 注意
- “完全再探索の上書き”は提供しません。初回から作り直す場合は `PVMresult` を消すか、`--project` を変えてください。
- データ件数が少ない場合（例: n < 30）は探索が粗くなります（ログで警告）。
- 再現性が重要なら `--random_state` を固定してください（解析ロジックに影響はないがクラスタ初期値は関与）。
"""


import os, sys, json, argparse, logging, re, unicodedata, hashlib, platform
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

import numpy as np
import pandas as pd

from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA, FastICA
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score, calinski_harabasz_score, davies_bouldin_score
from sklearn.metrics.pairwise import cosine_distances

# tqdm（無ければダミー）
try:
    from tqdm import tqdm
except Exception:
    def tqdm(x, **k): return x  # fallback（進捗バー無し）

SCRIPT_VERSION = "PVM-cosine-two-stage-1.6 (stage2-candidates-added)"

# ----------------- ログ -----------------
def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] [CHIBI] %(message)s",
        datefmt="%H:%M:%S",
    )
log = logging.getLogger(__name__)

# ----------------- ベースライン探索（履歴ありに限定） -----------------
def find_latest_baseline_project(result_root: Path) -> Optional[str]:
    ok = []
    for p in result_root.glob("baseline_*"):
        hist = p / "history"
        if hist.exists() and any(hist.glob("v*")):
            ok.append(p)
    if not ok:
        return None
    ok.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return ok[0].name.replace("baseline_", "", 1)

# -------------- ユーティリティ --------------
def normalize_text(s: Any) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def autodetect_input() -> Tuple[Path, str]:
    here = Path(".")
    for name in ["入力.xlsx", "入力.csv"]:
        p = here / name
        if p.exists():
            return p.resolve(), p.suffix.lstrip(".").lower()
    cands = sorted(list(here.glob("*.xlsx")) + list(here.glob("*.csv")),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        raise FileNotFoundError("入力ファイルが見つかりません（入力.xlsx / 入力.csv / *.xlsx / *.csv）")
    return cands[0].resolve(), cands[0].suffix.lstrip(".").lower()

def read_table(path: Path, ext: str) -> pd.DataFrame:
    if ext == "xlsx":
        try:
            return pd.read_excel(path)
        except ImportError:
            log.error("Excel読み込みには openpyxl が必要です。pip install openpyxl")
            raise
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
    if not text_col:
        for c in ["text", "テキスト", "本文", "content", "sentence", "意味", "comment", "body"]:
            if c.lower() in lowers:
                text_col = lowers[c.lower()]
                break
        if not text_col and cols:
            lengths = {c: df[c].astype(str).str.len().mean() for c in cols}
            text_col = max(lengths, key=lengths.get)
    if not text_col or text_col not in df.columns:
        raise ValueError("テキスト列の自動検出に失敗しました。--text_col で指定してください。")
    if id_col and id_col not in df.columns:
        id_col = None
    return text_col, id_col

def get_project_name(args_tag: Optional[str], inpath: Path) -> str:
    if args_tag:
        return args_tag
    base = inpath.stem
    return re.sub(r"[^0-9A-Za-z一-龥ぁ-んァ-ン_-]+", "", base or "PVM")

def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def l2_normalize(X: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    nrm = np.linalg.norm(X, axis=1, keepdims=True) + eps
    return X / nrm

# ----------------- Embedding（Ruri） -----------------
def ensure_ruri():
    try:
        import torch  # noqa
        from transformers import AutoTokenizer, AutoModel  # noqa
        return True
    except Exception as e:
        log.error("Ruri埋め込みには torch / transformers が必要です。")
        log.error("インストール例: pip install torch transformers")
        log.error("詳細: %s", e)
        return False

def compute_embeddings(texts: List[str], model_name: str, batch: int, max_len: int) -> Tuple[np.ndarray, str]:
    import torch
    from transformers import AutoTokenizer, AutoModel
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("埋め込みモデルを読み込み中: %s", model_name)
    tok = AutoTokenizer.from_pretrained(model_name)
    mdl = AutoModel.from_pretrained(model_name).to(device).eval()
    reps = []
    truncated_total = 0
    bar_disable = not sys.stdout.isatty()
    log.info("Embedding開始: total=%d, batch=%d, max_len=%d, device=%s", len(texts), batch, max_len, device)
    with torch.inference_mode():
        with tqdm(total=len(texts), desc="Embedding", unit="text", disable=bar_disable) as pbar:
            for i in range(0, len(texts), batch):
                batch_text = texts[i:i+batch]
                enc = tok(batch_text, padding=True, truncation=True,
                          max_length=max_len, return_tensors="pt", return_length=True)
                if "length" in enc:
                    try:
                        truncated_total += int((enc["length"] > max_len).sum().item())
                    except Exception:
                        pass
                enc = {k: v.to(device) for k, v in enc.items()
                       if k in ("input_ids", "attention_mask", "token_type_ids")}
                out = mdl(**enc).last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1)
                emb = (out * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-6)
                reps.append(emb.detach().cpu().numpy())
                pbar.update(len(batch_text))
    if truncated_total > 0:
        log.warning("Tokenizerで %d 文が max_len=%d を超えたためトランケートされました。", truncated_total, max_len)
    X = np.vstack(reps).astype(np.float32)
    log.info("埋め込み完了（%d文, device=%s）", len(texts), device)
    return X, device

# ----------------- 評価（cosine一貫） -----------------
def eval_cluster(X_normalized: np.ndarray, labels: np.ndarray) -> Dict[str, float]:
    out = {}
    try:
        out["sil"] = float(silhouette_score(X_normalized, labels, metric="cosine"))
    except Exception:
        out["sil"] = -1.0
    try:
        out["ch"] = float(calinski_harabasz_score(X_normalized, labels))
    except Exception:
        out["ch"] = 0.0
    try:
        db = float(davies_bouldin_score(X_normalized, labels))
        out["db"] = db
        out["db_inv"] = 1.0/(1.0+db)
    except Exception:
        out["db"] = 10.0
        out["db_inv"] = 0.0
    return out

# ----------------- ICAヘルパー（互換フォールバック） -----------------
def _fastica_safe(n_components: int, rs: int) -> FastICA:
    """sklearnのバージョン差を吸収（whiten='unit-variance'→無い場合は True）"""
    try:
        return FastICA(n_components=n_components, random_state=rs,
                       whiten="unit-variance", max_iter=5000, tol=1e-4)
    except TypeError:
        return FastICA(n_components=n_components, random_state=rs,
                       whiten=True, max_iter=5000, tol=1e-4)

# ----------------- 軸（PCA → ICA① → ICA②） -----------------
def fit_stage1_axes(X: np.ndarray, pca_var: float, ica1_dim: int, random_state: int = 42) -> Tuple[Dict[str, Any], np.ndarray]:
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    max_pcs = min(Xs.shape[0]-1, Xs.shape[1])
    pca = PCA(n_components=max_pcs, random_state=random_state)
    Xp = pca.fit_transform(Xs)
    cumsum = np.cumsum(pca.explained_variance_ratio_)
    n_pcs = int(np.searchsorted(cumsum, pca_var) + 1)
    n_pcs = max(2, min(n_pcs, Xp.shape[1]))
    ica1_dim = max(2, min(ica1_dim, n_pcs))
    try:
        ica1 = _fastica_safe(ica1_dim, random_state)
        Xi1 = ica1.fit_transform(Xp[:, :n_pcs])
    except Exception:
        try:
            ica1 = _fastica_safe(ica1_dim, random_state + 1)
            Xi1 = ica1.fit_transform(Xp[:, :n_pcs])
        except Exception:
            ica1_dim2 = max(2, ica1_dim - 1)
            log.warning("ICA①が収束しないため n_components を %d→%d に下げて再試行します。", ica1_dim, ica1_dim2)
            ica1 = _fastica_safe(ica1_dim2, random_state + 2)
            Xi1 = ica1.fit_transform(Xp[:, :n_pcs])
    meta = dict(
        scaler_mean=scaler.mean_.tolist(),
        scaler_scale=scaler.scale_.tolist(),
        pca_components=pca.components_.tolist(),
        pca_mean=pca.mean_.tolist(),
        pca_n_components=n_pcs,
        ica1_components=ica1.components_.tolist(),
        ica1_n_components=int(ica1.n_components),
        embed_dim=int(X.shape[1]),
    )
    return meta, Xi1.astype(np.float32)

def apply_stage1_axes(X: np.ndarray, meta: Dict[str, Any]) -> np.ndarray:
    scaler_mean = np.array(meta["scaler_mean"])
    scaler_scale = np.array(meta["scaler_scale"])
    pca_components = np.array(meta["pca_components"])
    pca_mean = np.array(meta["pca_mean"])
    n_pcs = int(meta["pca_n_components"])
    ica1_components = np.array(meta["ica1_components"])
    if X.shape[1] != int(meta.get("embed_dim", X.shape[1])):
        raise ValueError("埋め込み次元が基準と不一致です。モデルや前処理が変わっていないか確認してください。")
    Xs = (X - scaler_mean) / np.where(scaler_scale==0.0, 1.0, scaler_scale)
    Xp = (Xs - pca_mean) @ pca_components.T
    Xp_sel = Xp[:, :n_pcs]
    Xi1 = Xp_sel @ ica1_components.T
    return Xi1.astype(np.float32)

def fit_stage2_axes(Xi1: np.ndarray, k: int, random_state: int = 42) -> Tuple[Dict[str, Any], np.ndarray]:
    ic2 = max(1, min(k - 1, Xi1.shape[1]))
    try:
        ica2 = _fastica_safe(ic2, random_state)
        Xi2 = ica2.fit_transform(Xi1)
    except Exception:
        try:
            ica2 = _fastica_safe(ic2, random_state + 1)
            Xi2 = ica2.fit_transform(Xi1)
        except Exception:
            ic2b = max(1, ic2 - 1)
            log.warning("ICA②が収束しないため n_components を %d→%d に下げて再試行します。", ic2, ic2b)
            ica2 = _fastica_safe(ic2b, random_state + 2)
            Xi2 = ica2.fit_transform(Xi1)
    meta2 = dict(
        ica2_components=ica2.components_.tolist(),
        ica2_n_components=int(ica2.n_components),
    )
    return meta2, Xi2.astype(np.float32)

def apply_stage2_axes(Xi1: np.ndarray, meta2: Dict[str, Any]) -> np.ndarray:
    ica2_components = np.array(meta2["ica2_components"])
    Xi2 = Xi1 @ ica2_components.T
    return Xi2.astype(np.float32)

# ----------------- 候補探索（ICA① dim × K） -----------------
def propose_ica1_dims_via_pca(X: np.ndarray, pca_var: float) -> List[int]:
    scaler = StandardScaler().fit(X)
    Xs = scaler.transform(X)
    max_pcs = min(Xs.shape[0]-1, Xs.shape[1])
    pca = PCA(n_components=max_pcs).fit(Xs)
    dims = set()
    for v in [0.80, 0.85, 0.90, 0.95]:
        n = int(np.searchsorted(np.cumsum(pca.explained_variance_ratio_), v) + 1)
        if 2 <= n <= max_pcs:
            dims.add(n)
    for d in [16, 24, 32]:
        if d <= max_pcs:
            dims.add(d)
    dims = sorted([d for d in dims if 8 <= d <= min(64, max_pcs)])
    if len(dims) > 5:
        idx = np.linspace(0, len(dims)-1, 5).round().astype(int)
        dims = [dims[i] for i in idx]
    return dims

def rank_candidates_inplace(cands: List[Dict[str, Any]]) -> None:
    if not cands:
        return
    def ranks(vals: List[float], reverse=True):
        order = np.argsort(vals)[::-1] if reverse else np.argsort(vals)
        r = np.empty_like(order, dtype=float)
        r[order] = np.arange(1, len(vals)+1)
        return r
    sil_r = ranks([c["sil"] for c in cands], reverse=True)
    ch_r  = ranks([c["ch"]  for c in cands], reverse=True)
    dbi_r = ranks([c["db_inv"] for c in cands], reverse=True)
    for i, c in enumerate(cands):
        c["rank_mean"] = float((sil_r[i] + ch_r[i] + dbi_r[i]) / 3.0)
    cands.sort(key=lambda c: (c["rank_mean"], -c["sil"]))

def explore_candidates(X: np.ndarray, k_min: int, k_max: int, pca_var: float, random_state: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    n = len(X)
    if n < 3:
        raise ValueError("データが3件未満です。最低3件以上のテキストが必要です。")
    k_max_eff = max(2, min(k_max, n - 1))
    if k_max_eff < k_max:
        log.warning("データ件数の都合で k_max を %d → %d に調整しました。", k_max, k_max_eff)
    k_max = k_max_eff
    dims = propose_ica1_dims_via_pca(X, pca_var)
    all_cands: List[Dict[str, Any]] = []
    for d in dims:
        meta1, Xi1 = fit_stage1_axes(X, pca_var=pca_var, ica1_dim=d, random_state=random_state)
        Xi1_norm = l2_normalize(Xi1)
        for k in range(max(2, k_min), max(k_min, k_max)+1):
            if k >= len(Xi1_norm):
                continue
            km = KMeans(n_clusters=k, n_init=10, random_state=random_state)
            labels1 = km.fit_predict(Xi1_norm)
            met = eval_cluster(Xi1_norm, labels1)
            all_cands.append(dict(ica1_dim=int(meta1["ica1_n_components"]), k=k, **met))
    rank_candidates_inplace(all_cands)
    best = all_cands[0] if all_cands else None
    top5 = all_cands[:5]
    return all_cands, dict(best=best, top5=top5, dims=dims)

# ---- Stage2用補助：d*の選定（平均ランク最良）とTOP5抽出 ----
def choose_best_d_by_rank(cands: List[Dict[str, Any]]) -> int:
    df = pd.DataFrame(cands)
    grp = df.groupby("ica1_dim")["rank_mean"].mean().sort_values()
    return int(grp.index[0])

def pick_top5_for_d(cands: List[Dict[str, Any]], d_star: int) -> List[Dict[str, Any]]:
    filt = [c for c in cands if int(c["ica1_dim"]) == int(d_star)]
    filt.sort(key=lambda c: (c["rank_mean"], -c["sil"]))
    return filt[:5]

# ----------------- 出力 -----------------
def export_candidates(outdir: Path, cands: List[Dict[str, Any]]):
    outdir.mkdir(parents=True, exist_ok=True)
    if not cands:
        return
    df = pd.DataFrame(cands)
    df.insert(0, "rank", np.arange(1, len(df)+1))
    (outdir / "k_candidates.csv").write_text("", encoding="utf-8")  # ensure parent exists on Windows edge cases
    df.to_csv(outdir / "k_candidates.csv", index=False, encoding="utf-8-sig")
    log.info("候補一覧を出力: %s", outdir / "k_candidates.csv")

def export_run_csv(outdir: Path, df_src: pd.DataFrame, df_cols_keep: List[str],
                   Xi2: np.ndarray, labels: np.ndarray, dists: np.ndarray,
                   max_ic_cols: Optional[int], extra_cols: Optional[Dict[str, Any]] = None):
    outdir.mkdir(parents=True, exist_ok=True)
    res = df_src[df_cols_keep].copy()
    ic_total = Xi2.shape[1]
    show_ic = ic_total if not max_ic_cols else min(ic_total, int(max_ic_cols))
    for i in range(show_ic):
        res[f"IC{i+1}"] = Xi2[:, i]
    res["cluster"] = labels.astype(int)
    res["dist"] = dists.astype(float)
    if extra_cols:
        for k, v in extra_cols.items():
            res[k] = v
    outp = outdir / "結果スコア.csv"
    res.to_csv(outp, index=False, encoding="utf-8-sig")
    log.info("スコア出力: %s", outp)

def export_report(outdir: Path, report: Dict[str, Any]):
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / "結果レポート.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log.info("レポート出力: %s", outdir / "結果レポート.json")


def export_naming_prompt(outdir: Path,
                         df_src: pd.DataFrame,
                         text_col: str,
                         labels: np.ndarray,
                         dists: np.ndarray,
                         top_n: int = 5,
                         include_dist: bool = True) -> None:
    """
    クラスタごとに dist 昇順 上位N件（中心に近い代表文）を並べ、
    クラスタ命名依頼の Markdown を出力します。
    """
    # --- resolve effective text column ---
    eff_text_col = text_col
    if eff_text_col not in df_src.columns:
        # Try common fallbacks
        fallbacks = ['意味', 'text', 'テキスト', '本文', '内容', '文', 'Text', 'TEXT', 'body', 'sentence']
        for c in fallbacks:
            if c in df_src.columns:
                eff_text_col = c
                break
        else:
            # pick the first object/string dtype column as a last resort
            try:
                eff_text_col = next(col for col in df_src.columns if df_src[col].dtype == 'object')
            except StopIteration:
                # if nothing suitable, raise a clear error
                raise KeyError(f"Text column '{text_col}' not found and no suitable fallback column detected. Available cols: {list(df_src.columns)}")
        try:
            log.warning("text_col '%s' not found; using '%s' instead.", text_col, eff_text_col)
        except Exception:
            pass
    outdir.mkdir(parents=True, exist_ok=True)
    L = np.asarray(labels).astype(int)
    D = np.asarray(dists).astype(float)

    lines = []
    lines.append("# AI命名依頼テンプレ（クラスタ名称づけ特化・dist併記）")
    lines.append("")
    lines.append("## 依頼")
    lines.append("各クラスタについて、代表文（distが小さい順 上位N件）を根拠に、実務で通じる短いクラスタ名称を3案ずつ提案してください。")
    lines.append("各案には一行の根拠要約を添えてください。必要に応じて“幅の注記”も1行で。")
    lines.append("")
    lines.append("## 前提（重要）")
    lines.append("- dist は最終クラスタ（ICA②後のKMeans）セントロイドとのコサイン距離。小さいほど中心的＝典型。")
    lines.append(f"- まずは上位N={top_n} の代表文だけで命名案を作成。余裕があれば dist が大きい文（クラスタ内 q90 以上）も見て幅を補足。")
    lines.append("")
    lines.append("## 出力フォーマット（例）")
    lines.append("- **Cluster k**")
    lines.append("  - **名称案A（最有力）**：＿＿＿＿")
    lines.append("    - 要約：＿＿＿＿（代表文の共通点を一行で）")
    lines.append("  - 名称案B：＿＿＿＿（一行要約）")
    lines.append("  - 名称案C：＿＿＿＿（一行要約）")
    lines.append("  - （任意）幅の注記：このクラスタには ＿＿＿＿ の周辺例も含まれる")
    lines.append("")
    lines.append("## 代表文（レビュー対象：dist 昇順）")

    for k in sorted(np.unique(L)):
        idx = np.where(L == k)[0]
        if len(idx) == 0:
            continue
        order = idx[np.argsort(D[idx])]
        pick = order[:top_n]
        lines.append(f"> ### Cluster {k}（上位{min(top_n, len(pick))}）")
        for j in pick:
            t = normalize_text(df_src.iloc[j][eff_text_col])
            if include_dist:
                lines.append(f"> - `dist={D[j]:.4f}`：{t}")
            else:
                lines.append(f"> - {t}")
        lines.append("")

    with open(outdir / "AI_命名依頼.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("命名依頼テンプレ出力: %s", outdir / "AI_命名依頼.md")

# ---- 候補（Stage2最終）の補助出力 ----
def export_stage2_selection(run_dir: Path, df_src: pd.DataFrame, keep_cols: List[str],
                            X: np.ndarray, pca_var: float, d_star: int,
                            plans: List[Dict[str, Any]], random_state: int,
                            max_ic_cols: Optional[int]) -> None:
    """
    d* を固定し、各 Plan の K で ICA②→最終クラスタを作って実割当（assignments）と
    最終スコア（sil/CH/DB_inv）を CSV 出力する。
    """
    # Stage1 を d* で固定して一度だけ
    meta1, Xi1 = fit_stage1_axes(X, pca_var=pca_var, ica1_dim=d_star, random_state=random_state)

    # 準備
    assign_df = df_src[keep_cols].copy()
    stage2_rows = []

    for idx, c in enumerate(plans, start=1):
        k = int(c["k"])
        # ICA② & 最終割当
        meta2, Xi2 = fit_stage2_axes(Xi1, k, random_state=random_state)
        Xi2_norm = l2_normalize(Xi2)
        km = KMeans(n_clusters=k, n_init=20, random_state=random_state)
        labels = km.fit_predict(Xi2_norm)
        C_norm = l2_normalize(km.cluster_centers_.astype(np.float32))
        D = cosine_distances(Xi2_norm, C_norm)
        dmin = D[np.arange(len(D)), labels]

        # 最終スコア（②ベース）
        met = eval_cluster(Xi2_norm, labels)
        stage2_rows.append(dict(
            plan=idx, ica1_dim=d_star, ic2_dim=int(meta2["ica2_n_components"]), k=k,
            sil=met["sil"], ch=met["ch"], db_inv=met["db_inv"]
        ))

        # 割当列（候補i_Kk）
        col_name = f"cand{idx}_K{k}"
        assign_df[col_name] = labels.astype(int)

        # （必要なら距離列も）例: assign_df[f"dist{idx}_K{k}"] = dmin.astype(float)

    # ランク付け（sil/CH/DB_invの平均ランク）
    s2 = pd.DataFrame(stage2_rows)
    def ranks(vals, reverse=True):
        order = np.argsort(vals)[::-1] if reverse else np.argsort(vals)
        r = np.empty_like(order, dtype=float)
        r[order] = np.arange(1, len(vals)+1)
        return r
    s2["r_sil"] = ranks(s2["sil"].values, True)
    s2["r_ch"]  = ranks(s2["ch"].values, True)
    s2["r_db"]  = ranks(s2["db_inv"].values, True)
    s2["rank_mean"] = (s2["r_sil"] + s2["r_ch"] + s2["r_db"]) / 3.0
    s2 = s2.sort_values(["rank_mean", "sil"], ascending=[True, False])

    # 出力
    run_dir.mkdir(parents=True, exist_ok=True)
    s2.to_csv(run_dir / "k_candidates_stage2.csv", index=False, encoding="utf-8-sig")
    assign_df.to_csv(run_dir / "k_candidates_assignments.csv", index=False, encoding="utf-8-sig")
    log.info("候補（②最終）評価を出力: %s", run_dir / "k_candidates_stage2.csv")
    log.info("候補（②最終）割当を出力: %s", run_dir / "k_candidates_assignments.csv")

# ------------- 基準の保存/読込（history管理） -------------
def save_baseline_version(basedir: Path, meta1: Dict[str, Any], meta2: Dict[str, Any],
                          centroids_normalized: np.ndarray, info: Dict[str, Any]) -> str:
    hist = basedir / "history"
    hist.mkdir(parents=True, exist_ok=True)
    existing = sorted([p.name for p in hist.glob("v*") if p.is_dir()])
    next_idx = (int(existing[-1][1:]) + 1) if existing else 1
    ver = f"v{next_idx:03d}"
    vdir = hist / ver
    vdir.mkdir(parents=True, exist_ok=True)
    with open(vdir / "baseline_axes_stage1.json", "w", encoding="utf-8") as f:
        json.dump(meta1, f, ensure_ascii=False, indent=2)
    with open(vdir / "baseline_axes_stage2.json", "w", encoding="utf-8") as f:
        json.dump(meta2, f, ensure_ascii=False, indent=2)
    np.save(vdir / "baseline_centroids.npy", centroids_normalized.astype(np.float32))
    with open(vdir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    ledger = basedir / "baseline_manifest.json"
    man = []
    if ledger.exists():
        try:
            man = json.load(open(ledger, "r", encoding="utf-8"))
            if not isinstance(man, list): man = []
        except Exception:
            man = []
    cent_sha = sha256_of_file(vdir / "baseline_centroids.npy")
    man.append(dict(version=ver, sha256=cent_sha, info=info))
    with open(ledger, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    return ver

def list_versions(basedir: Path) -> List[str]:
    hist = basedir / "history"
    if not hist.exists(): return []
    return sorted([p.name for p in hist.glob("v*") if p.is_dir()])

def load_baseline_version(basedir: Path, version: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any], np.ndarray, Dict[str, Any], str]:
    vers = list_versions(basedir)
    if not vers:
        raise FileNotFoundError("baselineのhistoryが見つかりません。初回実行が必要です。")
    ver = version if (version and version in vers) else vers[-1]
    vdir = basedir / "history" / ver
    meta1 = json.load(open(vdir / "baseline_axes_stage1.json", "r", encoding="utf-8"))
    meta2 = json.load(open(vdir / "baseline_axes_stage2.json", "r", encoding="utf-8"))
    cent = np.load(vdir / "baseline_centroids.npy")
    info = json.load(open(vdir / "manifest.json", "r", encoding="utf-8"))
    norms = np.linalg.norm(cent, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-3):
        cent = l2_normalize(cent)
    return meta1, meta2, cent, info, ver

# ------------- 柔軟適用（新話題だけ追加） -------------
def flexible_extend_centroids(Xi2_norm: np.ndarray, base_centroids_norm: np.ndarray,
                              q: float = 0.90, add_k: int = 2, random_state: int = 42) -> Tuple[np.ndarray, Dict[str, Any]]:
    D_base = cosine_distances(Xi2_norm, base_centroids_norm)
    mind = D_base.min(axis=1)
    thr = float(np.quantile(mind, q))
    mask = mind >= thr
    n_new = int(mask.sum())
    info = dict(outlier_threshold=thr, outlier_count=n_new, q=q, add_k=add_k)
    if n_new == 0 or add_k <= 0:
        return base_centroids_norm, info
    sub = Xi2_norm[mask]
    k_eff = int(min(add_k, max(1, len(sub))))
    km = KMeans(n_clusters=k_eff, n_init=10, random_state=random_state)
    _ = km.fit_predict(sub)
    C_new = l2_normalize(km.cluster_centers_.astype(np.float32))
    C_all = np.vstack([base_centroids_norm, C_new]).astype(np.float32)
    info.update(dict(added_clusters=int(k_eff)))
    return C_all, info

# ------------- メイン -------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_xlsx", type=str, default=None)
    ap.add_argument("--input_csv", type=str, default=None)
    ap.add_argument("--text_col", type=str, default=None)
    ap.add_argument("--id_col", type=str, default=None)
    ap.add_argument("--project", type=str, default=None)

    ap.add_argument("--embedding_model", type=str, default="cl-nagoya/ruri-v3-310m")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max_len", type=int, default=384)

    ap.add_argument("--pca_var", type=float, default=0.90)
    ap.add_argument("--k_min", type=int, default=3)
    ap.add_argument("--k_max", type=int, default=12)
    ap.add_argument("--random_state", type=int, default=42)

    # 動作モード
    ap.add_argument("--unlock", dest="unlock", action="store_true", help="柔軟適用（基準準拠＋新話題だけ追加して版追加保存）")
    ap.add_argument("--柔軟適用", dest="unlock", action="store_true")
    ap.add_argument("--show-candidates", dest="show_candidates", action="store_true", help="候補出しのみ（②最終で比較用CSVも出力）")
    ap.add_argument("--候補表示", dest="show_candidates", action="store_true")
    ap.add_argument("--use-plan", dest="use_plan", type=int, default=None, help="初回の基準作成時に採用するPlan番号")
    ap.add_argument("--採用プラン", dest="use_plan", type=int, default=None)
    ap.add_argument("--baseline-from", dest="baseline_from", type=str, default=None, help="外部プロジェクトの基準を流用してロック/柔軟適用（最優先）")
    ap.add_argument("--基準流用", dest="baseline_from", type=str, default=None)

    # 柔軟適用パラメータ
    ap.add_argument("--unlock-q", dest="unlock_q", type=float, default=0.90, help="外れ値判定の距離分位点（0..1）")
    ap.add_argument("--unlock-add-k", dest="unlock_add_k", type=int, default=2, help="新規に追加する最大クラスタ数")

    ap.add_argument("--max_ic_cols", type=int, default=None, help="CSVに出すIC（ICA②）列の上限（内部はフル）")
    ap.add_argument("--log_level", type=str, default="INFO")
    args = ap.parse_args()
    setup_logging(args.log_level)

    if not (0.0 < float(args.unlock_q) < 1.0):
        log.error("--unlock-q は (0, 1) の範囲で指定してください。例: 0.90")
        sys.exit(2)

    np.random.seed(args.random_state)

    # 入力
    if args.input_xlsx:
        infile, ext = Path(args.input_xlsx), "xlsx"
    elif args.input_csv:
        infile, ext = Path(args.input_csv), "csv"
    else:
        infile, ext = autodetect_input()

    df0 = read_table(infile, ext)
    text_col, id_col = autodetect_columns(df0, args.text_col, args.id_col)
    log.info('columns: text_col="%s"%s', text_col, f', id_col="{id_col}"' if id_col else "")
    df = df0[[c for c in [id_col, text_col] if c is not None]].copy()
    if id_col is None:
        df["id"] = np.arange(len(df))
        df.rename(columns={text_col: "text"}, inplace=True)
        keep_cols = ["id", "text"]
    else:
        df.rename(columns={text_col: "text", id_col: "id"}, inplace=True)
        keep_cols = ["id", "text"]
    df["text"] = df["text"].astype(str).map(normalize_text)
    n = len(df)
    log.info("データ件数: %d", n)

    # プロジェクトとディレクトリ
    project = get_project_name(args.project, infile)
    result_root = Path("PVMresult")
    baseline_dir_current = result_root / f"baseline_{project}"
    has_current_baseline = len(list_versions(baseline_dir_current)) > 0

    # 使う baseline の決定（埋め込み前の予告ログ）
    selected_baseline_project: Optional[str] = None
    selected_baseline_ver: Optional[str] = None

    if args.baseline_from:
        cand_dir = result_root / f"baseline_{args.baseline_from}"
        if not cand_dir.exists() or not list_versions(cand_dir):
            raise FileNotFoundError(f"--baseline-from で指定した基準が見つかりません: {cand_dir}")
        selected_baseline_project = args.baseline_from
        selected_baseline_ver = list_versions(cand_dir)[-1]
        mode_hint = "🔓 柔軟適用予定" if args.unlock else "🔒 クラスターロック予定"
        log.info("%s：外部基準を使用（baseline: %s, ver: %s）", mode_hint, selected_baseline_project, selected_baseline_ver)
    elif has_current_baseline:
        selected_baseline_project = project
        selected_baseline_ver = list_versions(baseline_dir_current)[-1]
        mode_hint = "🔓 柔軟適用予定" if args.unlock else "🔒 クラスターロック予定"
        log.info("%s：前回基準を使用（baseline: %s, ver: %s）", mode_hint, selected_baseline_project, selected_baseline_ver)
    else:
        latest = find_latest_baseline_project(result_root)
        if latest is not None:
            selected_baseline_project = latest
            latest_dir = result_root / f"baseline_{latest}"
            selected_baseline_ver = list_versions(latest_dir)[-1]
            mode_hint = "🔓 柔軟適用予定" if args.unlock else "🔒 クラスターロック予定"
            log.info("%s：自動検出された基準を使用（baseline: %s, ver: %s）", mode_hint, selected_baseline_project, selected_baseline_ver)
        else:
            # 全く基準がない → 初回ガイダンス
            if args.show_candidates:
                log.info("🧭 初回（候補出しのみ）：この回では基準を作りません。`k_candidates.csv` と ②最終用CSV を出します。")
            elif args.use_plan is not None:
                log.info("🧭 初回（基準作成）：選択済みの Plan #%d で基準を作成します。", int(args.use_plan))
            else:
                log.info("🧭 初回（自動基準作成）：ベストPlanを自動採用して基準を作成します。候補だけ見たい場合は `--show-candidates` を付けてください。")

    if args.unlock and selected_baseline_project is None:
        log.error("柔軟適用（--unlock）には既存の基準が必要です。まず初回実行で基準を作成してください。")
        sys.exit(2)

    log.info("プロジェクト: %s", project)

    # Ruriチェック & Embedding
    if not ensure_ruri():
        sys.exit(1)
    X, device = compute_embeddings(df["text"].tolist(), args.embedding_model, args.batch, args.max_len)

    if n < max(30, args.k_max * 3):
        log.warning("データ件数が少なめです（n=%d）。k_max=%d は粗めの探索になります。", n, args.k_max)

    # ====== 分岐 ======
    if selected_baseline_project is None and not args.unlock:
        # === 初回フロー（基準なし） ===
        log.info("=== 実行モード: 初回 ===")

        # 出力先 run_dir
        run_idx = 1
        while True:
            run_dir = result_root / f"run_{project}_{run_idx:02d}"
            if not run_dir.exists():
                break
            run_idx += 1
        run_dir.mkdir(parents=True, exist_ok=True)

        # 候補探索（①：d × K）
        cands, extra = explore_candidates(X, args.k_min, args.k_max, args.pca_var, args.random_state)
        if not cands:
            raise RuntimeError("有効な候補が見つかりませんでした。データやパラメータを見直してください。")

        export_candidates(run_dir, cands)

        # Stage2用の補助（d*固定でKだけ変えるTOP5）
        d_star = choose_best_d_by_rank(cands)
        top5_for_stage2 = pick_top5_for_d(cands, d_star)

        if args.show_candidates:
            # ②の最終結果で比較できるCSVを出力
            export_stage2_selection(run_dir, df, keep_cols, X, args.pca_var, d_star,
                                    top5_for_stage2, args.random_state, args.max_ic_cols)
            # ログ整形
            log.info("候補（TOP5 / d*=%d 固定・Kだけ比較 / ランク平均が小さいほど良い）:", d_star)
            log.info("   Plan  K    sil     CH     DB_inv")
            tmp = pd.read_csv(run_dir / "k_candidates_stage2.csv")
            for _, row in tmp.iterrows():
                log.info("   %4d %3d  %5.3f %6.0f   %5.3f",
                         int(row['plan']), int(row['k']),
                         float(row['sil']), float(row['ch']), float(row['db_inv']))
            log.info("この回は候補出しで終了します。`k_candidates_stage2.csv` / `k_candidates_assignments.csv` を見て、")
            log.info("次は `--use-plan N`（任意）か未指定でベスト採用にて実行してください。")
            log.info("👉 例: `python PVM.py --use-plan 3`（Plan#3を採用） / `python PVM.py`（ベストPlanを自動採用）")
            return

        # Plan決定（未指定なら rank=1）
        plan_idx = (max(1, min(int(args.use_plan), len(cands))) - 1) if (args.use_plan is not None) else 0
        chosen = cands[plan_idx]
        d_star, k_star = int(chosen["ica1_dim"]), int(chosen["k"])
        log.info("基準作成: Plan #%d を採用 → d=%d, K=%d", plan_idx+1, d_star, k_star)

        # Stage1 & Stage2
        meta1, Xi1 = fit_stage1_axes(X, pca_var=args.pca_var, ica1_dim=d_star, random_state=args.random_state)
        meta2, Xi2 = fit_stage2_axes(Xi1, k_star, random_state=args.random_state)
        Xi2_norm = l2_normalize(Xi2)
        km2 = KMeans(n_clusters=k_star, n_init=20, random_state=args.random_state)
        labels2 = km2.fit_predict(Xi2_norm)
        C2_norm = l2_normalize(km2.cluster_centers_.astype(np.float32))
        D = cosine_distances(Xi2_norm, C2_norm)
        labels_final = np.argmin(D, axis=1).astype(int)
        dists = D[np.arange(len(D)), labels_final]

        # baseline保存
        baseline_dir_current.mkdir(parents=True, exist_ok=True)
        info = dict(
            project=project,
            plan=dict(rank=plan_idx+1, ica1_dim=int(meta1["ica1_n_components"]), k=k_star, chosen=chosen),
            script_version=SCRIPT_VERSION,
            embedding_model=args.embedding_model,
            pca_var=args.pca_var,
            random_state=args.random_state,
            rank_method="mean_of_ranks(sil↑, CH↑, 1/(1+DB)↑)",
            metric_family="cosine-spherical",
            centroids_are_normalized=True,
            mode="first",
            environment=dict(
                python=platform.python_version(),
                os=platform.platform(),
            )
        )
        try:
            import torch, transformers, sklearn  # type: ignore
            info["environment"].update(dict(
                torch=torch.__version__,
                transformers=transformers.__version__,
                sklearn=sklearn.__version__,
                device=device,
            ))
        except Exception:
            pass

        ver = save_baseline_version(baseline_dir_current, meta1, meta2, C2_norm, info)

        export_run_csv(run_dir, df, keep_cols, Xi2, labels_final, dists, args.max_ic_cols)
        export_report(run_dir, dict(
            n=n, project=project, mode="first (commit)", used_version=ver,
            ica1_dim=int(meta1["ica1_n_components"]), k=k_star, ic2_dim=int(meta2["ica2_n_components"]),
            candidates_top5=pick_top5_for_d(cands, choose_best_d_by_rank(cands))[:5],
            rank_method="mean_of_ranks(sil↑, CH↑, 1/(1+DB)↑)",
            metric_family="cosine-spherical",
        ))
        export_naming_prompt(run_dir, df, text_col, labels_final, dists, top_n=5, include_dist=True)
        log.info("baseline作成: %s", baseline_dir_current / "history" / ver)
        log.info("=========== Summary ===========")
        log.info("n=%d, project=%s, outdir=%s", n, project, str(run_dir.resolve()))
        log.info("完了。")
        return

    # === baseline あり（ロック or 柔軟適用） ===
    run_idx = 1
    while True:
        run_dir = result_root / f"run_{project}_{run_idx:02d}"
        if not run_dir.exists():
            break
        run_idx += 1
    run_dir.mkdir(parents=True, exist_ok=True)

    base_dir = result_root / f"baseline_{selected_baseline_project}"
    meta1, meta2, C2_norm, info, ver = load_baseline_version(base_dir, version=None)
    if info.get("embedding_model") and info["embedding_model"] != args.embedding_model:
        log.warning("baselineのembedding_model(%s) と現在(%s) が不一致。時系列比較の厳密性が下がる可能性があります。",
                    info["embedding_model"], args.embedding_model)

    Xi1 = apply_stage1_axes(X, meta1)
    Xi2 = apply_stage2_axes(Xi1, meta2)
    Xi2_norm = l2_normalize(Xi2)

    if args.unlock:
        log.info("=== 実行モード: 柔軟適用（基準準拠＋新話題吸収） ===")
        C_all, flex_info = flexible_extend_centroids(
            Xi2_norm, C2_norm, q=float(args.unlock_q), add_k=int(args.unlock_add_k),
            random_state=args.random_state
        )
        D_all = cosine_distances(Xi2_norm, C_all)
        labels_final = np.argmin(D_all, axis=1).astype(int)
        dists = D_all[np.arange(len(D_all)), labels_final]

        base_mind = cosine_distances(Xi2_norm, C2_norm).min(axis=1)
        extra_cols = {"flex_added": (base_mind >= flex_info["outlier_threshold"]).astype(int)}

        baseline_dir_current.mkdir(parents=True, exist_ok=True)
        info2 = dict(info)
        info2.update(dict(
            project=project,
            mode="unlock",
            unlock_params=flex_info,
            script_version=SCRIPT_VERSION,
        ))
        ver2 = save_baseline_version(baseline_dir_current, meta1, meta2, C_all, info2)

        export_run_csv(run_dir, df, keep_cols, Xi2, labels_final, dists, args.max_ic_cols, extra_cols=extra_cols)
        export_report(run_dir, dict(
            n=n, project=project, mode="unlock (commit)", used_version=ver2,
            base_version_used=f"{base_dir.name}:{ver}",
            ica1_dim=int(meta1["ica1_n_components"]), k=int(C_all.shape[0]),
            ic2_dim=int(meta2["ica2_n_components"]),
            unlock_params=flex_info,
            metric_family=info.get("metric_family", "cosine-spherical"),
            centroids_are_normalized=True,
        ))
        export_naming_prompt(run_dir, df, text_col, labels_final, dists, top_n=5, include_dist=True)
        log.info("柔軟適用: 新話題候補 = %d 件（q=%.2f）", flex_info.get("outlier_count", 0), float(args.unlock_q))
        log.info("柔軟適用: 追加セントロイド = %d 個", flex_info.get("added_clusters", 0))
        log.info("柔軟適用: 追加後の基準を保存しました → %s", baseline_dir_current / "history" / ver2)

    else:
        log.info("=== 実行モード: クラスターロック（基準固定: %s, ver: %s） ===", base_dir.name, ver)
        D = cosine_distances(Xi2_norm, C2_norm)
        labels_final = np.argmin(D, axis=1).astype(int)
        dists = D[np.arange(len(D)), labels_final]
        export_run_csv(run_dir, df, keep_cols, Xi2, labels_final, dists, args.max_ic_cols)
        export_report(run_dir, dict(
            n=n, project=project, mode="lock", used_version=ver,
            base_project=base_dir.name.replace("baseline_", "", 1),
            ica1_dim=int(meta1["ica1_n_components"]), k=int(C2_norm.shape[0]),
            ic2_dim=int(meta2["ica2_n_components"]),
            metric_family=info.get("metric_family", "cosine-spherical"),
            centroids_are_normalized=info.get("centroids_are_normalized", True),
        ))
        export_naming_prompt(run_dir, df, text_col, labels_final, dists, top_n=5, include_dist=True)
        log.info("=========== Summary ===========")
        log.info("n=%d, project=%s, outdir=%s", n, project, str(run_dir.resolve()))
        log.info("完了。")

if __name__ == "__main__":
    main()