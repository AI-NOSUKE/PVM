# PVM (Phantom Vector Mapping)
![python](https://img.shields.io/badge/python-3.13%20%7C%203.14-blue)
![license](https://img.shields.io/badge/License-PVM%20v1.3-source--available-orange)
![ci](https://github.com/AI-NOSUKE/PVM/actions/workflows/ci.yml/badge.svg)

> **License:** 本リポジトリはソースコードを公開していますが、OSSではありません。非商用の個人利用・学術研究・教育・条件内のPoCは無償です。商用利用には事前の有償ライセンス契約が必要で、年額75万円／200万円／400万円のプランがあります。コードおよび改変版の再配布は禁止しています。詳細は[LICENSE](./LICENSE)を確認してください。

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

このアプローチは、**解釈しやすい意味軸**と**再利用しやすいクラスタ**を得ることを狙っています。効果はデータごとに異なるため、出力スコアと代表文の両方で確認してください。

> ※ ベクトル化（embedding）はPVM手法そのものには含まれませんが、処理の前提ステップとして 0. に記載しています。

## PVM Standard 6.x

PVM 6.0.0 で、自由回答の意味空間生成を Standard PVM に刷新しました。

従来の `full_pvm` は `PCA → ICA① → 全文書 second-ICA(k−1)` を使っていましたが、6.0.0 以降は以下を標準PVMとします。

```text
Embedding
→ PCA
→ ICA①
→ Cluster①
→ Centroid Projection
→ Cluster②
```

操作方法は従来とほぼ同じです。schema 2.0 baseline は警告付きで読み込み可能ですが、追加された ICA① 空間 gate は使われません。重要なプロジェクトでは v6.2.x / schema 2.1 でbaselineを再作成してください。

PVM Standard 6.x の要点:

- 新標準は `Embedding → PCA → ICA① → Cluster① → Centroid Projection → Cluster②` です。
- **v6.2.3 が現行版**です。計算処理とbaseline schema 2.1はv6.2.2から変更せず、PVM License v1.3で非商用利用・商用利用・再配布・PoCの境界を明確化しました。
- ICA①は意味軸、Centroid Projection後の座標はクラスタリング・lock用の境界整理空間です。初回baselineでは `ICA軸レポート.md` を既定出力し、両者を分けて確認できます。
- `--search-budget fast|standard|thorough` で探索コストを選べます。通常は `standard`、埋め込み後の探索を短縮したい場合は `fast` を使います。
- exact検証に失敗したseedは混在次元のARIへ入れず記録します。完全版が成立しない場合だけ従来のICA/PCA退避経路へ進み、`selection_tier=degraded` として明示します。
- v6.1.1 は v6.1.0 に対する unlock再保存バグ修正でした。
- v6.2.3 の `SCRIPT_VERSION` は `PVM-standard-6.2.3`、`SCHEMA_VERSION` は `2.1` です。既存のschema 2.1 baselineはそのまま利用できます。
- v6.1.0 では候補選定の主指標を全候補共通の PCA L2 評価空間で計算し、射影後空間の指標は診断用に分離しています。
- schema 2.0 baseline は警告付きで読み込み可能です。この場合、追加された ICA① 空間 gate は使わず、従来通り final空間 gate のみで動作します。
- 評価では内部指標だけに依存せず、安定性、holdoutへのlock適用、クラスタ解釈の一貫性を確認します。
- [PVM Standard 6.2.3 Release Notes](./RELEASE_v6.2.3.md)
- [PVM Standard 6.2.2 Release Notes](./RELEASE_v6.2.2.md)
- [PVM Standard 6.2.1 Release Notes](./RELEASE_v6.2.1.md)
- [PVM Standard 6.2.0 Release Notes](./RELEASE_v6.2.0.md)
- [PVM Standard 6.1.2 Release Notes](./RELEASE_v6.1.2.md)
- [PVM Standard 6.1.0 Release Notes](./RELEASE_v6.1.0.md)
- [PVM Standard 6.0.0 Release Notes](./RELEASE_v6.0.0.md)

👉 サンプルレポート（PVMによるWebテキスト分類と、クラスタロックを用いた比較分析の実例）  
- ももクロ関連コメント分析: [docs/momoclo_report.md](docs/momoclo_report.md)  
- メンバー別比較分析（クラスタロック活用）: [docs/momoclo_memberBreakDownreport.md](docs/momoclo_memberBreakDownreport.md)

## ⚙️ この実装（ローカル動作＆日本語特化）

このGitHubリポジトリでは、PVM手法を日本語テキスト向けに実装したPythonコードを提供しています。  
使用している日本語埋め込みモデルは `cl-nagoya/ruri-v3-310m`（Hugging Face Transformersベース）です。

初回実行時のモデル取得後は、埋め込み・次元圧縮・クラスタリング・解釈補助をローカルで実行できます。
入力本文を外部APIへ送信する処理はありません。初回のモデルと依存パッケージの取得にはネットワーク通信が必要です。

Ruri v3 のクラスタリング用途に合わせ、既定では各テキストに `トピック: ` prefix を内部的に付与して embedding します。入力ファイルや出力CSV、AI向けパケットの原文には prefix は混ざりません。


## 🔒 入力本文を外部APIへ送信しないローカル処理

- PVMの埋め込み・クラスタリング処理は、入力本文を外部APIへ送信しません
- **外部APIキーは不要**で、モデルがキャッシュ済みであればオフライン実行できます
- 初回のみモデル（1GB超）をインターネット経由で取得しますが、以降は**ローカルキャッシュを使用**

> 機密データを扱う場合は、この通信仕様に加え、利用端末のアクセス制御、入力・出力ファイルの保管、使用するモデルと依存パッケージのライセンスも確認してください。


---

## 目次
- [目的と特徴](#目的と特徴)
- [PVM Standard 6.x](#pvm-standard-6x)
- [インストール（ローカル）](#インストールローカル)
- [クイックスタート](#クイックスタート)
  - [① 動作確認サンプル](#-動作確認サンプル)
  - [② ローカル利用（最小コマンド）](#-ローカル利用最小コマンド)
- [単一ファイル設計](#単一ファイル設計)
- [主なオプション（基本）](#主なオプション基本)
- [補助オプション（その他）](#補助オプションその他)
- [出力ファイル](#出力ファイル)
- [運用の目安と再現性](#運用の目安と再現性)
- [評価上の注意](#評価上の注意)
- [ベンチマーク方針](#ベンチマーク方針)
- [学術背景（要点）](#学術背景要点)
- [ライセンス / 作者](#ライセンス--作者)

---

## 目的と特徴
- **目的**：実務で「意味が取り出しやすいクラスタ」を安定して得る。
- **特徴**：
  - **Standard PVM 6.x**：PCA後のICA①空間で暫定クラスタを作り、クラスタ重心差にもとづく centroid projection で最終意味空間へ再構成する。
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
.\.venv\Scripts\Activate.ps1 # Windows PowerShell
# source .venv/bin/activate # macOS/Linux
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```
- 推奨環境は Python 3.14 です。互換性確認として、CIでは Python 3.13 / 3.14 の両方で依存関係のインストール、単体テスト、`py_compile`、`--version`、`--self-check` を実行しています。初回はRuri v3モデル（1GB超）をダウンロードするため、ネットワーク接続が必要で時間がかかることがあります。
- 日本語Windowsで`PVM.py`を直接実行すると、必要な場合だけPythonをUTF-8モードで自動的に起動し直します。通常の実行コマンドを変更する必要はありません。別のPythonスクリプトから`import PVM`してRuri埋め込みを使う場合は、呼び出し元を`python -X utf8 your_script.py ...`で起動してください。
- Python 3.14対応に伴い依存ライブラリを更新しています。PVM Standard 6.0.0のアルゴリズム仕様は維持していますが、旧依存環境で作成したbaselineと完全な数値一致を保証するものではありません。重要なプロジェクトでは、Python 3.14環境でbaselineを再作成することを推奨します。

---

## クイックスタート

### ① 動作確認サンプル

CIでは Python 3.13 / 3.14 の両方で単体テスト / `py_compile` / `--version` / `--self-check` を検証しています。<br>
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

`examples/sample_texts.csv`は50件の動作確認用データです。処理経路と出力の確認には使えますが、クラスタ品質の判定には小さすぎます。`degraded`やbaseline見直しの警告が出ても、サンプルでの動作確認自体が失敗したという意味ではありません。実データでは100件以上を目安にしてください。

この実行で作られる`baseline_sample_texts`は、project名が`sample_texts`の実行だけで自動読込されます。別の入力ファイルや別の`--project`で始める実データ分析には自動適用されません。意図的に別名baselineを使う場合だけ`--baseline-from sample_texts`のように指定します。

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
22:47:00 [INFO] [OCHIBI] 使用する列: テキスト列="text"（ID列なし・自動付番）
22:47:00 [INFO] [OCHIBI] データ件数: 100
22:50:05 [INFO] [OCHIBI] 埋め込み開始: 件数=100, batch=8, max_len=8192, prefix='トピック: ', device=cpu
22:51:06 [INFO] [OCHIBI] 初回（自動基準作成）: ベスト Plan を自動採用して baseline を作成します。
23:34:32 [INFO] [OCHIBI] スコア出力: PVMresult/run_プロジェクト名_01/結果スコア.csv
23:34:32 [INFO] [OCHIBI] AI向け依頼を出力: PVMresult/run_プロジェクト名_01/AI_解釈依頼.md
23:34:32 [INFO] [OCHIBI] baseline 作成/更新: PVMresult/baseline_プロジェクト名/history/v001
```
</details>

---

## 単一ファイル設計

`PVM.py` は、意図的に single-file local CLI として維持しています。これは未整理だからではなく、実務でのローカル利用、機密データの外部送信回避、導入の簡単さ、配置と監査のしやすさを優先するための設計判断です。

現行の `PVM.py` は単一ファイルで、内部には embedding、transform、clustering、evaluation、baseline/history、lock/unlock、CLI の責務が含まれます。一般的なライブラリ設計であれば分割対象になり得ますが、PVMの標準配布形態では「1ファイルで完結し、ローカルで確認・実行できる」ことを重視しています。

将来的にライブラリ化、PyPI化、モジュール分割を検討する余地はあります。ただし、現時点の標準は single-file CLI であり、PVM Standard 6.x でもこの方針を維持します。

---

## 主なオプション（基本）

| オプション | 説明 | デフォルト値 |
|---|---|---|
| *(無指定)* | 既存の基準でロック適用。**初回は自動採用で基準作成** | - |
| `--show-candidates` | 候補のみ出力（基準は作らない） | - |
| `--use-plan N` | 候補の **rank=N** を採用して基準作成。rank=1が最良 | 未指定（無指定実行では最良Planを自動採用） |
| `--unlock` | 柔軟適用：新話題を追加クラスタで吸収 | - |
| `--baseline-from NAME` | 他プロジェクトの基準を流用してロック/アンロック | - |
| `--input_csv PATH` / `--input_xlsx PATH` | 入力データの指定 | 自動検出※ |
| `--text_col NAME` | テキスト列名 | 自動検出※※ |
| `--project NAME` | 分析とbaselineを識別する名前（例：`顧客アンケート`）。同じbaselineで初回・lock・unlockを行う間は同じ名前を使う | 入力ファイル名 |

> ※ 入力ファイル未指定時：`入力.xlsx` / `入力.csv` を優先、なければ最新のExcel/CSVを使用  
> ※※ テキスト列未指定時：`text` / `テキスト` / `本文` などを優先、なければ最長列を使用

---

## 補助オプション（その他）

| オプション | 説明 | デフォルト値 |
|---|---|---|
| `--id_col NAME` | ID列名（任意） | 内部で自動付番 |
| `--unlock-q Q` | 新話題検出の距離分位点（0<Q<1）。未指定時はbaseline保存値を引き継ぐ | baseline保存値（初回 0.95） |
| `--unlock-add-k K` | 追加クラスタの上限 | 2 |
| `--unlock-min-points N` | unlock時に新クラスタ候補として扱う最小件数 | 8 |
| `--baseline-version vXXX` | lock / unlock 時に使用する baseline version を明示 | 最新版 |
| `--restore-version vXXX` | 指定 version を復元保存して終了 | - |
| `--search-budget MODE` | 初回探索の計算予算（`fast` / `standard` / `thorough`） | standard |
| `--include-ica1-cols` | `結果スコア.csv` にICA①座標も追加（通常の意味軸確認は既定のレポートで可能） | なし |
| `--max-cp-cols N` | `結果スコア.csv` に出力する最終座標列の上限（旧 `--max_ic_cols` も利用可） | 全て |
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

- `結果スコア.csv` … 各テキストのクラスタ割当・距離・最終座標。完全版では `CP1...`、`--include-ica1-cols` 指定時は `ICA1_1...` も追加
- `ICA軸レポート.md` … ICA①の代表軸と正負の極端文。意味軸をCP後の座標と混同せず確認するため初回baselineで既定出力
- `結果レポート.json` … 実行情報・採用 Plan などのメタ情報  
- `AI_解釈依頼.md` … クラスタ解釈・命名をAIに依頼するための代表文パケット
- `AI_クラスタ一覧.csv` … クラスタごとの要約一覧
- `k_candidates.csv` … exact検証した候補一覧。canonical次元、seed成功数、ICA軸診断、CPの変更率・境界集中度、`selection_tier` を含む
- `k_candidates_stage2.csv` … 候補探索で上位になったPlan TOP5の比較（`ica1_dim` / CP後次元を表す互換フィールド `ic2_dim` / `k` / 各指標）
- `k_candidates_assignments.csv` … 各候補での全テキストの割当情報  
- `baseline_プロジェクト名/` … 基準情報（history でバージョン管理）

---

## 運用の目安と再現性

- **データ件数**：最低3件以上必要、30件以下では候補探索が粗くなります。十分な件数（100件以上）を推奨。  
- **初回の自動採用**：無指定で走らせると自動採用で基準作成。意図を固定したい場合は `--show-candidates` → `--use-plan N` を明示。  
- **再現性**：`--random_state` の固定 + **baselineロック** 運用を推奨。  
- **project名**：同じ分析の初回・lock・unlockでは同じ`--project`を使います。別の分析だけ別名にしてください。実行回ごとに`1回目`、`2回目`と変える用途ではありません。
- **旧baseline互換性**：schema 2.0 baseline は警告付きで読み込み可能ですが、ICA① 空間 gate は使われません。schema 1.1 など旧baselineは再作成してください。
- **baselineの選択**：`--baseline-from`を指定した場合はそのbaseline、未指定なら現在projectと同名のbaselineだけを使います。別名baselineは1系列だけでも自動採用しません。
- **モデル依存**：埋め込みモデル、embedding prefix、max_len を変えると軸解釈が変わります。一貫性のため同一設定での運用を推奨。
- **プロジェクト分離**：異なる分析は `--project` で分けることで、基準の混在を防げます。

---

## 評価上の注意

PVM v6.1.0以降では、候補選定に使う silhouette、Calinski-Harabasz、Davies-Bouldin などの内部指標は、Centroid Projection後の候補固有空間ではなく、全候補で共通の評価空間（PCA後の `X_eval = l2_normalize(Xp)`）で計算します。フィールド名は `silhouette_eval_space`、`ch_eval_space`、`db_eval_space` のように空間が分かる形にしています。

射影後空間の `silhouette_projected_space`、`ch_projected_space`、`db_projected_space` は、解釈・表示・診断用の指標です。Centroid Projection は Cluster① から学習されるため、射影後指標を候補選定の品質証拠や外部妥当性の証明としては扱いません。v6.1.0で silhouette 値が旧レポートより低く出る場合がありますが、これは劣化ではなく、射影後空間で膨らんでいた値を共通評価空間で保守的に測るためです。

PCA/ICA①の圧縮空間の外に完全に乗る新話題は、ICA① pre-projection gateでも原理的に検出できません。これは次元圧縮を使う手法の一般的限界であり、実運用ではholdout/定期的なbaseline reviewで補います。

v6.1.1以降の novelty gate は final空間 gate と ICA① pre-projection gate のORで判定するため、学習データ自身をlockした場合でも `gate_over_rate` は従来より高く出ることがあります。これは検出感度を上げた結果であり、ただちに品質劣化を意味しません。baseline reviewでは `gate_final_only_count` / `gate_ica1_only_count` / `gate_both_count` を分けて確認してください。

PVMの評価では、内部指標だけでなく、次の観点を併用します。

- **安定性**：再標本化や `--random_state` 変更に対して、クラスタ構造や代表文が大きく崩れないかを確認する。
- **固定運用性**：holdoutデータや別時点データにbaseline lockを適用し、既存クラスタへの割当が解釈可能に保たれるかを見る。
- **意味的一貫性**：人手またはLLMにより、クラスタ内の代表文・境界例・命名が一貫しているかを評価する。

PVMは「正解ラベル再現器」ではありません。自由回答の意味構造を可視化し、一度解釈したクラスタ体系をbaselineとして固定運用するための実務向けパイプラインです。

具体的な評価手順は [docs/evaluation_protocol.md](docs/evaluation_protocol.md) にまとめています。このプロトコルは今後の検証手順であり、現時点でPVMが他手法より優位であることを断言するものではありません。

---

## ベンチマーク方針

PVM Standard 6.x（現行版6.2.3）の有効性は、同じ入力データと同じembedding条件のもとで、複数の比較対象と並べて検証します。少なくとも以下を比較対象とします。

- embedding + spherical k-means
- PCA → ICA① + spherical k-means
- PVM Standard 6.xの現行版
- 必要に応じてBERTopic等の既存トピックモデリング手法

比較では、内部指標の順位だけでなく、seed変更時の安定性、holdoutへのlock適用、クラスタ名の付けやすさ、代表文の読みやすさ、実務上の再利用しやすさを合わせて確認します。

評価プロトコルは [docs/evaluation_protocol.md](docs/evaluation_protocol.md) を参照してください。

---

## 学術背景（要点）

- **ICA①**：PCA後の空間から、意味的に独立しやすい成分を抽出。
- **Cluster①**：ICA①空間で暫定クラスタを作り、クラスタ重心を求める。
- **Centroid Projection**：クラスタ重心の全体平均からの差分にSVDをかけ、クラスタ間の違いが大きい between-class 方向へ射影。
- **spherical k-means**：L2正規化した最終空間で、cosine 距離に基づいてクラスタを確定。
- **候補評価**：複数の ICA① 次元・クラスタ数を探索し、全候補共通の PCA L2 評価空間で分離指標を計算する。Centroid Projection後の指標は診断用として分離し、Plan選定の主証拠にはしない。

この **ICA① + centroid projection + spherical k-means** は、単段階よりも**解釈しやすい軸**と**運用しやすいクラスタ**を得ることを狙った構成です。効果はデータごとに異なるため、内部指標だけで決めず、seed安定性、holdout lock、代表文の読みやすさを併せて確認します。

---

## ライセンス / 作者

- **License**：PVM License v1.3（source-available、非OSS）
  - 非商用の個人利用・学術研究・教育、および条件内のPoCのみ無償です。
  - 個人事業、副業、収益化、社内業務、受託・顧客提供を含む商用利用には、事前の有償ライセンス契約が必要です。
  - 年額は、社内利用75万円／顧客への成果物を含む外部提供200万円／クラスタロック付き外部提供400万円です。
  - コードおよび改変版の再配布は禁止しています。
  - 詳細は [LICENSE](./LICENSE) / [利用条件FAQ](./docs/USAGE_FAQ.md) を参照してください。
- **Author**：AI-NOSUKE（透明ペインター / Phantom Color Painter）
