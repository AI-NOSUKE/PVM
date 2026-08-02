# PVM Standard 6.2.0

6.2.0は、baseline / lock / unlockの運用構造を変えず、初回baselineの「ICA①次元とkの探索」と出力の意味づけを修正するリリースです。

## 変更点

- PCA次元全体を対数間隔で覆うICA①候補を探索し、必要な範囲だけ局所refineします。
- retry中に収束した実効次元をcanonical候補として登録し、比較時は次元fallbackを無効化します。canonical次元に収束しないseedはARI計算から除外し、失敗理由と成功率を出力します。
- Centroid ProjectionのrankがICA①次元より小さい候補を完全版として選択できます。
- ICA①軸をseed再現性、列シャッフル対照との差、極端文集合とSpearman相関による非重複性で診断し、`usable_ic_count`と`ic_signal_gain`を出力します。
- CPの割当変更率、境界例への集中、コア文書の保存を診断できます。
- 完全版が成立しない場合の既存ICA/PCA fallbackは残しますが、`selection_tier=degraded`、`quality_gate_passed=false` として完全版から分離します。
- `結果スコア.csv` の完全版最終座標を `CP1...` と明記し、ICA①座標と区別します。初回baselineでは `ICA軸レポート.md` を既定出力し、`--include-ica1-cols` でCSVにもICA①座標を追加できます。
- `--search-budget fast|standard|thorough` を追加しました。

## 互換性

- `SCRIPT_VERSION`: `PVM-standard-6.2.0`
- baseline schema: `2.1`（6.1.2と同じ）
- 既存のbaseline / lock / unlockコマンドは維持します。
- `--max_ic_cols` は互換維持し、意味が明確な別名 `--max-cp-cols` を追加しました。
