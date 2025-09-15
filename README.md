# 📘 PVM (Phantom Vector Mapping)
[![Python](https://img.shields.io/badge/python-3.11%2B-informational)](#)
![License](https://img.shields.io/badge/license-PVM%20v1.2-blue)
[![Last commit](https://img.shields.io/github/last-commit/AI-NOSUKE/PVM)](https://github.com/AI-NOSUKE/PVM/commits/main)
[![Release](https://img.shields.io/github/v/release/AI-NOSUKE/PVM?display_name=tag&sort=semver)](https://github.com/AI-NOSUKE/PVM/releases)
[![CI](https://github.com/AI-NOSUKE/PVM/actions/workflows/ci.yml/badge.svg)](https://github.com/AI-NOSUKE/PVM/actions/workflows/ci.yml)

PVM は、テキストデータを **PCA → ICA → 暫定クラスタ → ICA（重心再分解） → 最終クラスタ** の二段階アプローチで解析する Python ツールです。  
マーケティングリサーチや文章解析で、曖昧で多義的なテキスト集合から**安定して解釈しやすいクラスタ**を得ることを目的としています。

---

## ✨ 特徴（実装ベース）
- **二段階 ICA + クラスタリング** による安定化と解釈性の向上
- **Plan（=rank）提案**：初回は k×d 候補を探索し、rank=1 を最良として提示
- **ロック/アンロック再実行**：既存基準への準拠 or 新話題の吸収に対応
- **出力**：`結果スコア.csv` / `結果レポート.json` / `AI_命名依頼.md` など（詳細は下記）

> 埋め込みは現状 **Ruri（ruri-v3-310m）** を前提とした実装です。`--embedding_model` で将来拡張を想定していますが、現バージョンでは Ruri のみを公式サポートとしています。

---

## 📥 インストール

Python 3.11 以降を推奨。仮想環境での利用をおすすめします。

```bash
# 仮想環境の作成
python -m venv .venv

# Windows
.\.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

# 依存ライブラリ
pip install -r requirements.txt
```

---

## 🚀 クイックスタート

### 1) サンプルデータで候補を確認
本リポジトリにはミニサンプル `examples/sample_texts.csv`（50行）を用意しています。

```bash
python PVM.py --input_csv examples/sample_texts.csv --text_col text --show-candidates
```

### 2) 提案 Plan を採用（例：rank=5）
```bash
python PVM.py --use-plan 5
```

### 3) 2回目以降（ロック実行）
```bash
python PVM.py
```

### 4) 新話題の吸収（アンロック）
```bash
python PVM.py --unlock
# 例: しきい値や追加クラスタ上限を調整
# python PVM.py --unlock-q 0.90 --unlock-add-k 2
```

---

## ⚙️ 主なオプション（コード準拠）

| オプション | 説明 |
|---|---|
| `--show-candidates` | 候補出力のみ（基準は作らない） |
| `--use-plan N` | 候補の **rank=N** を採用して基準を作成 |
| `--unlock` | アンロック（新話題の吸収） |
| `--baseline-from NAME` | 既存プロジェクトの基準を参照 |
| `--project NAME` | 保存先のプロジェクト名（例：`1回目` / `2回目`） |
| `--input_xlsx PATH` / `--input_csv PATH` | 入力データを指定 |
| `--text_col NAME` / `--id_col NAME` | 列名を指定 |
| `--k_min N` / `--k_max N` | 探索する k の下限・上限（同値で固定も可） |
| `--unlock-q Q` | 新話題検出の距離分位点（0..1） |
| `--unlock-add-k K` | 新規に追加する最大クラスタ数 |
| `--embedding_model NAME` | 埋め込みモデル名（**現状は Ruri を公式サポート**） |
| `--batch N` / `--max_len N` / `--pca_var R` / `--random_state S` / `--log_level LEVEL` | 実行パラメータ |

> **注意**：`--sheet_name` は現行バージョンにはありません（Excel 読み込み時は既定シート or 自動判定）。必要であれば今後の改善候補です。

---

## 📦 出力ファイル

- `k_candidates.csv`：①候補評価（`rank=1` が最良）  
- `k_candidates_stage2.csv`：②最終評価（`rank=1` が最良）と割当  
- `結果スコア.csv`：各文のクラスタ割当、距離、IC（ICA②）などの指標  
- `結果レポート.json`：採用 Plan、d・K、評価指標、実行条件  
- `AI_命名依頼.md`：各クラスタの代表文（中心に近い順）を並べた命名用テンプレ

> 詳細は `Manual.md` を参照してください。

---

## 🧠 運用の目安（暫定）
- **件数**：数百〜数千文規模で実用域（CPU環境）  
- **時間**：データ量・環境に依存。初回（候補探索）は最も時間がかかります。  
- **メモリ**：ベクトル長×件数に比例。不要列の削減、`--pca_var` の圧縮率調整で低減可能。  
- **再現性**：`--random_state` を固定してください。微小差（±1e-5 程度）は浮動小数点丸めによるものです。

---

## 🎓 学術背景：なぜ二段階 ICA なのか？

高次元の埋め込み空間では、**相関が残ったままの軸**や**ノイズ成分**がクラスタリングを不安定にします。  
PVM は以下の段階で **独立性の高い意味軸** を確保し、解釈しやすさを高めます。

1. **PCA → ICA①**：相関とノイズを抑制し、一次の独立成分を抽出  
2. **暫定クラスタリング**：大まかなトピック構造を取得  
3. **ICA②（重心再分解）→ 最終クラスタリング**：クラスタ重心を再投影することで境界を明瞭化し、**軸の解釈可能性が高いクラスタ**に精緻化

これにより、単一段階の ICA/クラスタリングよりも **安定・再現性・解釈性** のバランスが良好になります。特に、マーケティングテキストのように多義性が高いデータで効果を発揮します。

---

## 🧑‍💻 作者 / 連絡先
AI-NOSUKE（透明ペインター / Phantom Color Painter）  
Powered by AI-KOTOBA

---

## 📜 ライセンス & 商用利用
- ライセンス：`LICENSE` を参照（PVM License v1.2）  
- 商用ライセンスや価格・利用条件は **`License_FAQ.md`** を参照してください。

---


