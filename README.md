# Udonarium Log Extractor

Udonariumの部屋データ（`.zip`）から、チャットログをテキストに抽出するローカル実行ツールです。

既存のWeb提供ツールだと処理データ量の上限が気になるため、ローカル環境で大量ログを扱えるように作っています。

## できること（初期の最小構成）

- `未処理` フォルダ内の `.zip` を順番に処理
- `chat.xml` から次の情報だけを抽出
  - タブ名
  - 発言者名
  - 発言内容
- 全タブ横断で時系列順に並べる
- 人間向けログでは、タブが切り替わるたびに見出しを再表示する
- 抽出結果を `出力ログ` に出力
- 正常終了した `.zip` を `処理済み` へ移動
- `config.json` により、人間向け/機械向けの出力ON/OFFを切り替え

## フォルダ構成

```text
.
├─ config.json
├─ extract_udonarium_logs.py
├─ 未処理/      # 処理前の部屋データ(zip)を置く
├─ 処理済み/    # 処理後のzipが移動される
└─ 出力ログ/    # 抽出されたテキストログ
```

## 使い方

1. Python 3.10+ を用意
2. `未処理` フォルダにUdonariumの部屋データ（zip）を置く
3. 次を実行

```bash
python extract_udonarium_logs.py
```

処理が終わると、`出力ログ` にログテキストが作成され、対象zipは `処理済み` に移動します。

## 設定ファイル（config.json）

デフォルト設定は次のとおりです（人間向けのみ出力）。

```json
{
  "outputs": {
    "human_readable": true,
    "machine_readable": false
  },
  "machine": {
    "format": "jsonl"
  }
}
```

- `outputs.human_readable`: 人間向けログ（`.txt`）を出力
- `outputs.machine_readable`: 機械向けログ（`.machine.jsonl` または `.machine.tsv`）を出力
- `machine.format`: `jsonl` か `tsv`

機械向けも同時に出す例:

```json
{
  "outputs": {
    "human_readable": true,
    "machine_readable": true
  },
  "machine": {
    "format": "jsonl"
  }
}
```

## オプション（CLI）

```bash
python extract_udonarium_logs.py \
  --unprocessed-dir 未処理 \
  --processed-dir 処理済み \
  --output-dir 出力ログ \
  --config config.json
```

## 出力例（人間向け）

```text
元ファイル: sample_room.zip

=== タブ: メインタブ ===
PL1: 目星で30、成功です
KP: では日記を見つけます情報どうぞ
=== タブ: 情報タブ ===
KP: 日記の内容は～～～
=== タブ: メインタブ ===
PL1: うわー、この内容はヤバイ
```

## 出力例（機械向け: JSONL）

```jsonl
{"tab":"メインタブ","speaker":"PL1","message":"目星で30、成功です"}
{"tab":"メインタブ","speaker":"KP","message":"では日記を見つけます情報どうぞ"}
{"tab":"情報タブ","speaker":"KP","message":"日記の内容は～～～"}
{"tab":"メインタブ","speaker":"PL1","message":"うわー、この内容はヤバイ"}
```

## 補足

- 初期仕様としてIDやタイムスタンプは出力していません
- 時系列の並び替えには `chat.xml` の `timestamp` を利用しています
