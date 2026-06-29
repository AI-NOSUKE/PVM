# PVM (Phantom Vector Mapping)
![python](https://img.shields.io/badge/python-3.14-blue)
![license](https://img.shields.io/badge/License-PVM%20v1.2-green)
![ci](https://github.com/AI-NOSUKE/PVM/actions/workflows/ci.yml/badge.svg)

## 🔰 概要（PVMとは）

**PVM（Phantom Vector Mapping）**は、embedding 後のテキストを PCA、ICA①、クラスタ重心にもとづく centroid projection で意味空間に再構成し、spherical k-means で安定して解釈しやすいクラスタを得る手法です。
テキストをベクトル化（embedding）した上で、意味的な独立軸とクラスタ間方向を抽出し、cosine 距離に基づく安定的なクラスタ構造を導きます。

主な構成は以下の通りです：

0. **テキストのベクトル化（embedding：埋め込み）**<br>
　└ 本実装では日本語特化の `cl-nagoya/ruri-v3-310m` を使用
1. **PCA による次元圧縮**  
2. **ICA① による独立成分の抽出**  
3. **Cluster① による暫定クラスタ作成**<br>
　└ ICA①空間でクラスタ重心を求める
4. **Centroid Projection による最終意味空間の再構成**<br>
　└ クラスタ重心の差分が張る between-class 方向へ射影
5. **Cluster② / spherical k-means によるクラスタ確定**<br>
　└ cosine 距離に対応するよう、ベクトルとセントロイドを正規化して割り当て
6. **候補評価と baseline 保存**<br>
　└ 複数の ICA① 次元・クラスタ数を比較し、最良 Plan を baseline として保存

このアプローチにより、**意味軸の抽出が容易**となり、**解釈性と再現性に優れたクラスタリング**が可能となります。

> ※ ベクトル化（embedding）はPVM手法そのものには含まれませんが、処理の前提ステップとして 0. に記載しています。

## PVM Standard 6.0.0

PVM 6.0.0 では、自由回答の意味空間生成を Standard PVM に刷新しました。

従来の `full_pvm` は `PCA → ICA① → 全文書ICA②(k−1)` を使っていましたが、6.0.0 以降は以下を標準PVMとします。

```text
Embedding
→ PCA
→ ICA①
→ Cluster①
→ Centroid Projection
→ Cluster②
```

操作方法は従来とほぼ同じですが、旧baselineとは互換性がありません。既存プロジェクトは6.0.0でbaselineを再作成してください。

v6.0.0 release の要点:

- 新標準は `Embedding → PCA → ICA① → Cluster① → Centroid Projection → Cluster②` です。
- `SCHEMA_VERSION` は `2.0` です。
- 評価では内部指標だけに依存せず、安定性、holdoutへのlock適用、クラスタ解釈の一貫性を確認します。
- release本文案は [RELEASE_v6.0.0.md](RELEASE_v6.0.0.md) にあります。

👉 サンプルレポート（PVMによるWebテキスト分類と、クラスタロックを用いた比較分析の実例）  
- ももクロ関連コメント分析: [docs/momoclo_report.md](docs/momoclo_report.md)  
- メンバー別比較分析（クラスタロック活用）: [docs/momoclo_memberBreakDownreport.md](docs/momoclo_memberBreakDownreport.md)

## ⚙️ この実装（ローカル動作＆日本語特化）

このGitHubリポジトリでは、PVM手法を日本語テキスト向けに実装したPythonコードを提供しています。  
使用している日本語埋め込みモデルは `cl-nagoya/ruri-v3-310m`（Hugging Face Transformersベース）です。

このコードは、**初回実行時にモデルをダウンロード**する以外は、**完全にローカル環境で実行可能**です。  
すべての処理（埋め込み・次元圧縮・クラスタリング・解釈補助など）はローカルで行われ、**外部APIや通信は不要**です。

Ruri v3 のクラスタリング用途に合わせ、既定では各テキストに `トピック: ` prefix を内部的に付与して embedding します。入力ファイルや出力CSV、AI向けパケットの原文には prefix は混ざりません。


## 🔒 セキュアでローカル完結（この実装の特長）

- テキストデータや個人情報を**外部サーバーへ送信することは一切ありません**  
- **外部APIキー不要**、オフライン環境でも再現可能  
- 初回のみモデル（1GB超）をインターネット経由で取得しますが、以降は**ローカルキャッシュを使用**

> このため、**実務データ・研究データ・社内資料など機密性の高いデータ**でも、セキュアに扱うことができます。


---

## 目次
- [概要](#概要)
- [PVM Standard 6.0.0](#pvm-standard-600)
- [インストール（ローカル）](#インストールローカル)
- [クイックスタート](#クイックスタート)
  - [① 動作確認サンプル](#①-動作確認サンプル)
  - [② ローカル利用（最小コマンド）](#②-ローカル利用最小コマンド)
- [単一ファイル設計](#単一ファイル設計)
- [主なオプション（基本）](#主なオプション基本)
- [補助オプション（その他）](#補助オプションその他)
- [出力ファイル](#出力ファイル)
- [運用の目安と再現性](#運用の目安と再現性)
- [評価上の注意](#評価上の注意)
- [今後のベンチマーク方針](#今後のベンチマーク方針)
- [学術背景（要点）](#学術背景要点)
- [ライセンス / 作者](#ライセンス--作者)

---

## 概要
- **目的**：実務で「意味が取り出しやすいクラスタ」を安定して得る。
- **特徴**：
  - **Standard PVM 6.0.0**：PCA後のICA①空間で暫定クラスタを作り、クラスタ重心差にもとづく centroid projection で最終意味空間へ再構成する。
  - **候補→採用→ロック/アンロック**の一連フローをコマンドで直感操作。  
  - **Cluster Lock**：解釈済みクラスタをロックして、別データへの再適用や比較分析が可能。  
  - **再現配慮**：乱数シード・baselineロック・ログ出力で運用を安定化。

> 💡初回を **無指定で実行**すると、自動でベスト Plan が採用され基準が作成されます。  
> 　 意図を明示したい場合は `--show-candidates` → `--use-plan N` も利用できます。

---

## インストール（ローカル）

```powershell
git clone https://github.com/AI-NOSUKE/PVM.git
cd PVM
py -3.14 -m venv .venv   # Windows
# python3.14 -m venv .venv # macOS/Linux
.\.venv\Scripts\activate    # Windows
# source .venv/bin/activate # macOS/Linux
pip install -r requirements.txt
```
- Python 3.14 推奨。初回は埋め込みモデル取得で少し時間がかかることがあります。
- Python 3.14対応に伴い依存ライブラリを更新しています。PVM Standard 6.0.0のアルゴリズム仕様は維持していますが、旧依存環境で作成したbaselineと完全な数値一致を保証するものではありません。重要なプロジェクトでは、Python 3.14環境でbaselineを再作成することを推奨します。

---

## クイックスタート

### ① 動作確認サンプル

CIでは `py_compile` / `--version` / `--self-check` を検証しています。<br>
以下は、同梱サンプル `examples/sample_texts.csv` を使ってローカルで実行できる最小例です。

```bash
# 初回：自動で候補探索し、ベストPlanを採用してbaselineを作成
python PVM.py --input_csv examples/sample_texts.csv

# 2回目以降はロック適用
python PVM.py --input_csv examples/sample_texts.csv

# 柔軟適用（新話題の吸収）
python PVM.py --input_csv examples/sample_texts.csv --unlock

# 候補だけ確認したい場合
python PVM.py --input_csv examples/sample_texts.csv --show-candidates

# 候補から明示採用したい場合
python PVM.py --input_csv examples/sample_texts.csv --use-plan 1
```

インストール後の軽量チェック（埋め込みモデル不要）:

```bash
python PVM.py --version
python PVM.py --self-check
```

補足：アンロックは既存基準に投影し、基準から遠い集合だけを外れ値とみなして  
その中で最大 `--unlock-add-k` 個まで新クラスタを追加します。

---

### ② ローカル利用（最小コマンド）

入力ファイル未指定時は自動検出されます（`入力.xlsx` / `入力.csv` / 最新のExcel・CSVファイル）。  
あなたのCSVのテキスト列名が **`text`** なら `--text_col` も不要です。

```powershell
# 初回：自動で候補探索し、ベストPlanを採用してbaselineを作成
python PVM.py

# 2回目以降（ロック適用）
python PVM.py

# 柔軟適用（アンロック）
python PVM.py --unlock

# 候補だけ確認したい場合
python PVM.py --show-candidates

# 候補から明示採用したい場合
python PVM.py --use-plan 1
```

👉 サンプルCSVはこちら：[examples/sample_texts.csv](examples/sample_texts.csv)

<details>
<summary><b>参考: 別データでの実行ログ例（クリックで展開）</b></summary>

```text
22:47:00 [INFO] [OCHIBI] columns: text_col="text"
22:47:00 [INFO] [OCHIBI] データ件数: 100
22:50:05 [INFO] [OCHIBI] Embedding開始: total=100, batch=8, max_len=8192, prefix='トピック: ', device=cpu
22:51:06 [INFO] [OCHIBI] 初回（自動基準作成）: ベスト Plan を自動採用して baseline を作成します。
23:34:32 [INFO] [OCHIBI] スコア出力: PVMresult/run_プロジェクト名_01/結果スコア.csv
23:34:32 [INFO] [OCHIBI] AI向け依頼を出力: PVMresult/run_プロジェクト名_01/AI_解釈依頼.md
23:34:32 [INFO] [OCHIBI] baseline 作成/更新: PVMresult/baseline_プロジェクト名/history/v001
```
</details>

---

## 単一ファイル設計

`PVM.py` は、意図的に single-file local CLI として維持しています。これは未整理だからではなく、実務でのローカル利用、機密データの外部送信回避、導入の簡単さ、監査しやすさ、コピー配布しやすさを優先するための設計判断です。

現行の `PVM.py` は約3200行の単一ファイルで、内部には embedding、transform、clustering、evaluation、baseline/history、lock/unlock、CLI の責務が含まれます。一般的なライブラリ設計であれば分割対象になり得ますが、PVMの標準配布形態では「1ファイルで完結し、ローカルで確認・実行できる」ことを重視しています。

将来的にライブラリ化、PyPI化、モジュール分割を検討する余地はあります。ただし、現時点の標準は single-file CLI であり、PVM Standard 6.0.0 でもこの方針を維持します。

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
| `--unlock-q Q` | 新話題検出の距離分位点（0<Q<1） | 0.95 |
| `--unlock-add-k K` | 追加クラスタの上限 | 2 |
| `--unlock-min-points N` | unlock時に新クラスタ候補として扱う最小件数 | 8 |
| `--baseline-version vXXX` | lock / unlock 時に使用する baseline version を明示 | 最新版 |
| `--restore-version vXXX` | 指定 version を復元保存して終了 | - |
| `--max_ic_cols N` | `結果スコア.csv` に出力する IC 列の上限 | 全て |
| `--k_min N` / `--k_max N` | 候補探索の K 範囲 | 3 / 12 |
| `--embedding_model NAME` | 埋め込みモデル | cl-nagoya/ruri-v3-310m |
| `--embedding-prefix TEXT` | embedding前に付けるprefix。通常変更不要。`none` で空prefix | `トピック: ` |
| `--batch N` / `--max_len N` | 埋め込みのバッチサイズ/最大長 | 8 / 8192 |
| `--pca_var R` | PCA の累積寄与率 | 0.90 |
| `--random_state S` | 乱数シード | 42 |
| `--log_level LEVEL` | ログレベル（INFO/DEBUG など） | INFO |
| **日本語alias** | `--候補表示` / `--採用プラン` / `--柔軟適用` / `--基準流用` など | - |

---

## 出力ファイル

代表的な成果物（プロジェクトごとに `PVMresult/` 以下へ保存）：

- `結果スコア.csv` … 各テキストのクラスタ割当・距離・IC 指標  
- `結果レポート.json` … 実行情報・採用 Plan などのメタ情報  
- `AI_解釈依頼.md` … クラスタ解釈・命名をAIに依頼するための代表文パケット
- `AI_クラスタ一覧.csv` … クラスタごとの要約一覧
- `k_candidates.csv` … 全候補一覧（d × K の組み合わせ評価）  
- `k_candidates_stage2.csv` … 候補探索で上位になったPlan TOP5の比較（ica1_dim / ic2_dim / k / 各指標）
- `k_candidates_assignments.csv` … 各候補での全テキストの割当情報  
- `baseline_プロジェクト名/` … 基準情報（history でバージョン管理）

---

## 運用の目安と再現性

- **データ件数**：最低3件以上必要、30件以下では候補探索が粗くなります。十分な件数（100件以上）を推奨。  
- **初回の自動採用**：無指定で走らせると自動採用で基準作成。意図を固定したい場合は `--show-candidates` → `--use-plan N` を明示。  
- **再現性**：`--random_state` の固定 + **baselineロック** 運用を推奨。  
- **PVM 6.0.0 baseline**：旧baselineとは互換性がありません。既存プロジェクトは新しいbaselineを作成してください。
- **baselineの優先度**：`--baseline-from` ＞ 現在プロジェクトの baseline ＞ フォルダ内に baseline が1系列だけならそれを自動採用。複数ある場合は誤適用を避けるため明示指定が必要です。
- **モデル依存**：埋め込みモデル、embedding prefix、max_len を変えると軸解釈が変わります。一貫性のため同一設定での運用を推奨。
- **プロジェクト分離**：異なる分析は `--project` で分けることで、基準の混在を防げます。

---

## 評価上の注意

PVM Standard 6.0.0 の Centroid Projection は、ICA①空間で作った Cluster① の重心差から学習されます。そのため、投影後空間で計算される silhouette、Calinski-Harabasz、Davies-Bouldin などの内部指標は、候補選定や品質確認には有用ですが、それ単体で手法の外部妥当性を証明するものではありません。

PVMの評価では、内部指標だけでなく、次の観点を併用します。

- **安定性**：再標本化や `--random_state` 変更に対して、クラスタ構造や代表文が大きく崩れないかを確認する。
- **固定運用性**：holdoutデータや別時点データにbaseline lockを適用し、既存クラスタへの割当が解釈可能に保たれるかを見る。
- **意味的一貫性**：人手またはLLMにより、クラスタ内の代表文・境界例・命名が一貫しているかを評価する。

PVMは「正解ラベル再現器」ではありません。自由回答の意味構造を可視化し、一度解釈したクラスタ体系をbaselineとして固定運用するための実務向けパイプラインです。

具体的な評価手順は [docs/evaluation_protocol.md](docs/evaluation_protocol.md) にまとめています。このプロトコルは今後の検証手順であり、現時点でPVMが他手法より優位であることを断言するものではありません。

---

## 今後のベンチマーク方針

PVM Standard 6.0.0 の有効性は、同じ入力データと同じembedding条件のもとで、複数の比較対象と並べて検証します。少なくとも以下をベンチマーク候補とします。

- embedding + spherical k-means
- PCA → ICA① + spherical k-means
- PVM Standard 6.0.0
- 必要に応じてBERTopic等の既存トピックモデリング手法

比較では、内部指標の順位だけでなく、seed変更時の安定性、holdoutへのlock適用、クラスタ名の付けやすさ、代表文の読みやすさ、実務上の再利用しやすさを合わせて確認します。

評価プロトコルは [docs/evaluation_protocol.md](docs/evaluation_protocol.md) を参照してください。

---

## 学術背景（要点）

- **ICA①**：PCA後の空間から、意味的に独立しやすい成分を抽出。
- **Cluster①**：ICA①空間で暫定クラスタを作り、クラスタ重心を求める。
- **Centroid Projection**：クラスタ重心の全体平均からの差分にSVDをかけ、クラスタ間の違いが大きい between-class 方向へ射影。
- **spherical k-means**：L2正規化した最終空間で、cosine 距離に基づいてクラスタを確定。
- **候補評価**：複数の ICA① 次元・クラスタ数を探索し、分離・安定性・バランス・過剰分割ペナルティ等を総合して Plan を選択。

この **ICA① + centroid projection + spherical k-means** により、単段階よりも**解釈しやすい軸**と**安定したクラスタ**が得られやすく、マーケティング活用（命名・要約・示唆抽出）で効果を発揮します。

---

## ライセンス / 作者

- **License**：PVM License v1.2（詳細は `LICENSE` / `LICENSE_FAQ.md` を参照）
- **Author**：AI-NOSUKE（透明ペインター / Phantom Color Painter）
