# PVM Standard 6.2.2

6.2.2は、baseline名による分析分離を明確にする安全性リリースです。クラスタリング、lock / unlockの計算、baseline schema 2.1は変更していません。

## 変更

- 未指定時に自動読込するbaselineを、現在のprojectと同名のものだけに限定しました。
- フォルダ内に別名baselineが1系列だけ存在しても、暗黙には流用しません。
- 別projectのbaselineを使う場合は`--baseline-from NAME`で明示します。
- `--restore-version`でも名前を推測せず、`--project NAME`または`--baseline-from SOURCE --project TARGET`を指定します。
- 存在しないbaselineを指定したlock / unlock関連操作は、Ruri埋め込みを始める前に停止します。

サンプル実行で作成した`baseline_sample_texts`が、別名の実データ分析へ自動適用されることはありません。既存のschema 2.1 baselineはそのまま利用できます。
