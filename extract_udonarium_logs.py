#!/usr/bin/env python3
"""
Udonariumの部屋データ(zip)からチャットログを抽出して、テキストで保存するスクリプトです。

設定ファイル(config.json)で、次の出力を切り替えられます。
- 人間向けテキスト（既定で有効）
- 人間向けHTML（将来追加予定 / 既定で無効）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence
import xml.etree.ElementTree as ET


def parse_args() -> argparse.Namespace:
    """コマンドライン引数を受け取る。"""
    parser = argparse.ArgumentParser(
        description="未処理フォルダのUdonarium部屋データ(zip)を解析して、チャットログをtxtで出力します。"
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
        help="抽出したテキストログの出力先フォルダ（既定値: 出力ログ）",
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
    # 改行コードを統一する（Windows/Unix混在対策）
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    # 空行を除外しつつ、行の前後空白を削ってから連結する
    lines = [line.strip() for line in unified.split("\n") if line.strip()]
    return " ".join(lines).strip()


def ensure_directory(path: Path) -> None:
    """フォルダがなければ作成する。"""
    path.mkdir(parents=True, exist_ok=True)


def make_unique_path(path: Path) -> Path:
    """
    既存ファイルと名前衝突しないパスを作る。
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


@dataclass(frozen=True)
class ChatMessage:
    """
    チャット1件分のデータを保持する。

    sequenceはタイムスタンプが同値/欠損のときの安定ソート用。
    """

    tab_name: str
    speaker: str
    content: str
    timestamp: int | None
    sequence: int


def default_config() -> Dict[str, Dict[str, object]]:
    """
    既定の設定値を返す。

    ポイント:
    - 既定では人間向けのみ出力
    - HTMLは次ステップで実装するため、いまは無効
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


def extract_messages(root: ET.Element) -> List[ChatMessage]:
    """
    XMLルートからチャットメッセージを取り出す。
    ここではまだタブ単位にまとめず、1件ずつフラットに保持する。
    """
    messages: List[ChatMessage] = []
    sequence = 0

    # chat-tab-list配下のchat-tabを順番に処理する。
    for tab in root.findall("chat-tab"):
        tab_name = (tab.get("name") or "無名タブ").strip() or "無名タブ"

        # 各タブ内のchat要素を走査して、発言1件ごとに保存する。
        for chat in tab.findall("chat"):
            # name属性が発言者名。空なら「名無し」にする。
            speaker = (chat.get("name") or "名無し").strip() or "名無し"
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
                    content=content,
                    timestamp=timestamp,
                    sequence=sequence,
                )
            )
            sequence += 1

    return messages


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
    人間向けの本文を作る。
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
    extracted_messages = extract_messages(root)
    sorted_messages = sort_messages_chronologically(extracted_messages)

    created_paths: List[Path] = []
    outputs = config["outputs"]

    # 人間向けログを作る設定なら、txtを出力する。
    if bool(outputs.get("human_text", False)):
        human_text = build_human_output_text(zip_path.name, sorted_messages)
        human_output_path = make_unique_path(output_dir / f"{zip_path.stem}.txt")
        human_output_path.write_text(human_text, encoding="utf-8")
        created_paths.append(human_output_path)

    # HTMLは次ステップで実装予定なので、ここでは明示的に案内しておく。
    if bool(outputs.get("human_html", False)):
        print(
            "[WARN] outputs.human_html は未実装です。今回の処理ではテキストのみ出力します。",
            file=sys.stderr,
        )

    if not created_paths:
        if bool(outputs.get("human_html", False)):
            raise ValueError(
                "outputs.human_html は未実装です。いったん outputs.human_text を true にしてください。"
            )
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
