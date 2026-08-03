# PVMケーススタディ実行スクリプト

[トップ](../README.md) · [評価手順](../docs/evaluation_protocol.md) · [公開結果](../docs/evaluation/pvm_6_2_4_momoclo_case_study.md)

`run_momoclo_case_study.py` は、PVM Standard 6.2.4 の変換段階とbaseline lockを、同一embedding・同一 `k`・同一seedで比較する再実行用スクリプトです。`PVM.py` の実関数をimportし、PCA・ICA・Centroid Projectionを別実装しません。

## 比較内容

- V1: embedding + spherical k-means
- V2: PCA + spherical k-means
- V3: PCA + ICA① + spherical k-means
- V4: V3 + Centroid Projection（PVM full）
- realと、embeddingの各列を独立に並べ替えたshuffled対照
- 5 seedのクラスタ安定性と、10分割のholdout lock整合
- 固定規則で抽出するV1/V4代表文、ICA①両端文、V3→V4移動文

内部指標は全variant共通のPCA L2空間で計算します。数値だけで優劣を決めず、機械抽出された本文も必ず読みます。

## 実行

本文列を持つCSVを指定します。既定では5,000件を `random_state=7` で抽出します。

```bash
python benchmark/run_momoclo_case_study.py \
  --input_csv path/to/output.csv \
  --output_dir benchmark/out/momoclo_6_2_4
```

初回は `cl-nagoya/ruri-v3-310m`（1GB超）をダウンロードするためネットワークが必要です。CPUではembeddingだけで長時間かかります。新規計算したembeddingは `OUTPUT_DIR/embeddings.npy` に保存されます。再実行では次のように明示して再利用できます。

```bash
python benchmark/run_momoclo_case_study.py \
  --input_csv path/to/output.csv \
  --embeddings benchmark/out/momoclo_6_2_4/embeddings.npy \
  --output_dir benchmark/out/momoclo_6_2_4_rerun
```

`--embeddings` はCSVから同じseedで選ばれる行と、行順まで一致するものだけを使ってください。スクリプトは行数と最終embeddingのSHA-256を記録しますが、キャッシュと本文の意味的対応までは自動判定できません。

## 出力

- `metrics.json`: 条件、ハッシュ、採用plan、集約値
- `runs.csv`: seed・split単位の生の測定値とエラー
- `qualitative_examples.md`: 固定規則で選んだ本文抜粋
- `qualitative_manifest.csv`: 抽出役割、元ID、本文SHA-256
- `embeddings.npy`: この実行でembeddingを新規計算した場合のみ

公開済みの一例は [PVM Standard 6.2.4 ももクロ実テキスト・ケーススタディ](../docs/evaluation/pvm_6_2_4_momoclo_case_study.md) にあります。これは一つのラベル無しコーパスでの事例であり、他分野への一般的優位性を証明するものではありません。
