# PVM Manual

## 1. 概要
高次元のテキスト埋め込みを **二段階 ICA + spherical k-means** で整理します。
初回は候補（k と ICA 次元の組み合わせ）を探索し、ベストPlanを自動採用してbaselineを作成します。
**rank=1 が最良**で、`--use-plan N` の **N にはこの rank 値**を渡します。
2回目以降は既存の基準に基づくロック実行がデフォルトです。`--unlock` で新話題のみを吸収して基準を拡張できます。

## 2. 入力データ
- 既定設定で `python PVM.py` を実行可能（必要に応じてオプションで上書き）。
- 対応形式：Excel（`.xlsx`）/ CSV（UTF-8 推奨）
- 最低限の列：`text`（テキスト本文）
  - 例：
    | id | text               |
    |----|--------------------|
    | 1  | これはテスト文です |

> 備考：列名が異なる場合は `--text_col` で指定可能。

## 3. インストール
```bash
pip install -r requirements.txt
```

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
- `--project NAME`：保存先のプロジェクト名（例：`1回目` / `2回目`）
- `--input_xlsx PATH` / `--input_csv PATH`：入力データの上書き指定
- `--text_col NAME` / `--id_col NAME`：列名を指定
- `--k_min N` / `--k_max N`：探索する k の下限・上限（同値で固定）
- `--unlock-q Q`：新話題検出の距離分位点（0..1）
- `--unlock-add-k K`：新規に追加する最大クラスタ数
- `--unlock-min-points N`：unlock 時に新クラスタ候補として扱う最小件数（既定：8）
- `--embedding_model NAME` / `--batch N` / `--max_len N` / `--pca_var R` / `--random_state S` / `--log_level LEVEL` など

## 6. 出力ファイル
- `k_candidates.csv`：全候補評価（rank=1 が最良）
- `k_candidates_stage2.csv`：候補探索で上位になったPlan TOP5の比較（ica1_dim / ic2_dim / k / 各指標）
- `k_candidates_assignments.csv`：各候補での全テキストの割当情報
- `結果スコア.csv`：各文のクラスタ割当、距離、IC（ICA②）などの指標
- `結果レポート.json`：採用 Plan、d・K、評価指標、実行条件
- `AI_解釈依頼.md`：クラスタ解釈・命名をAIに依頼するための代表文パケット
- `AI_クラスタ一覧.csv`：クラスタごとの要約一覧

## 7. ヒント / トラブルシュート
- 列名が違う場合：`--text_col` で指定
- 微小なスコア誤差（±1e-5 程度）：浮動小数点の丸めによるもので正常
- 再現性を高めたい：`--random_state` を固定
- 出力を分けたい：`--project` で保存先を分ける
- 生成物をリポジトリに含めたくない：`.gitignore` に `PVMresult/` を追加

## Appendix: ログ例（抜粋）
- 初回 Plan 採用：
```
基準作成: Plan #5 を採用 → d=16, K=6
baseline作成: PVMresult\baseline_1回目\history\v001
```
- アンロック：
```
柔軟適用: 新話題候補 = 10 件（q=0.90）
柔軟適用: 追加セントロイド = 2 個
柔軟適用: 追加後の基準を保存しました → PVMresult\baseline_2回目\history\v001
```
