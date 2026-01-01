# PVM (Phantom Vector Mapping)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/License-PVM%20v1.2-green)
![release](https://img.shields.io/github/v/release/AI-NOSUKE/PVM?color=orange)
![ci](https://github.com/AI-NOSUKE/PVM/actions/workflows/ci.yml/badge.svg)

## 🔰 概要（PVMとは）

**PVM（Phantom Vector Mapping）**は安定して解釈しやすいクラスタを得るための**二段階クラスタリング手法**です。  
テキストをベクトル化（embedding）した上で、意味的な独立軸を抽出し、安定的なクラスタ構造を導きます。

主な構成は以下の通りです：

0. **テキストのベクトル化（embedding：埋め込み）**  
　└ 任意の埋め込みモデルを利用可能ですが、本実装では日本語特化の `ruri-v3-310m` を使用  
1. **PCA による次元圧縮**  
2. **ICA① による独立成分の抽出**  
3. **KMeans① による初期クラスタリング**  
4. **セントロイドの平均ベクトルで再度 ICA（ICA②）**  
5. **KMeans② によるクラスタの確定**

このアプローチにより、**意味軸の抽出が容易**となり、**解釈性と再現性に優れたクラスタリング**が可能となります。

> ※ ベクトル化（embedding）はPVM手法そのものには含まれませんが、処理の前提ステップとして 0. に記載しています。

👉 サンプルレポート（PVMによるWebテキスト分類と、クラスタロックを用いた比較分析の実例）  
- ももクロ関連コメント分析（前編）: [docs/momoclo_report.md](docs/momoclo_report.md)  
- メンバー別内訳分析（クラスタロック）: [docs/momoclo_memberBreakDownreport.md](docs/momoclo_memberBreakDownreport.md)

## ⚙️ この実装（ローカル動作＆日本語特化）

このGitHubリポジトリでは、PVM手法を日本語テキスト向けに実装したPythonコードを提供しています。  
使用している日本語埋め込みモデルは `cl-nagoya/ruri-v3-310m`（Hugging Face Transformersベース）です。

このコードは、**初回実行時にモデルをダウンロード**する以外は、**完全にローカル環境で実行可能**です。  
すべての処理（埋め込み・次元圧縮・クラスタリング・命名補助など）はローカルで行われ、**外部APIや通信は不要**です。


## 🔒 セキュアでローカル完結（この実装の特長）

- テキストデータや個人情報を**外部サーバーへ送信することは一切ありません**  
- **外部APIキー不要**、オフライン環境でも再現可能  
- 初回のみモデル（約440MB）をインターネット経由で取得しますが、以降は**すべてローカル処理**

> このため、**実務データ・研究データ・社内資料など機密性の高いデータ**でも、セキュアに扱うことができます。


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
  - **Cluster Lock**：解釈済みクラスタをロックして、別データへの再適用や比較分析が可能。  
  - **再現配慮**：乱数シード・baselineロック・ログ出力で運用を安定化。

> 💡初回を **無指定で実行**すると、自動でベスト Plan が採用され基準が作成されます。  
> 　 意図を明示したい場合は `--show-candidates → --use-plan N` を推奨。

---

## インストール（ローカル）

```powershell
git clone https://github.com/AI-NOSUKE/PVM.git
cd PVM
python -m venv .venv
.\.venv\Scripts\activate    # Windows
# source .venv/bin/activate # macOS/Linux
pip install -r requirements.txt
```
- Python 3.11+ 推奨。初回は埋め込みモデル取得で少し時間がかかることがあります。

---

## クイックスタート

### ① CIサンプル（必ず通る最小テスト）

固定データ（`examples/sample_texts.csv`）で検証しています。

```powershell
# 候補探索
python PVM.py --input_csv examples/sample_texts.csv --show-candidates

# Plan採用（例：rank=1）
python PVM.py --input_csv examples/sample_texts.csv --use-plan 1

# 2回目以降はロック適用
python PVM.py --input_csv examples/sample_texts.csv

# 柔軟適用（新話題の吸収）
python PVM.py --input_csv examples/sample_texts.csv --unlock
```

補足：アンロックは既存基準に投影し、基準から遠い集合だけを外れ値とみなして  
その中で最大 `--unlock-add-k` 個まで新クラスタを追加します。

---

### ② ローカル利用（最小コマンド）

入力ファイル未指定時は自動検出されます（`入力.xlsx` / `入力.csv` / 最新のExcel・CSVファイル）。  
あなたのCSVのテキスト列名が **`text`** なら `--text_col` も不要です。

```powershell
# 候補探索
python PVM.py --show-candidates

# Plan採用（例：rank=1）
python PVM.py --use-plan 1

# 2回目以降（ロック適用）
python PVM.py

# 柔軟適用（アンロック）
python PVM.py --unlock
```

👉 サンプルCSVはこちら：[examples/sample_texts.csv](examples/sample_texts.csv)

<details>
<summary><b>参考: 実行ログの例（クリックで展開）</b></summary>

```text
INFO PVM: embedding model = cl-nagoya/ruri-v3-310m
INFO PVM: candidates search k in [3..12], seed=42
INFO PVM: stage-2 compare TOP5 → results written to PVMresult/k_candidates_stage2.csv
INFO PVM: global plan rank=1 selected → baseline saved to PVMresult/baseline_1回目/
INFO PVM: locked apply done → results written to PVMresult/結果スコア.csv
```
</details>

---

## 主なオプション（基本）

| オプション | 説明 | デフォルト値 |
|---|---|---|
| *(無指定)* | 既存の基準でロック適用。**初回は自動採用で基準作成** | - |
| `--show-candidates` | 候補のみ出力（基準は作らない） | - |
| `--use-plan N` | 候補の **rank=N** を採用して基準作成 | 1（最良） |
| `--unlock` | 柔軟適用：新話題を追加クラスタで吸収 | - |
| `--baseline-from NAME` | 他プロジェクトの基準を流用してロック/アンロック | - |
| `--input_csv PATH` / `--input_xlsx PATH` | 入力データの指定 | 自動検出※ |
| `--text_col NAME` | テキスト列名 | 自動検出※※ |
| `--project NAME` | 出力の保存先名（例：`1回目` / `2回目`） | 入力ファイル名 |

> ※ 入力ファイル未指定時：`入力.xlsx` / `入力.csv` を優先、なければ最新のExcel/CSVを使用  
> ※※ テキスト列未指定時：`text` / `テキスト` / `本文` などを優先、なければ最長列を使用

---

## 補助オプション（その他）

| オプション | 説明 | デフォルト値 |
|---|---|---|
| `--id_col NAME` | ID列名（任意） | 内部で自動付番 |
| `--unlock-q Q` | 新話題検出の距離分位点（0<Q<1） | 0.90 |
| `--unlock-add-k K` | 追加クラスタの上限 | 2 |
| `--max_ic_cols N` | `結果スコア.csv` に出力する IC 列の上限 | 全て |
| `--k_min N` / `--k_max N` | 候補探索の K 範囲 | 3 / 12 |
| `--embedding_model NAME` | 埋め込みモデル | cl-nagoya/ruri-v3-310m |
| `--batch N` / `--max_len N` | 埋め込みのバッチサイズ/最大長 | 16 / 384 |
| `--pca_var R` | PCA の累積寄与率 | 0.90 |
| `--random_state S` | 乱数シード | 42 |
| `--log_level LEVEL` | ログレベル（INFO/DEBUG など） | INFO |
| **日本語alias** | `--候補表示` / `--採用プラン` / `--柔軟適用` / `--基準流用` など | - |

---

## 出力ファイル

代表的な成果物（プロジェクトごとに `PVMresult/` 以下へ保存）：

- `結果スコア.csv` … 各テキストのクラスタ割当・距離・IC 指標  
- `結果レポート.json` … 実行情報・採用 Plan などのメタ情報  
- `AI_命名依頼.md` … クラスタ命名依頼テンプレ（代表文リスト付き）  
- `k_candidates.csv` … 全候補一覧（d × K の組み合わせ評価）  
- `k_candidates_stage2.csv` … 二段階後の候補比較（d* 固定で K のみ変更した TOP5）  
- `k_candidates_assignments.csv` … 各候補での全テキストの割当情報  
- `baseline_プロジェクト名/` … 基準情報（history でバージョン管理）

---

## 運用の目安と再現性

- **データ件数**：最低3件以上必要、30件以下では候補探索が粗くなります。十分な件数（100件以上）を推奨。  
- **初回の自動採用**：無指定で走らせると自動採用で基準作成。意図を固定したい場合は `--show-candidates` → `--use-plan N` を明示。  
- **再現性**：`--random_state` の固定 + **baselineロック** 運用を推奨。  
- **baselineの優先度**：`--baseline-from` ＞ 現在プロジェクトの baseline ＞ワークスペース内の最新 baseline_* が自動的に選択されます。
- **モデル依存**：埋め込みモデルを変えると軸解釈が変わります。一貫性のため同一モデルでの運用を推奨。  
- **プロジェクト分離**：異なる分析は `--project` で分けることで、基準の混在を防げます。

---

## 学術背景（要点）

- **ICA①**：潜在因子を独立化して「混じって見えにくい意味」を分離。  
- **KMeans①**：大枠の群れ（トピック）を把握。  
- **ICA②（重心再分解）**：クラスタ重心周りの軸を再構成して**輪郭を明確化**。  
- **KMeans②**：最終クラスタを確定。  

この **二段階 ICA** により、単段階よりも**解釈しやすい軸**と**安定したクラスタ**が得られやすく、マーケティング活用（命名・要約・示唆抽出）で効果を発揮します。

---

## ライセンス / 作者

- **License**：PVM License v1.2（詳細は `LICENSE` / `LICENSE_FAQ.md` を参照）
- **Author**：AI-NOSUKE（透明ペインター / Phantom Color Painter）

