#!/usr/bin/env python3
"""
Udonariumの部屋データ(zip)からチャットログを抽出して、テキストで保存するスクリプトです。

最小構成として、以下の情報だけを出力します。
- タブ名
- 発言者名
- 発言内容
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path
from typing import List, Sequence, Tuple
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


def extract_tab_messages(root: ET.Element) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """
    XMLルートから、タブごとの(発言者, 発言)一覧を取り出す。
    戻り値: [(タブ名, [(発言者名, 発言), ...]), ...]
    """
    tab_data: List[Tuple[str, List[Tuple[str, str]]]] = []

    # chat-tab-list配下のchat-tabを順番に処理する。
    for tab in root.findall("chat-tab"):
        tab_name = (tab.get("name") or "無名タブ").strip() or "無名タブ"
        messages: List[Tuple[str, str]] = []

        # 各タブのchat要素を走査する。
        for chat in tab.findall("chat"):
            # name属性が発言者名。空なら「名無し」にする。
            speaker = (chat.get("name") or "名無し").strip() or "名無し"
            content = normalize_text(chat.text or "")

            # 空発言はスキップする（空行だらけになるのを防ぐ）。
            if not content:
                continue

            messages.append((speaker, content))

        tab_data.append((tab_name, messages))

    return tab_data


def build_output_text(zip_name: str, tabs: Sequence[Tuple[str, Sequence[Tuple[str, str]]]]) -> str:
    """テキスト出力用の本文を作る。"""
    lines: List[str] = []
    lines.append(f"元ファイル: {zip_name}")
    lines.append("")

    for tab_name, messages in tabs:
        lines.append(f"=== タブ: {tab_name} ===")
        if not messages:
            lines.append("(発言なし)")
            lines.append("")
            continue

        # 最小構成として「発言者: 発言」のみを並べる。
        for speaker, content in messages:
            lines.append(f"{speaker}: {content}")

        lines.append("")

    # ファイル末尾に改行を1つ入れる。
    return "\n".join(lines).rstrip() + "\n"


def process_zip_file(zip_path: Path, output_dir: Path, processed_dir: Path) -> Path:
    """
    1つのzipを処理し、ログ出力後にzipを処理済みフォルダへ移動する。
    戻り値は出力したテキストファイルのパス。
    """
    root = load_chat_root_from_zip(zip_path)
    tabs = extract_tab_messages(root)
    output_text = build_output_text(zip_path.name, tabs)

    # zip名をベースにtxt名を作る。重複時は連番を付ける。
    output_path = make_unique_path(output_dir / f"{zip_path.stem}.txt")
    output_path.write_text(output_text, encoding="utf-8")

    # 処理済みzipの移動先。重複時は連番を付ける。
    moved_path = make_unique_path(processed_dir / zip_path.name)
    shutil.move(str(zip_path), str(moved_path))
    return output_path


def main() -> int:
    """エントリーポイント。"""
    args = parse_args()
    unprocessed_dir = Path(args.unprocessed_dir)
    processed_dir = Path(args.processed_dir)
    output_dir = Path(args.output_dir)

    # 必要なフォルダを準備する。
    ensure_directory(unprocessed_dir)
    ensure_directory(processed_dir)
    ensure_directory(output_dir)

    # 未処理フォルダ内のzipだけを対象にする。
    zip_files = sorted(unprocessed_dir.glob("*.zip"))
    if not zip_files:
        print("未処理フォルダにzipファイルがありません。")
        return 0

    success_count = 0
    failed_count = 0

    for zip_path in zip_files:
        try:
            output_path = process_zip_file(zip_path, output_dir, processed_dir)
            print(f"[OK] {zip_path.name} -> {output_path}")
            success_count += 1
        except Exception as exc:  # noqa: BLE001
            # エラーが出たzipは移動せずに未処理フォルダへ残す。
            print(f"[ERROR] {zip_path.name}: {exc}", file=sys.stderr)
            failed_count += 1

    print(f"完了: 成功 {success_count} 件 / 失敗 {failed_count} 件")
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
