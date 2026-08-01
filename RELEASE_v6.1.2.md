# PVM Standard 6.1.2 Release Notes

## Summary

PVM Standard 6.1.2 は、6.1.x 系の堅牢化・整合性修正リリースです。
コアパイプラインは変更ありません:

```text
Embedding → PCA → ICA① → Cluster① → Centroid Projection → Cluster②
```

本リリースは、lock / unlock の gate 閾値解決の一貫性修正と、ユーザー向け表示の改善が中心です。

## Main changes

### Gate 閾値解決の一貫性（挙動修正）

- unlock 時の base_threshold には ±20% のドリフト制限（クランプ）が適用されますが、従来は次回実行時の閾値解決が生の分位点テーブルを優先していたため、unlock を繰り返す運用でクランプ済みの採用閾値が迂回されることがありました。6.1.2 では、**baseline 保存時と同じ quantile が要求された場合はドリフト制限適用済みの採用閾値（`base_threshold` / `ica1_base_threshold`）を正**とし、`--unlock-q` で別の quantile を明示指定した場合のみ分位点テーブルから補間します。
- `--unlock-q` 未指定時は、CLI 既定値ではなく **baseline に保存された unlock_q を引き継ぐ**ようになりました。これにより、baseline を既定値以外の quantile で作成した場合でも、final空間 gate と ICA①空間 gate の感度が暗黙に食い違うことがなくなります。
- 通常 lock も unlock と同じ閾値解決関数を通すようになり、両モードの gate が対称になりました。`結果レポート.json` に `gate_threshold` / `gate_quantile` を追加しています。

### バグ修正

- `spherical_kmeans()` の空クラスタ再割当で、全候補行を使い尽くした場合の
  フォールバック経路に未定義変数参照（NameError）が残っていた問題を修正しました
  （通常運用ではほぼ到達しない経路です）。
- 入力ファイル名からの project 名自動生成で、長音記号「ー」、「ヴ/ヵ/ヶ」、
  踊り字「々」が除去されていた問題を修正しました（例:「レビュー.csv」→ project「レビュー」）。
  全角英数字は NFKC 正規化で半角に揃え、全文字が除去された場合は「PVM」にフォールバックします。
- BOM 付き UTF-8 の CSV を読み込んだ際、先頭列名に BOM が残る可能性があった読み込み順を修正しました
  （`utf-8-sig` を最初に試すようになりました）。
- `.gitattributes` の先頭に BOM が混入し、`* text=auto eol=lf` のパターンが
  実質無効化されていた問題を修正しました。
- Issue テンプレートの front matter（`title` の引用符重複・BOM）を修正しました。

### ユーザー向け表示の改善

- 通常運用（INFO レベル）では、transformers / huggingface_hub / torch / tokenizers が
  出力する英語の警告・注意書きを抑制するようにしました。これらは正常動作でも表示され、
  エラーと誤解されやすいためです。`--log_level DEBUG` ではすべて表示されます。
- 進捗バーの表記を日本語化しました（「埋め込み」「候補探索」）。
- 一部の英語混じりログ（列検出、ICA① 閾値維持の通知など）を日本語に統一しました。
- v5.x 時代のバージョン番号が残っていた案内文を現行表記に更新しました。

### CI

- `--self-check` は torch / transformers を必要としないため、コア依存のみで数分で回る
  軽量スモークジョブと、requirements.txt 全体（torch CPU wheel 含む）のインストール検証
  ジョブに分離しました。いずれも Python 3.13 / 3.14 の両方で実行します。

## Notes

- baseline の保存形式（schema）に変更はありません。
- 既定運用（`--unlock-q` 未指定、baseline も既定値で作成）では、初回 lock の割当結果は
  6.1.1 と一致します。unlock を繰り返した baseline では、ドリフト制限が正しく効くように
  なったぶん、gate 判定が従来より保守的になる場合があります（これは修正意図どおりの挙動です）。

## Previous release (v6.1.1)

v6.1.1 は v6.1.0 の堅牢化内容に対する unlock 再保存バグ修正でした。
偏った unlock バッチで保護対象の ICA① セントロイドがゼロ化されないよう、
既存行を凍結し、追加クラスタ分のみをバッチから推定するようになっています。

## Schema Version

```text
SCHEMA_VERSION = "2.1"
SCRIPT_VERSION = "PVM-standard-6.1.2"
```

## Compatibility

- schema 2.1 baseline はそのまま利用できます（再作成不要）。
- schema 2.0 baseline は従来どおり `pre_projection_gate_missing` 警告付きで読み込み可能で、
  final空間 gate のみで動作します。
- schema 1.1 以前の旧 baseline は読み込めません。初回実行で再作成してください。

## Tested Items

```text
python -m py_compile PVM.py
python PVM.py --version
python PVM.py --self-check
```

GitHub Actions では Python 3.13 / 3.14 の両方で、軽量スモーク（コア依存のみ）と
依存スタック全体のインストール検証を実行します。
