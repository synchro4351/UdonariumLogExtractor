# Udonarium Log Extractor

Udonariumの部屋データ（`.zip`）から、チャットログをローカルで抽出するツールです。

- 人間向けテキスト（`.txt`）
- 人間向けHTML（`index.html + assets`）

の2形式に対応しています（設定でON/OFF）。

## できること

- `未処理` フォルダ内の `.zip` を順番に処理
- `chat.xml` から次を抽出
  - タブ名
  - 発言者名
  - 発言者ID
  - 発言内容
  - 画像識別子（HTMLで利用）
- 全タブ横断で時系列順に並べる
- 処理完了した `.zip` を `処理済み` へ移動

## フォルダ構成

```text
.
├─ config.json
├─ extract_udonarium_logs.py
├─ 未処理/      # 処理前の部屋データ(zip)を置く
├─ 処理済み/    # 処理後のzipが移動される
└─ 出力ログ/    # 抽出ログ
```

## 使い方

1. Python 3.10+ を用意
2. `未処理` にUdonariumの部屋データ（zip）を置く
3. 実行

```bash
python extract_udonarium_logs.py
```

## 設定ファイル（config.json）

初期値:

```json
{
  "outputs": {
    "human_text": true,
    "human_html": false
  }
}
```

- `outputs.human_text`: 人間向けテキスト（`.txt`）を出力
- `outputs.human_html`: 人間向けHTML（`..._html/index.html`）を出力

HTMLも同時に出す例:

```json
{
  "outputs": {
    "human_text": true,
    "human_html": true
  }
}
```

## HTML出力の構成

`出力ログ/<zip名>_html/` の中に次を生成します。

```text
index.html
assets/
  css/style.css
  js/app.js
  js/data.js
  images/...   # zipから抽出した発言画像
```

このフォルダをそのままVPSなどのWeb公開ディレクトリにアップロードして利用できます。

## HTMLビューアの機能

- 画面上部に固定メニュー
- 発言者IDごとの文字色変更
  - 初期色は自動配色
  - 同じIDは同じ色
- タブごとの表示/非表示切り替え
- タイムスタンプ表示ON/OFF（既定: OFF）
- 発言者ID表示ON/OFF（既定: OFF）
- タブ表示モード切り替え
  - 既定: 1列時系列（タブごとに背景色・区切り表示）
  - 切替: タブ別列表示

## CLIオプション

```bash
python extract_udonarium_logs.py \
  --unprocessed-dir 未処理 \
  --processed-dir 処理済み \
  --output-dir 出力ログ \
  --config config.json
```
