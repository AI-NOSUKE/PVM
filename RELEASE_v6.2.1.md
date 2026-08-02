# PVM Standard 6.2.1

6.2.1は、日本語WindowsでRuri v3を読み込む際の文字コード互換性を改善するパッチリリースです。探索、クラスタリング、lock / unlock、schema 2.1は6.2.0から変更していません。

## 変更

- `PVM.py`の直接実行時、Windowsが従来のロケール文字コードを使用していれば、PythonをUTF-8モードで一度だけ自動再起動します。
- `PVM`を別スクリプトからimportして使う場合は、非UTF-8環境でRuri埋め込みを開始する前に、`python -X utf8 your_script.py ...`という解決方法を明示します。
- `SCRIPT_VERSION`を`PVM-standard-6.2.1`へ更新しました。baseline schemaは`2.1`のままです。

これにより、UTF-8で収録されたPyTorch内部テンプレートを日本語Windowsの`cp932`で誤読し、モデル読込前に停止する問題を回避します。
