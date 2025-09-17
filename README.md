# PVM (Phantom Vector Mapping)
**二段階 ICA による日本語テキストクラスタリングエンジン**  
PCA → ICA① → KMeans → ICA②（セントロイド再分解）→ KMeans の二段階アプローチで、**解釈しやすく安定**したクラスタを作るための研究・実務向けツール。

![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/License-PVM%20v1.2-green)
![release](https://img.shields.io/github/v/release/AI-NOSUKE/PVM?color=orange)
![ci](https://github.com/AI-NOSUKE/PVM/actions/workflows/ci.yml/badge.svg)

---

## 目次
- [概要](#概要)
- [インストール（ローカル）](#インストールローカル)
- [クイックスタート](#クイックスタート)
  - [① CIサンプル（必ず通る最小テスト）](#①-ciサンプル必ず通る最小テスト)
  - [② ローカル利用（最小コマンド）](#②-ローカル利用最小コマンド)
- [主なオプション（基本）](#主なオプション基本)
- [補助オプション（その他）](#補助オプションその他)
- [出力ファイル](#出力ファイル)
- [運用の目安と再現性](#運用の目安と再現性)
- [学術背景（要点）](#学術背景要点)
- [ライセンス / 作者](#ライセンス--作者)

---

## 概要
- **目的**：実務で「意味が取り出しやすいクラスタ」を安定して得る。
- **特徴**：
  - **二段階 ICA**：一次の独立成分で大枠を掴み、**クラスタ重心を再分解（ICA②）**して輪郭を整える。
  - **候補→採用→ロック/アンロック**の一連フローをコマンドで直感操作。
  - **再現配慮**：乱数シード・baselineロック・ログ出力で運用を安定化。

> 💡 初回を **無指定で実行**すると、自動でベスト Plan が採用され基準が作成されます。  
> 意図を明示したい場合は --show-candidates → --use-plan N を推奨。

---

## インストール（ローカル）

`powershell
git clone https://github.com/AI-NOSUKE/PVM.git
cd PVM
python -m venv .venv
.\.venv\Scripts\activate    # Windows
# source .venv/bin/activate # macOS/Linux
pip install -r requirements.txt
`

- Python 3.11+ 推奨。初回は埋め込みモデル取得で少し時間がかかることがあります。

---

## クイックスタート

### ① CIサンプル（必ず通る最小テスト）

固定データ（examples/sample_texts.csv、列名 	ext）で検証しています。

`powershell
# 候補探索
python PVM.py --input_csv examples/sample_texts.csv --text_col text --show-candidates

# Plan採用（例：rank=1）
python PVM.py --use-plan 1

# 2回目以降はロック適用
python PVM.py

# 柔軟適用（新話題の吸収）
python PVM.py --unlock
`

### ② ローカル利用（最小コマンド）
あなたのCSVのテキスト列名が **	ext** なら --text_col は不要。  
複数の試行結果を分けたい時だけ --project を付けます。

`powershell
# 候補探索
python PVM.py --show-candidates

# Plan採用
python PVM.py --use-plan 1

# 2回目以降（ロック適用）
python PVM.py

# 柔軟適用（アンロック）
python PVM.py --unlock
`

👉 サンプルCSVはこちら：[examples/sample_texts.csv](examples/sample_texts.csv)

<details>
<summary><b>参考: 実行ログの例（クリックで展開）</b></summary>

`	ext
INFO PVM: embedding model = cl-nagoya/ruri-v3-310m
INFO PVM: candidates search k in [8..16], seed=42
INFO PVM: stage-2 compare TOP5 → results written to PVMresult/k_candidates_stage2.csv
INFO PVM: global plan rank=1 selected → baseline saved to PVMresult/baseline_1回目/
INFO PVM: locked apply done → results written to PVMresult/結果スコア.csv
`
</details>

---

## 主なオプション（基本）

| オプション | 説明 |
|---|---|
| *(無指定)* | 既存の基準でロック適用。**初回は自動採用で基準作成** |
| --show-candidates | 候補のみ出力（基準は作らない） |
| --use-plan N | 候補の **rank=N** を採用して基準作成 |
| --unlock | 柔軟適用：新話題を追加クラスタで吸収 |
| --input_csv PATH / --input_xlsx PATH | 入力データの指定（いずれか一つ） |
| --text_col NAME | テキスト列名（既定 	ext） |
| --project NAME | 出力の保存先名（例：1回目 / 2回目） |

> 補足：--id_col は任意（未指定なら内部付番）。

---

## 補助オプション（その他）

| オプション | 説明 |
|---|---|
| --unlock-q Q | 新話題検出の距離分位点（0<**Q**<1、既定 0.90） |
| --unlock-add-k K | 追加クラスタの上限（既定 2） |
| --max_ic_cols N | 結果スコア.csv に出力する IC 列の上限 |
| --k_min N / --k_max N | 候補探索の K 範囲 |
| --embedding_model NAME | 既定：cl-nagoya/ruri-v3-310m |
| --batch N / --max_len N | 埋め込みのバッチサイズ/最大長 |
| --pca_var R | PCA の累積寄与（既定 0.90） |
| --random_state S | 乱数シード |
| --log_level LEVEL | ログレベル（INFO/DEBUG など） |
| **日本語alias** | --候補表示 / --採用プラン / --柔軟適用 など（利便性向けの補助） |

---

## 出力ファイル

代表的な成果物（プロジェクトごとに PVMresult/ 以下へ保存）：

- 結果スコア.csv … 各候補/各クラスタのスコア・IC 指標
- 結果レポート.json … 実行情報・採用 Plan などのメタ
- AI_命名依頼.md … クラスタ命名依頼テンプレ
- k_candidates.csv … 候補一覧（一次）
- k_candidates_stage2.csv … 二段階後の候補比較（TOP5 等）
- k_candidates_assignments.csv … 割当情報のサマリ
- logs/ … 実行ログ

---

## 運用の目安と再現性

- **件数レンジ**：超少量（例：<30）では候補探索が粗くなります。十分な件数を推奨。  
- **初回の自動採用**：無指定で走らせると自動採用で基準作成。意図を固定したい場合は --use-plan N を明示。  
- **再現性**：--random_state の固定 + **baselineロック** 運用を推奨。  
- **モデル依存**：埋め込みモデルを変えると軸解釈が変わることがあります（既定は Ruri）。

---

## 学術背景（要点）

- **ICA①**：潜在因子を独立化して「混じって見えにくい意味」を分離。  
- **KMeans①**：大枠の群れ（トピック）を把握。  
- **ICA②（重心再分解）**：クラスタ重心周りの軸を再構成して**輪郭を明確化**。  
- **KMeans②**：最終クラスタを確定。  
この **二段階 ICA** により、単段階よりも**解釈しやすい軸**と**安定したクラスタ**が得られやすく、マーケ活用（命名・要約・示唆抽出）で効きます。

---

## ライセンス / 作者

- **License**：PVM License v1.2（詳細は LICENSE / LICENSE_FAQ.md を参照）
- **Author**：AI-NOSUKE（透明ペインター / Phantom Color Painter）

