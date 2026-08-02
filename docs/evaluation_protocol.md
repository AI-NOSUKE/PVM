# PVM 評価手順

[トップ](../README.md) · [操作マニュアル](../Manual.md) · [資料一覧](./README.md)

## 目的

PVMの評価では、内部クラスタ指標だけで性能を判断しません。PVMは正解ラベルの再現器ではなく、自由記述の意味構造を整理し、解釈したクラスタ体系をbaselineとして再利用するためのパイプラインです。

## 指標の扱い

候補選定の silhouette、Calinski-Harabasz、Davies-Bouldin は、全候補共通の PCA L2 評価空間 `X_eval = l2_normalize(pca_base["Xp"])` で計算します。`silhouette_eval_space`、`ch_eval_space`、`db_eval_space` はこの共通空間の指標です。

Centroid Projection後の `silhouette_projected_space`、`ch_projected_space`、`db_projected_space` は診断・解釈補助用です。Centroid ProjectionはCluster①の重心から学習されるため、これらを候補品質や外部妥当性の単独証拠にはしません。

## 評価軸

- **seed安定性**：乱数seedを変え、割当、代表文、クラスタ解釈が大きく崩れないか確認する。
- **再標本安定性**：bootstrapまたは固定比率のsubsampleで再実行し、同様の意味グループが再現するか確認する。
- **holdout lock**：trainでbaselineを作成し、holdoutを再学習せずlock適用して割当の解釈可能性を確認する。
- **意味的一貫性**：人手またはLLM補助で、代表文、境界例、クラスタ名の一貫性を確認する。
- **ARI / NMI**：信頼できる正解ラベルがある場合のみ、追加証拠として使う。

## 比較対象

入力データ、embeddingモデル、前処理、比較可能な範囲の `k` を揃え、少なくとも次を比較します。

- embedding + spherical k-means
- PCA → ICA① + spherical k-means
- PVMの完全パイプライン
- 必要に応じてBERTopic等の他手法

## 手順

1. データ出典、フィルタ条件、件数、言語を記録する。
2. embeddingモデルとembedding prefixを固定する。
3. 各手法を可能な範囲で同じ `k` 候補で実行する。
4. 複数seedで繰り返す。
5. bootstrapまたはsubsampleで再標本評価する。
6. trainでbaselineを作成し、holdoutへlock適用する。
7. 代表文、境界例、クラスタ名を読む。
8. 内部指標と定性所見を分けず併記する。

## 報告項目

| 項目 | 内容 |
|---|---|
| dataset | データ名または出典 |
| sample size | テキスト件数 |
| embedding | モデルとprefix |
| method | 比較手法 |
| k | クラスタ数 |
| eval-space metrics | `silhouette_eval_space` / `ch_eval_space` / `db_eval_space` |
| projected-space metrics | 診断用指標 |
| balance | クラスタ件数のバランス |
| stability | seedまたは再標本安定性 |
| holdout lock | holdout割当の整合性と解釈 |
| qualitative review | 代表文とクラスタ命名の所見 |

## 解釈原則

- 内部指標は候補の絞り込みと診断に使い、単独で優位性を主張しない。
- seedや標本が変わっても、意味と代表文が保たれるかを重視する。
- baseline lockの運用性は、holdoutまたは別時点データで確認する。
- 測定済みの結果と、未検証の仮説を分けて報告する。
