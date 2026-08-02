# PVM Manual

## 1. 概要
高次元のテキスト埋め込みを **PCA → ICA① → Cluster① → Centroid Projection → Cluster②** で整理します。
初回は候補（k と ICA 次元の組み合わせ）を探索し、ベストPlanを自動採用してbaselineを作成します。
**rank=1 が最良**で、`--use-plan N` の **N にはこの rank 値**を渡します。
2回目以降は既存の基準に基づくロック実行がデフォルトです。`--unlock` で新話題のみを吸収して基準を拡張できます。

現行版は **PVM Standard 6.2.1** です。6.2.0でICA①次元とクラスタ数の探索を拡張し、Centroid Projectionが実際に圧縮した候補と、ICA①の意味軸を確認できる出力を追加しました。6.2.1では日本語WindowsでのRuri読込時に必要なUTF-8モードを、`PVM.py`の直接実行時に自動で扱います。探索、lock/unlock、baseline schema 2.1は6.2.0から変更していません。schema 2.0 baselineも警告付きで読み込めますが、ICA①空間gateは使われません。

## 2. 入力データ
- 既定設定で `python PVM.py` を実行可能（必要に応じてオプションで上書き）。
- 対応形式：Excel（`.xlsx`）/ CSV（UTF-8 推奨）
- 最低限必要なもの：テキスト本文を含む列（列名は`text`推奨）
  - 例：
    | id | text               |
    |----|--------------------|
    | 1  | これはテスト文です |

> 備考：列名が異なる場合は `--text_col` で指定可能。

## 3. インストール
```powershell
git clone https://github.com/AI-NOSUKE/PVM.git
cd PVM
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

macOS/Linuxでは次のコマンドを使います。

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

初回はRuri v3モデル（1GB超）のダウンロードにネットワーク接続と時間が必要です。

## 4. 実行フロー（典型パターン）
### 4.1 初回：自動でbaseline作成
```bash
python PVM.py
```
→ 入力ファイルを自動検出し、候補探索後にベストPlanを自動採用して `baseline_プロジェクト名/history/v001` を保存します。

### 4.2 候補だけ確認したい場合
```bash
python PVM.py --show-candidates
```
→ `k_candidates.csv` / `k_candidates_stage2.csv` / `k_candidates_assignments.csv` を出力します。baselineは更新しません。

### 4.3 候補から明示採用したい場合
```bash
python PVM.py --use-plan 5
```
→ 候補探索結果の `rank=5` のPlanを採用してbaselineを作成/更新します。

### 4.4 2回目以降：ロック / アンロック
```bash
python PVM.py            # ロック（基準固定）
python PVM.py --unlock   # アンロック（新話題のみ追加）
```

## 5. 主なオプション（抜粋）
- `--show-candidates` / `--候補表示`：候補出力のみ（基準は作らない）
- `--use-plan N` / `--採用プラン N`：候補から **rank=N** の案を採用して基準作成
- `--unlock` / `--柔軟適用`：新話題を基準に追加して保存
- `--baseline-from NAME` / `--基準流用 NAME`：他プロジェクトの基準を参照
- `--baseline-version vXXX`：lock / unlock 時に使用する baseline version を明示（既定：最新版）
- `--restore-version vXXX`：指定 version を復元保存して終了
- `--project NAME`：分析とbaselineを識別する名前（例：`顧客アンケート`）。同じ分析の初回・lock・unlockでは同じ名前を使う
- `--input_xlsx PATH` / `--input_csv PATH`：入力データの上書き指定
- `--text_col NAME` / `--id_col NAME`：列名を指定
- `--k_min N` / `--k_max N`：探索する k の下限・上限（同値で固定）
- `--search-budget fast|standard|thorough`：初回探索の計算予算（既定：`standard`）
- `--include-ica1-cols`：`結果スコア.csv`へICA①座標も追加
- `--max-cp-cols N`：`結果スコア.csv`へ出力するCP座標列の上限
- `--unlock-q Q`：新話題検出の距離分位点（0<Q<1）。未指定時は baseline 保存値を引き継ぐ（初回は 0.95）
- `--unlock-add-k K`：新規に追加する最大クラスタ数
- `--unlock-min-points N`：unlock 時に新クラスタ候補として扱う最小件数（既定：8）
- `--embedding_model NAME` / `--batch N` / `--max_len N` / `--pca_var R` / `--random_state S` / `--log_level LEVEL` など

## 6. 出力ファイル
- `k_candidates.csv`：全候補評価（rank=1 が最良）
- `k_candidates_stage2.csv`：候補探索で上位になったPlan TOP5の比較（`ica1_dim` / CP後次元を表す互換フィールド`ic2_dim` / `k` / 各指標）
- `k_candidates_assignments.csv`：各候補での全テキストの割当情報
- `結果スコア.csv`：各文のクラスタ割当、距離、CP後座標（`CP1...`）。`--include-ica1-cols`指定時はICA①座標（`ICA1_1...`）も追加
- `ICA軸レポート.md`：ICA①の正負の代表文を示す意味軸確認用レポート（初回baselineで出力）
- `結果レポート.json`：採用 Plan、d・K、`silhouette_eval_space` など空間名付き評価指標、実行条件
- `AI_解釈依頼.md`：クラスタ解釈・命名をAIに依頼するための代表文パケット
- `AI_クラスタ一覧.csv`：クラスタごとの要約一覧

## 7. ヒント / トラブルシュート
- 列名が違う場合：`--text_col` で指定
- 微小なスコア誤差（±1e-5 程度）：浮動小数点の丸めによるもので正常
- 再現性を高めたい：`--random_state` を固定
- 同じbaselineで継続したい：初回・lock・unlockで同じ`--project`を使う
- 別の分析として出力を分けたい：別の`--project`名を使う
- 50件の同梱サンプルで`degraded`警告が出る：動作確認用として小さいためです。品質判断には100件以上の実データを推奨します
- 生成物をリポジトリに含めたくない：`.gitignore` に `PVMresult/` を追加

## Appendix: ログ例（抜粋）
- 初回（自動基準作成）：
```
[INFO] [OCHIBI] 🧭 初回（自動基準作成）: ベスト Plan を自動採用して baseline を作成します。
[INFO] [OCHIBI] baseline 作成/更新: PVMresult/baseline_顧客アンケート/history/v001
```
- アンロック：
```
[INFO] [OCHIBI] === 実行モード: 柔軟適用（add-only unlock） ===
[INFO] [OCHIBI] unlock baseline 更新: PVMresult/baseline_顧客アンケート/history/v002
```
