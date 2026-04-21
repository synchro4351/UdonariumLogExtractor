# Udonarium Log Extractor

Udonarium の部屋データ（zip）から、読みやすいログを作るツールです。

この README は「まず使う」ための説明だけに絞っています。  
詳しい仕様は [詳細ドキュメント](docs/DETAILS.md) を見てください。

## 1. 先に必要なもの

- Windows パソコン
- Python（3.10 以上）

## 2. Python が入っているか確認

PowerShell かコマンドプロンプトで、次を実行します。

```powershell
python --version
```

もし `python` が使えない場合は、次も試してください。

```powershell
py --version
```

どちらも動かない場合は、先に Python をインストールしてください。

## 3. 使い方（基本）

このフォルダで、次を実行します。

```powershell
python extract_udonarium_logs.py
```

実行するとファイル選択ダイアログが開くので、Udonarium の zip を選びます。  
あとは画面のダイアログに沿って選ぶだけです。

## 4. できあがるファイル

出力先は「選んだ zip と同じフォルダ」です。

- HTML ログ: `<zip名>_html` フォルダ
- テキストログ: `<zip名>.txt`（設定で ON のときだけ）

## 5. 設定を変えたいとき

`config.json` を編集します（メモ帳でOKです）。

よく使う項目:

- `outputs.human_html`: HTML 出力を使うか
- `outputs.human_text`: テキスト出力を使うか
- `common.show_timestamp`: 時刻を表示するか
- `common.show_speaker_id`: ID を表示するか

## 6. 困ったとき

- 実行しても何も起きない: ターミナルにエラーが出ていないか確認
- ダイアログが見えない: ほかのウィンドウの後ろに隠れていないか確認
- 文字化けする: `config.json` を UTF-8 で保存

