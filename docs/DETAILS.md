# 詳細ドキュメント

このファイルは、技術寄りの詳細仕様です。  
まず使いたい場合は [README](../README.md) を見てください。

## 主な機能

- zipをファイル選択ダイアログで指定して処理
- `chat.xml` / `fly_chat.xml` を解析して時系列順に整列
- 発言者IDの自動統合（しきい値ベース）
- 色選択ダイアログで発言者グループ色を調整（「全員色なし」あり）
- タブ選択ダイアログで出力対象タブを指定
- HTMLを静的生成（動的切替なし）
- HTMLを複数ページに分割（既定: 1000発言/ページ）
- 1列表示時はタブ順に応じた字下げでタブを判別しやすく表示
- アバター画像クリックで原寸オーバーレイ表示
- テキスト出力（任意でON）

## 実行方法

```bash
python extract_udonarium_logs.py
```

### 任意オプション

```bash
python extract_udonarium_logs.py --config config.json --input-zip C:\\path\\room.zip
```

- `--config`: 設定ファイルのパス
- `--input-zip`: 入力zipを直接指定（未指定ならダイアログ）

## 処理フロー

1. zipを選択（または `--input-zip` 指定）
2. 話者色を決定
3. タブ選択と表示モード（1列/別列）を選択
4. 出力生成

補足:
- 通常Udonarium: 発言者グループの色を選択
- Udonarium_fly: ログ内の色をそのまま使用（色選択ダイアログなし）

## 出力場所

入力zipと同じフォルダに生成します。

- テキスト: `<zip名>.txt`（重複時は `_1` などを付与）
- HTML: `<zip名>_html/`

HTMLフォルダ構成:

```text
<zip名>_html/
  index.html
  page-002.html
  page-003.html
  ...
  assets/
    style.css
    viewer.js
    images/
      ...
```

## config.json

```json
{
  "outputs": {
    "human_text": false,
    "human_html": true
  },
  "common": {
    "show_timestamp": false,
    "show_speaker_id": false
  },
  "html": {
    "messages_per_page": 1000,
    "separate_tabs_columns_default": false
  },
  "speaker_grouping": {
    "enabled": true,
    "min_messages_for_merge": 15,
    "min_overlap_messages": 6,
    "min_overlap_ratio": 0.25,
    "name_ratio_threshold": 0.12,
    "min_name_count": 3
  },
  "ui": {
    "speaker_alias_preview_max_chars": 48
  }
}
```

### 設定項目の意味

- `outputs.human_text`: テキスト出力ON/OFF
- `outputs.human_html`: HTML出力ON/OFF
- `common.show_timestamp`: 時刻表示ON/OFF（テキスト/HTML共通）
- `common.show_speaker_id`: ID表示ON/OFF（テキスト/HTML共通）
- `html.messages_per_page`: 1ページあたりの発言数（`0` でページ分割なし）
- `html.separate_tabs_columns_default`: タブ別列表示の初期値
- `speaker_grouping.*`: ID統合ルール
- `ui.speaker_alias_preview_max_chars`: 色選択ダイアログの別名表示文字数

## ID統合アルゴリズム（要約）

ID同士を次の条件で統合します。

1. 各IDで「十分出現した名前」を有効名として抽出
2. IDペアで有効名の重なりを測定
3. しきい値を満たすペアをUnion-Findで結合

これにより、日程ごとにIDが変わっても同一話者をまとめやすくします。  
ただし誤判定はゼロではないため、最終調整は色選択ダイアログで行えます。
