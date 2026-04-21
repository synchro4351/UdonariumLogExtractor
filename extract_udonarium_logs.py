#!/usr/bin/env python3
"""
Udonariumの部屋データ(zip)からチャットログを抽出するスクリプトです。

このスクリプトは、設定ファイル(config.json)の内容に応じて次を出力します。
- 人間向けテキスト（.txt）
- 人間向けHTML（index.html + assetsフォルダ）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple
import xml.etree.ElementTree as ET


# HTMLで扱う画像拡張子の一覧。
# Udonarium部屋データに入ることが多い画像形式をここに定義する。
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を受け取る。"""
    parser = argparse.ArgumentParser(
        description=(
            "未処理フォルダのUdonarium部屋データ(zip)を解析して、"
            "人間向けテキスト/HTMLログを出力します。"
        )
    )
    # 初期値は、リポジトリの想定フォルダ構成に合わせる。
    parser.add_argument(
        "--unprocessed-dir",
        default="未処理",
        help="未処理のzipファイルを置くフォルダ（既定値: 未処理）",
    )
    parser.add_argument(
        "--processed-dir",
        default="処理済み",
        help="処理後のzipファイルを移動するフォルダ（既定値: 処理済み）",
    )
    parser.add_argument(
        "--output-dir",
        default="出力ログ",
        help="抽出したログの出力先フォルダ（既定値: 出力ログ）",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="出力形式を指定する設定ファイル（既定値: config.json）",
    )
    return parser.parse_args()


def normalize_text(text: str) -> str:
    """
    発言文字列を読みやすい形に整える。

    - 改行をまとめて1行化
    - 前後の空白を除去
    """
    # 改行コードを統一する（Windows/Unix混在対策）。
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    # 空行を除外しつつ、各行の前後空白を削ってから連結する。
    lines = [line.strip() for line in unified.split("\n") if line.strip()]
    return " ".join(lines).strip()


def ensure_directory(path: Path) -> None:
    """フォルダがなければ作成する。"""
    path.mkdir(parents=True, exist_ok=True)


def make_unique_path(path: Path) -> Path:
    """
    既存ファイルと名前衝突しないファイルパスを作る。
    例: sample.txt -> sample_1.txt
    """
    if not path.exists():
        return path

    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def make_unique_directory(path: Path) -> Path:
    """
    既存フォルダと名前衝突しないフォルダパスを作る。
    例: sample_html -> sample_html_1
    """
    if not path.exists():
        return path

    index = 1
    while True:
        candidate = path.with_name(f"{path.name}_{index}")
        if not candidate.exists():
            return candidate
        index += 1


@dataclass(frozen=True)
class ChatMessage:
    """
    チャット1件分のデータを保持する。

    sequenceは、タイムスタンプが同値/欠損のときの安定ソート用。
    """

    tab_name: str
    speaker: str
    speaker_id: str
    image_identifier: str
    content: str
    timestamp: int | None
    sequence: int


def default_config() -> Dict[str, Dict[str, object]]:
    """
    既定の設定値を返す。

    - human_text: 既定で有効
    - human_html: 既定で無効（必要時にtrueへ）
    """
    return {
        "outputs": {
            "human_text": True,
            "human_html": False,
        },
    }


def load_config(config_path: Path) -> Dict[str, Dict[str, object]]:
    """
    設定ファイルを読み込む。
    ファイルが存在しない場合は既定値を使う。
    """
    config = default_config()

    if not config_path.exists():
        return config

    try:
        # utf-8-sigで読むことで、BOM付きUTF-8設定ファイルも扱えるようにする。
        loaded = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"設定ファイルのJSONが壊れています: {config_path}") from exc

    if not isinstance(loaded, dict):
        raise ValueError("設定ファイルのトップレベルはオブジェクト(JSONの{})である必要があります。")

    outputs = loaded.get("outputs")
    if outputs is not None:
        if not isinstance(outputs, dict):
            raise ValueError("設定ファイルの outputs はオブジェクトである必要があります。")
        for key in ("human_text", "human_html"):
            if key in outputs:
                value = outputs[key]
                if not isinstance(value, bool):
                    raise ValueError(f"outputs.{key} は true/false で指定してください。")
                config["outputs"][key] = value

    return config


def load_chat_root_from_zip(zip_path: Path) -> ET.Element:
    """
    zip内のchat.xmlを読み込み、XMLルート要素を返す。
    chat.xmlがない場合やXMLが壊れている場合は例外にする。
    """
    with zipfile.ZipFile(zip_path, "r") as archive:
        try:
            xml_bytes = archive.read("chat.xml")
        except KeyError as exc:
            raise FileNotFoundError("chat.xml が見つかりません。") from exc

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError("chat.xml のXML解析に失敗しました。") from exc

    return root


def parse_timestamp(raw_timestamp: str | None) -> int | None:
    """
    タイムスタンプ属性を整数へ変換する。
    変換できない場合はNoneを返す。
    """
    if raw_timestamp is None:
        return None

    raw = raw_timestamp.strip()
    if not raw:
        return None

    try:
        return int(raw)
    except ValueError:
        return None


def extract_tabs_and_messages(root: ET.Element) -> Tuple[List[str], List[ChatMessage]]:
    """
    XMLルートからタブ名一覧とチャットメッセージ一覧を取り出す。

    戻り値:
    - タブ名の順序付きリスト
    - メッセージのフラットなリスト
    """
    tab_names: List[str] = []
    messages: List[ChatMessage] = []
    sequence = 0

    # chat-tab-list配下のchat-tabを順番に処理する。
    for tab in root.findall("chat-tab"):
        tab_name = (tab.get("name") or "無名タブ").strip() or "無名タブ"
        tab_names.append(tab_name)

        # 各タブ内のchat要素を走査して、発言1件ごとに保存する。
        for chat in tab.findall("chat"):
            # name属性が発言者名。空なら「名無し」にする。
            speaker = (chat.get("name") or "名無し").strip() or "名無し"
            # from属性を発言者IDとして使う。空ならunknown。
            speaker_id = (chat.get("from") or "unknown").strip() or "unknown"
            # 画像識別子。none_iconは「画像なし」扱いで使う。
            image_identifier = (chat.get("imageIdentifier") or "").strip()
            # 発言本文を1行化して扱いやすくする。
            content = normalize_text(chat.text or "")
            # 空発言はスキップする（空行だらけになるのを防ぐ）。
            if not content:
                continue
            # 時系列ソートに使うtimestamp属性を取得する。
            timestamp = parse_timestamp(chat.get("timestamp"))

            messages.append(
                ChatMessage(
                    tab_name=tab_name,
                    speaker=speaker,
                    speaker_id=speaker_id,
                    image_identifier=image_identifier,
                    content=content,
                    timestamp=timestamp,
                    sequence=sequence,
                )
            )
            sequence += 1

    return tab_names, messages


def sort_messages_chronologically(messages: Sequence[ChatMessage]) -> List[ChatMessage]:
    """
    全タブ横断で時系列順に並べる。

    - まずtimestampで昇順
    - timestampがないものは末尾へ
    - 同時刻はsequenceで元順序を維持
    """
    return sorted(
        messages,
        key=lambda item: (
            item.timestamp is None,
            item.timestamp if item.timestamp is not None else 0,
            item.sequence,
        ),
    )


def build_human_output_text(zip_name: str, messages: Sequence[ChatMessage]) -> str:
    """
    人間向けテキストの本文を作る。
    タブが切り替わったタイミングで見出しを再表示する。
    """
    lines: List[str] = []
    lines.append(f"元ファイル: {zip_name}")
    lines.append("")

    if not messages:
        lines.append("(発言なし)")
        return "\n".join(lines).rstrip() + "\n"

    current_tab: str | None = None
    for message in messages:
        # タブが変わったら見出しを出す。
        if message.tab_name != current_tab:
            lines.append(f"=== タブ: {message.tab_name} ===")
            current_tab = message.tab_name

        # 最小構成として「発言者: 発言」のみを並べる。
        lines.append(f"{message.speaker}: {message.content}")

    # ファイル末尾に改行を1つ入れる。
    return "\n".join(lines).rstrip() + "\n"


def build_zip_image_index(archive: zipfile.ZipFile) -> Dict[str, zipfile.ZipInfo]:
    """
    zip内の画像ファイルを「識別子(stem) -> ZipInfo」の辞書にまとめる。

    例:
    - 1234567890abcdef.png -> keyは "1234567890abcdef"
    """
    index: Dict[str, zipfile.ZipInfo] = {}

    for info in archive.infolist():
        if info.is_dir():
            continue

        filename = Path(info.filename).name
        suffix = Path(filename).suffix.lower()

        # 画像拡張子以外は対象外にする。
        if suffix not in IMAGE_EXTENSIONS:
            continue

        stem = Path(filename).stem
        # 同一stemが複数ある場合は、最初のものを採用する。
        if stem not in index:
            index[stem] = info

    return index


def extract_used_images(
    zip_path: Path,
    messages: Sequence[ChatMessage],
    images_dir: Path,
) -> Dict[str, str]:
    """
    HTML表示に必要な画像だけをzipから取り出し、imagesフォルダへ保存する。

    戻り値:
    - imageIdentifier -> HTMLから参照する相対パス（assets/images/...）
    """
    ensure_directory(images_dir)

    # 発言で使われたimageIdentifierだけを対象にする。
    used_identifiers = {
        msg.image_identifier
        for msg in messages
        if msg.image_identifier and msg.image_identifier != "none_icon"
    }

    image_path_map: Dict[str, str] = {}
    used_filenames: set[str] = set()

    with zipfile.ZipFile(zip_path, "r") as archive:
        image_index = build_zip_image_index(archive)

        for identifier in sorted(used_identifiers):
            info = image_index.get(identifier)
            if info is None:
                # 見つからない場合は画像なし扱いにする。
                continue

            original_name = Path(info.filename).name
            target_name = original_name

            # 念のため同名衝突を回避する。
            if target_name in used_filenames:
                target_name = f"{identifier}{Path(original_name).suffix.lower()}"
            used_filenames.add(target_name)

            image_bytes = archive.read(info)
            target_path = images_dir / target_name
            target_path.write_bytes(image_bytes)

            # HTML内では、bundle直下からの相対パスで参照する。
            image_path_map[identifier] = f"assets/images/{target_name}"

    return image_path_map


def build_html_index_html(source_zip_name: str) -> str:
    """HTML本体(index.html)を生成する。"""
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Udonarium Log Viewer - {source_zip_name}</title>
  <link rel="stylesheet" href="assets/css/style.css" />
</head>
<body>
  <header id="menu" class="menu">
    <div class="menu-line">
      <strong class="menu-title">Udonarium Log Viewer</strong>
      <span id="source-name" class="source-name"></span>
    </div>
    <div class="menu-line controls">
      <label><input id="toggle-timestamp" type="checkbox" /> タイムスタンプ表示</label>
      <label><input id="toggle-speaker-id" type="checkbox" /> ID表示</label>
      <label><input id="toggle-columns" type="checkbox" /> タブを別列で表示</label>
    </div>
    <details open>
      <summary>タブ表示切り替え</summary>
      <div id="tab-filters" class="tab-filters"></div>
    </details>
    <details>
      <summary>発言者の文字色</summary>
      <div id="speaker-color-list" class="speaker-color-list"></div>
    </details>
  </header>

  <main id="log-root" class="log-root"></main>

  <script src="assets/js/data.js"></script>
  <script src="assets/js/app.js"></script>
</body>
</html>
"""


def build_html_css() -> str:
    """HTML表示用のCSSを生成する。"""
    return """/* 基本設定: 余白は控えめで、長文ログを読みやすくする */
:root {
  --menu-height: 220px;
  --bg: #f7f7f7;
  --text: #1f1f1f;
  --muted: #666;
  --panel: #ffffff;
  --border: #dddddd;
}

* {
  box-sizing: border-box;
}

html,
body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Yu Gothic UI", "Hiragino Kaku Gothic ProN", "Meiryo", sans-serif;
  line-height: 1.45;
  font-size: 14px;
}

.menu {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 1000;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  padding: 6px 8px;
  max-height: 48vh;
  overflow: auto;
}

.menu-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
}

.menu-line.controls label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}

.menu-title {
  font-size: 14px;
}

.source-name {
  font-size: 12px;
  color: var(--muted);
  word-break: break-all;
}

details {
  border: 1px solid var(--border);
  border-radius: 4px;
  background: #fcfcfc;
  padding: 4px 6px;
  margin-bottom: 6px;
}

details summary {
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}

.tab-filters,
.speaker-color-list {
  display: grid;
  gap: 4px;
  margin-top: 6px;
}

.tab-filters {
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
}

.speaker-color-list {
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
}

.tab-filter-item,
.speaker-color-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 0;
  font-size: 12px;
}

.speaker-id-preview {
  color: var(--muted);
  font-size: 11px;
}

.log-root {
  padding: calc(var(--menu-height) + 8px) 8px 10px;
}

.empty-state {
  color: var(--muted);
  padding: 8px;
  border: 1px dashed var(--border);
  border-radius: 4px;
  background: #fff;
}

.log-stream {
  display: block;
}

.tab-separator {
  margin: 8px 0 4px;
  padding-left: 6px;
  border-left: 4px solid var(--tab-accent, #999);
  color: #444;
  font-weight: 700;
  font-size: 12px;
}

.message-row {
  display: flex;
  gap: 8px;
  margin: 3px 0;
  padding: 4px 6px;
  border-left: 4px solid var(--tab-accent, #bbb);
  border-radius: 4px;
  background: var(--tab-bg, #fff);
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 4px;
  object-fit: cover;
  flex: 0 0 auto;
  background: #ddd;
}

.avatar-fallback {
  width: 32px;
  height: 32px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: #555;
  background: #e3e3e3;
  flex: 0 0 auto;
}

.message-main {
  min-width: 0;
  flex: 1 1 auto;
}

.message-header {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
  margin-bottom: 1px;
}

.speaker-name {
  font-weight: 700;
}

.meta {
  display: none;
  color: var(--muted);
  font-size: 11px;
}

.tab-chip {
  font-size: 11px;
  color: #555;
  border: 1px solid #bdbdbd;
  border-radius: 10px;
  padding: 0 6px;
}

.message-content {
  white-space: pre-wrap;
  word-break: break-word;
}

body.show-timestamp .meta-timestamp {
  display: inline;
}

body.show-speaker-id .meta-speaker-id {
  display: inline;
}

.columns-grid {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(260px, 1fr);
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 6px;
}

.tab-column {
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #fff;
  padding: 4px;
  min-height: 120px;
}

.tab-column-title {
  font-size: 12px;
  font-weight: 700;
  margin: 0 0 4px;
  padding: 2px 4px;
  border-left: 4px solid var(--tab-accent, #999);
  background: var(--tab-bg, #f5f5f5);
}

@media (max-width: 640px) {
  .message-row {
    gap: 6px;
    padding: 3px 4px;
  }

  .avatar,
  .avatar-fallback {
    width: 28px;
    height: 28px;
  }
}
"""


def build_html_app_js() -> str:
    """HTML表示用のJavaScriptを生成する。"""
    return r"""(() => {
  "use strict";

  // data.jsで注入したログデータを参照する。
  const data = window.UDONARIUM_LOG_DATA;
  if (!data || !Array.isArray(data.messages)) {
    return;
  }

  // 画面上の主要ノードを最初に取得しておく。
  const menu = document.getElementById("menu");
  const logRoot = document.getElementById("log-root");
  const sourceName = document.getElementById("source-name");
  const tabFilters = document.getElementById("tab-filters");
  const speakerColorList = document.getElementById("speaker-color-list");
  const toggleTimestamp = document.getElementById("toggle-timestamp");
  const toggleSpeakerId = document.getElementById("toggle-speaker-id");
  const toggleColumns = document.getElementById("toggle-columns");

  // 画面に元zip名を表示する。
  sourceName.textContent = data.source_file || "";

  // 発言者一覧とタブ別メッセージ一覧を前処理しておく。
  const speakerMap = buildSpeakerMap(data.messages);
  const tabOrder = Array.isArray(data.tabs) ? data.tabs.slice() : [];
  const messagesByTab = buildMessagesByTab(tabOrder, data.messages);

  // UIの現在状態を一元管理する。
  const state = {
    showTimestamp: false,
    showSpeakerId: false,
    separateColumns: false,
    visibleTabs: new Set(tabOrder),
    speakerColors: new Map(),
  };

  // 発言者IDごとに初期色を作る（同じIDは同じ色）。
  for (const speakerId of speakerMap.keys()) {
    state.speakerColors.set(speakerId, defaultSpeakerColor(speakerId));
  }

  // 初期描画。
  renderTabFilters();
  renderSpeakerColorControls();
  bindToggles();
  applyBodyClasses();
  renderLog();
  syncMenuHeight();

  // メニュー高さは開閉やリサイズで変わるので都度再計算する。
  window.addEventListener("resize", syncMenuHeight);
  menu.addEventListener("toggle", syncMenuHeight, true);

  function buildSpeakerMap(messages) {
    const map = new Map();
    for (const msg of messages) {
      const speakerId = normalizeSpeakerId(msg.speaker_id);
      if (!map.has(speakerId)) {
        map.set(speakerId, msg.speaker || "名無し");
      }
    }
    return map;
  }

  function buildMessagesByTab(tabs, messages) {
    const map = new Map();
    for (const tab of tabs) {
      map.set(tab, []);
    }
    for (const msg of messages) {
      if (!map.has(msg.tab)) {
        map.set(msg.tab, []);
      }
      map.get(msg.tab).push(msg);
    }
    return map;
  }

  function normalizeSpeakerId(rawValue) {
    const value = (rawValue || "").trim();
    return value || "unknown";
  }

  function bindToggles() {
    toggleTimestamp.addEventListener("change", () => {
      state.showTimestamp = toggleTimestamp.checked;
      applyBodyClasses();
    });

    toggleSpeakerId.addEventListener("change", () => {
      state.showSpeakerId = toggleSpeakerId.checked;
      applyBodyClasses();
    });

    toggleColumns.addEventListener("change", () => {
      state.separateColumns = toggleColumns.checked;
      renderLog();
    });
  }

  function applyBodyClasses() {
    document.body.classList.toggle("show-timestamp", state.showTimestamp);
    document.body.classList.toggle("show-speaker-id", state.showSpeakerId);
  }

  function renderTabFilters() {
    tabFilters.textContent = "";

    for (const tabName of tabOrder) {
      const item = document.createElement("label");
      item.className = "tab-filter-item";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.visibleTabs.has(tabName);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          state.visibleTabs.add(tabName);
        } else {
          state.visibleTabs.delete(tabName);
        }
        renderLog();
      });

      const label = document.createElement("span");
      label.textContent = tabName;

      item.appendChild(checkbox);
      item.appendChild(label);
      tabFilters.appendChild(item);
    }
  }

  function renderSpeakerColorControls() {
    speakerColorList.textContent = "";

    for (const [speakerId, displayName] of speakerMap.entries()) {
      const row = document.createElement("div");
      row.className = "speaker-color-item";

      const colorInput = document.createElement("input");
      colorInput.type = "color";
      colorInput.value = state.speakerColors.get(speakerId);
      colorInput.addEventListener("input", () => {
        state.speakerColors.set(speakerId, colorInput.value);
        renderLog();
      });

      const name = document.createElement("span");
      name.textContent = displayName;
      name.style.color = state.speakerColors.get(speakerId);

      const idPreview = document.createElement("span");
      idPreview.className = "speaker-id-preview";
      idPreview.textContent = `(${speakerId})`;

      row.appendChild(colorInput);
      row.appendChild(name);
      row.appendChild(idPreview);
      speakerColorList.appendChild(row);
    }
  }

  function renderLog() {
    logRoot.textContent = "";

    if (state.separateColumns) {
      renderColumnsMode();
      return;
    }
    renderStreamMode();
  }

  function renderStreamMode() {
    const visibleMessages = data.messages.filter((msg) => state.visibleTabs.has(msg.tab));
    if (visibleMessages.length === 0) {
      logRoot.appendChild(createEmptyState("表示対象の発言がありません。"));
      return;
    }

    const stream = document.createElement("div");
    stream.className = "log-stream";

    let lastTab = null;
    for (const msg of visibleMessages) {
      // タブが切り替わったときに見出し行を挿入する。
      if (msg.tab !== lastTab) {
        const separator = document.createElement("div");
        separator.className = "tab-separator";
        separator.textContent = `タブ: ${msg.tab}`;

        const palette = tabPalette(msg.tab);
        separator.style.setProperty("--tab-accent", palette.accent);

        stream.appendChild(separator);
        lastTab = msg.tab;
      }

      stream.appendChild(createMessageRow(msg));
    }

    logRoot.appendChild(stream);
  }

  function renderColumnsMode() {
    const grid = document.createElement("div");
    grid.className = "columns-grid";

    let appendedColumnCount = 0;
    for (const tabName of tabOrder) {
      if (!state.visibleTabs.has(tabName)) {
        continue;
      }

      const tabMessages = messagesByTab.get(tabName) || [];
      const column = document.createElement("section");
      column.className = "tab-column";

      const palette = tabPalette(tabName);
      column.style.setProperty("--tab-bg", palette.bg);
      column.style.setProperty("--tab-accent", palette.accent);

      const title = document.createElement("h2");
      title.className = "tab-column-title";
      title.textContent = tabName;
      column.appendChild(title);

      if (tabMessages.length === 0) {
        column.appendChild(createEmptyState("このタブに発言はありません。"));
      } else {
        for (const msg of tabMessages) {
          column.appendChild(createMessageRow(msg));
        }
      }

      grid.appendChild(column);
      appendedColumnCount += 1;
    }

    if (appendedColumnCount === 0) {
      logRoot.appendChild(createEmptyState("表示対象のタブがありません。"));
      return;
    }

    logRoot.appendChild(grid);
  }

  function createMessageRow(msg) {
    const row = document.createElement("article");
    row.className = "message-row";
    row.dataset.tab = msg.tab;

    const palette = tabPalette(msg.tab);
    row.style.setProperty("--tab-bg", palette.bg);
    row.style.setProperty("--tab-accent", palette.accent);

    // 画像があれば表示し、なければ簡易イニシャルを表示する。
    if (msg.image) {
      const avatar = document.createElement("img");
      avatar.className = "avatar";
      avatar.src = msg.image;
      avatar.alt = `${msg.speaker || "名無し"} の画像`;
      avatar.loading = "lazy";
      row.appendChild(avatar);
    } else {
      const fallback = document.createElement("div");
      fallback.className = "avatar-fallback";
      fallback.textContent = speakerInitial(msg.speaker);
      row.appendChild(fallback);
    }

    const main = document.createElement("div");
    main.className = "message-main";

    const header = document.createElement("div");
    header.className = "message-header";

    const speakerName = document.createElement("span");
    speakerName.className = "speaker-name";
    speakerName.textContent = msg.speaker || "名無し";
    const speakerId = normalizeSpeakerId(msg.speaker_id);
    speakerName.style.color = state.speakerColors.get(speakerId) || defaultSpeakerColor(speakerId);
    header.appendChild(speakerName);

    const speakerIdMeta = document.createElement("span");
    speakerIdMeta.className = "meta meta-speaker-id";
    speakerIdMeta.textContent = `ID: ${speakerId}`;
    header.appendChild(speakerIdMeta);

    const timestampMeta = document.createElement("span");
    timestampMeta.className = "meta meta-timestamp";
    timestampMeta.textContent = `時刻: ${formatTimestamp(msg.timestamp)}`;
    header.appendChild(timestampMeta);

    const tabChip = document.createElement("span");
    tabChip.className = "tab-chip";
    tabChip.textContent = msg.tab;
    header.appendChild(tabChip);

    const content = document.createElement("div");
    content.className = "message-content";
    content.textContent = msg.message || "";

    main.appendChild(header);
    main.appendChild(content);
    row.appendChild(main);

    return row;
  }

  function createEmptyState(text) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = text;
    return empty;
  }

  function speakerInitial(name) {
    const trimmed = (name || "").trim();
    if (!trimmed) {
      return "?";
    }
    return trimmed.slice(0, 1);
  }

  function formatTimestamp(rawValue) {
    if (rawValue === null || rawValue === undefined) {
      return "-";
    }
    const num = Number(rawValue);
    if (!Number.isFinite(num)) {
      return String(rawValue);
    }
    const dt = new Date(num);
    if (Number.isNaN(dt.getTime())) {
      return String(rawValue);
    }
    return dt.toLocaleString("ja-JP", { hour12: false });
  }

  function syncMenuHeight() {
    const menuHeight = menu ? menu.offsetHeight : 0;
    document.documentElement.style.setProperty("--menu-height", `${menuHeight}px`);
  }

  // タブ名から背景色/境界色を安定生成する。
  function tabPalette(tabName) {
    const hue = hashString(tabName) % 360;
    return {
      bg: `hsl(${hue}, 58%, 96%)`,
      accent: `hsl(${hue}, 45%, 56%)`,
    };
  }

  // 発言者IDから文字色を安定生成する。
  function defaultSpeakerColor(speakerId) {
    const hue = hashString(speakerId) % 360;
    return hslToHex(hue, 62, 34);
  }

  // 簡単な文字列ハッシュ（同じ入力なら同じ値）。
  function hashString(text) {
    let hash = 0;
    for (let i = 0; i < text.length; i += 1) {
      hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
    }
    return hash;
  }

  // HSLからcolor input用のHEXへ変換する。
  function hslToHex(h, s, l) {
    const sat = s / 100;
    const light = l / 100;
    const c = (1 - Math.abs(2 * light - 1)) * sat;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = light - c / 2;
    let r = 0;
    let g = 0;
    let b = 0;

    if (h < 60) {
      r = c;
      g = x;
    } else if (h < 120) {
      r = x;
      g = c;
    } else if (h < 180) {
      g = c;
      b = x;
    } else if (h < 240) {
      g = x;
      b = c;
    } else if (h < 300) {
      r = x;
      b = c;
    } else {
      r = c;
      b = x;
    }

    const rr = Math.round((r + m) * 255);
    const gg = Math.round((g + m) * 255);
    const bb = Math.round((b + m) * 255);
    return `#${toHex(rr)}${toHex(gg)}${toHex(bb)}`;
  }

  function toHex(value) {
    return value.toString(16).padStart(2, "0");
  }
})();
"""


def build_html_data_js(
    source_zip_name: str,
    tab_names: Sequence[str],
    messages: Sequence[ChatMessage],
    image_path_map: Dict[str, str],
) -> str:
    """
    HTML側へ渡すデータ(data.js)を生成する。
    data.jsは単純なグローバル変数代入にして、ローカル閲覧時も動作しやすくする。
    """
    payload_messages: List[Dict[str, Any]] = []
    for msg in messages:
        payload_messages.append(
            {
                "tab": msg.tab_name,
                "speaker": msg.speaker,
                "speaker_id": msg.speaker_id,
                "message": msg.content,
                "timestamp": msg.timestamp,
                "image": image_path_map.get(msg.image_identifier),
            }
        )

    payload = {
        "source_file": source_zip_name,
        "tabs": list(tab_names),
        "messages": payload_messages,
    }
    json_body = json.dumps(payload, ensure_ascii=False)
    return f"window.UDONARIUM_LOG_DATA = {json_body};\n"


def build_human_output_html(
    zip_path: Path,
    output_dir: Path,
    tab_names: Sequence[str],
    messages: Sequence[ChatMessage],
) -> Path:
    """
    人間向けHTML出力を作成する。

    出力構成:
    - {zip名}_html/index.html
    - {zip名}_html/assets/css/style.css
    - {zip名}_html/assets/js/app.js
    - {zip名}_html/assets/js/data.js
    - {zip名}_html/assets/images/...
    """
    bundle_dir = make_unique_directory(output_dir / f"{zip_path.stem}_html")
    assets_dir = bundle_dir / "assets"
    css_dir = assets_dir / "css"
    js_dir = assets_dir / "js"
    images_dir = assets_dir / "images"

    # 必要なフォルダを作る。
    ensure_directory(bundle_dir)
    ensure_directory(css_dir)
    ensure_directory(js_dir)
    ensure_directory(images_dir)

    # 発言で使われる画像だけ抽出する。
    image_path_map = extract_used_images(zip_path, messages, images_dir)

    # HTML/CSS/JSファイルを書き出す。
    (bundle_dir / "index.html").write_text(
        build_html_index_html(zip_path.name),
        encoding="utf-8",
    )
    (css_dir / "style.css").write_text(build_html_css(), encoding="utf-8")
    (js_dir / "app.js").write_text(build_html_app_js(), encoding="utf-8")
    (js_dir / "data.js").write_text(
        build_html_data_js(zip_path.name, tab_names, messages, image_path_map),
        encoding="utf-8",
    )

    return bundle_dir / "index.html"


def process_zip_file(
    zip_path: Path,
    output_dir: Path,
    processed_dir: Path,
    config: Dict[str, Dict[str, object]],
) -> List[Path]:
    """
    1つのzipを処理し、ログ出力後にzipを処理済みフォルダへ移動する。
    戻り値は出力したファイルパス一覧。
    """
    root = load_chat_root_from_zip(zip_path)
    tab_names, extracted_messages = extract_tabs_and_messages(root)
    sorted_messages = sort_messages_chronologically(extracted_messages)

    created_paths: List[Path] = []
    outputs = config["outputs"]

    # 人間向けテキストを作る設定なら、txtを出力する。
    if bool(outputs.get("human_text", False)):
        human_text = build_human_output_text(zip_path.name, sorted_messages)
        human_output_path = make_unique_path(output_dir / f"{zip_path.stem}.txt")
        human_output_path.write_text(human_text, encoding="utf-8")
        created_paths.append(human_output_path)

    # 人間向けHTMLを作る設定なら、bundleを出力する。
    if bool(outputs.get("human_html", False)):
        human_html_path = build_human_output_html(
            zip_path=zip_path,
            output_dir=output_dir,
            tab_names=tab_names,
            messages=sorted_messages,
        )
        created_paths.append(human_html_path)

    if not created_paths:
        raise ValueError("設定により出力がすべて無効です。outputsを見直してください。")

    # 処理済みzipの移動先。重複時は連番を付ける。
    moved_path = make_unique_path(processed_dir / zip_path.name)
    shutil.move(str(zip_path), str(moved_path))
    return created_paths


def main() -> int:
    """エントリーポイント。"""
    args = parse_args()
    unprocessed_dir = Path(args.unprocessed_dir)
    processed_dir = Path(args.processed_dir)
    output_dir = Path(args.output_dir)
    config_path = Path(args.config)

    # 必要なフォルダを準備する。
    ensure_directory(unprocessed_dir)
    ensure_directory(processed_dir)
    ensure_directory(output_dir)
    config = load_config(config_path)

    # 未処理フォルダ内のzipだけを対象にする。
    zip_files = sorted(unprocessed_dir.glob("*.zip"))
    if not zip_files:
        print("未処理フォルダにzipファイルがありません。")
        return 0

    success_count = 0
    failed_count = 0

    for zip_path in zip_files:
        try:
            output_paths = process_zip_file(zip_path, output_dir, processed_dir, config)
            output_display = ", ".join(str(path) for path in output_paths)
            print(f"[OK] {zip_path.name} -> {output_display}")
            success_count += 1
        except Exception as exc:  # noqa: BLE001
            # エラーが出たzipは移動せずに未処理フォルダへ残す。
            print(f"[ERROR] {zip_path.name}: {exc}", file=sys.stderr)
            failed_count += 1

    print(f"完了: 成功 {success_count} 件 / 失敗 {failed_count} 件")
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
