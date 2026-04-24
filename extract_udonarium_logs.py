#!/usr/bin/env python3
"""
Udonariumの部屋データ(zip)からログを抽出するツールです。

この版では、次を重視しています。
- 1ファイルずつ確実に処理する
- HTMLは動的変更をやめ、静的に軽く表示する
- 長いログは1000件ごとなどでページ分割する

主な流れ:
1. ファイル選択ダイアログでzipを選ぶ
2. chat.xmlを解析する
3. 発言者グループと色を確認・調整する
4. タブ選択と表示モードを選ぶ
5. テキスト / HTML を出力する
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple
import xml.etree.ElementTree as ET

# GUIは標準ライブラリのtkinterを使う。
# Windows環境での実行を想定しているため、通常は利用可能。
try:
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox
except Exception:  # pragma: no cover - 実行環境依存
    tk = None  # type: ignore[assignment]
    colorchooser = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]


# Udonariumのzipに含まれうる画像拡張子。
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
CHAT_XML_CANDIDATES = ("chat.xml", "fly_chat.xml")


@dataclass(frozen=True)
class ChatMessage:
    """チャット1件分のデータ。"""

    tab_name: str
    speaker_name: str
    speaker_id: str
    image_identifier: str
    content: str
    timestamp: int | None
    sequence: int
    native_color: str | None


@dataclass(frozen=True)
class SpeakerGroup:
    """
    発言者グループ情報。

    - 1つ以上のIDをまとめた単位
    - 色選択ダイアログではこの単位で色を指定する
    """

    group_key: str
    representative_name: str
    member_ids: Tuple[str, ...]
    aliases_sorted: Tuple[Tuple[str, int], ...]
    message_count: int
    default_color: str


class UnionFind:
    """IDをまとめるためのUnion-Find。"""

    def __init__(self, items: Iterable[str]) -> None:
        self.parent: Dict[str, str] = {}
        self.rank: Dict[str, int] = {}
        for item in items:
            self.parent[item] = item
            self.rank[item] = 0

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, a: str, b: str) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
            return
        if self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
            return
        self.parent[rb] = ra
        self.rank[ra] += 1


def parse_args() -> argparse.Namespace:
    """CLI引数を読み取る。"""
    parser = argparse.ArgumentParser(
        description="Udonariumの部屋データ(zip)を解析して、テキスト/HTMLを出力します。"
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="設定ファイルのパス（既定値: config.json）",
    )
    parser.add_argument(
        "--input-zip",
        default="",
        help="入力zipを直接指定する。未指定時はファイル選択ダイアログを出す。",
    )
    return parser.parse_args()


def default_config() -> Dict[str, Any]:
    """
    既定設定を返す。

    ポイント:
    - HTMLは既定でON
    - テキストは既定でOFF（必要なら設定でON）
    - ID/時刻は既定で非表示
    """
    return {
        "outputs": {
            "human_text": False,
            "human_html": True,
        },
        "common": {
            "show_timestamp": False,
            "show_speaker_id": False,
        },
        "html": {
            "messages_per_page": 1000,
            "separate_tabs_columns_default": False,
        },
        "speaker_grouping": {
            "enabled": True,
            "min_messages_for_merge": 15,
            "min_overlap_messages": 6,
            "min_overlap_ratio": 0.25,
            "name_ratio_threshold": 0.12,
            "min_name_count": 3,
        },
        "ui": {
            "speaker_alias_preview_max_chars": 48,
        },
    }


def load_config(config_path: Path) -> Dict[str, Any]:
    """
    設定ファイルを読み込む。
    ファイルが無い場合は既定値を使う。
    """
    config = default_config()
    if not config_path.exists():
        return config

    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"設定ファイルのJSON解析に失敗しました: {config_path}") from exc

    if not isinstance(loaded, dict):
        raise ValueError("設定ファイルのトップレベルはオブジェクト(JSONの{})にしてください。")

    # セクションごとに型を確認しながら上書きする。
    for section_name in ("outputs", "common", "html", "speaker_grouping", "ui"):
        loaded_section = loaded.get(section_name)
        if loaded_section is None:
            continue
        if not isinstance(loaded_section, dict):
            raise ValueError(f"設定ファイルの {section_name} はオブジェクトである必要があります。")
        for key, value in loaded_section.items():
            if key in config[section_name]:
                config[section_name][key] = value

    # 最小限の型チェックと範囲調整を行う。
    config["outputs"]["human_text"] = bool(config["outputs"]["human_text"])
    config["outputs"]["human_html"] = bool(config["outputs"]["human_html"])
    config["common"]["show_timestamp"] = bool(config["common"]["show_timestamp"])
    config["common"]["show_speaker_id"] = bool(config["common"]["show_speaker_id"])
    config["html"]["separate_tabs_columns_default"] = bool(config["html"]["separate_tabs_columns_default"])
    config["speaker_grouping"]["enabled"] = bool(config["speaker_grouping"]["enabled"])

    # 0 を指定した場合は「ページ分割なし（全件を1ページ）」として扱う。
    config["html"]["messages_per_page"] = max(0, int(config["html"]["messages_per_page"]))
    config["speaker_grouping"]["min_messages_for_merge"] = max(
        1, int(config["speaker_grouping"]["min_messages_for_merge"])
    )
    config["speaker_grouping"]["min_overlap_messages"] = max(
        1, int(config["speaker_grouping"]["min_overlap_messages"])
    )
    config["speaker_grouping"]["min_overlap_ratio"] = max(
        0.0, min(1.0, float(config["speaker_grouping"]["min_overlap_ratio"]))
    )
    config["speaker_grouping"]["name_ratio_threshold"] = max(
        0.0, min(1.0, float(config["speaker_grouping"]["name_ratio_threshold"]))
    )
    config["speaker_grouping"]["min_name_count"] = max(1, int(config["speaker_grouping"]["min_name_count"]))
    config["ui"]["speaker_alias_preview_max_chars"] = max(
        10, int(config["ui"]["speaker_alias_preview_max_chars"])
    )

    return config


def normalize_text(text: str) -> str:
    """発言本文を1行へ整形する。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    return " ".join(lines).strip()


def parse_timestamp(raw_timestamp: str | None) -> int | None:
    """タイムスタンプ属性を整数へ変換する。"""
    if raw_timestamp is None:
        return None
    raw = raw_timestamp.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def normalize_css_color(raw_color: str | None) -> str | None:
    """#RRGGBB/#RGB 形式だけを受け付ける。"""
    if raw_color is None:
        return None
    text = raw_color.strip()
    if not text:
        return None
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", text) and not re.fullmatch(r"#[0-9a-fA-F]{3}", text):
        return None
    return text.lower()


def load_chat_root_from_zip(zip_path: Path) -> Tuple[ET.Element, str]:
    """zip内のchat XMLを読み込み、XMLルート要素とファイル名を返す。"""
    with zipfile.ZipFile(zip_path, "r") as archive:
        file_names = [info.filename for info in archive.infolist() if not info.is_dir()]

        # まずは完全一致で探す。見つからない場合は basename 一致も許容する。
        selected_name: str | None = None
        lowered_to_original = {name.lower(): name for name in file_names}
        for candidate in CHAT_XML_CANDIDATES:
            if candidate in file_names:
                selected_name = candidate
                break
            lowered = lowered_to_original.get(candidate.lower())
            if lowered:
                selected_name = lowered
                break
            basename_matched = next(
                (name for name in file_names if Path(name).name.lower() == candidate.lower()),
                None,
            )
            if basename_matched:
                selected_name = basename_matched
                break

        if selected_name is None:
            expected = ", ".join(CHAT_XML_CANDIDATES)
            raise FileNotFoundError(f"zip内に {expected} が見つかりません。")

        xml_bytes = archive.read(selected_name)

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"{Path(selected_name).name} のXML解析に失敗しました。") from exc

    return root, Path(selected_name).name


def extract_tabs_and_messages(root: ET.Element) -> Tuple[List[str], List[ChatMessage]]:
    """XMLからタブ一覧とメッセージ一覧を抽出する。"""
    tab_names: List[str] = []
    messages: List[ChatMessage] = []
    seq = 0

    for tab in root.findall("chat-tab"):
        tab_name = (tab.get("name") or "無名タブ").strip() or "無名タブ"
        tab_names.append(tab_name)

        for chat in tab.findall("chat"):
            speaker_name = (chat.get("name") or "名無し").strip() or "名無し"
            speaker_id = (chat.get("from") or "unknown").strip() or "unknown"
            image_identifier = (chat.get("imageIdentifier") or "").strip()
            native_color = normalize_css_color(chat.get("color"))
            content = normalize_text(chat.text or "")
            if not content:
                continue

            messages.append(
                ChatMessage(
                    tab_name=tab_name,
                    speaker_name=speaker_name,
                    speaker_id=speaker_id,
                    image_identifier=image_identifier,
                    content=content,
                    timestamp=parse_timestamp(chat.get("timestamp")),
                    sequence=seq,
                    native_color=native_color,
                )
            )
            seq += 1

    return tab_names, messages


def sort_messages_chronologically(messages: Sequence[ChatMessage]) -> List[ChatMessage]:
    """全タブ横断で時系列順へ並べる。"""
    return sorted(
        messages,
        key=lambda m: (
            m.timestamp is None,
            m.timestamp if m.timestamp is not None else 0,
            m.sequence,
        ),
    )


def stable_hash(text: str) -> int:
    """文字列から安定した疑似ハッシュ値を作る。"""
    value = 0
    for ch in text:
        value = (value * 31 + ord(ch)) & 0xFFFFFFFF
    return value


def hsl_to_hex(hue: int, sat: float, light: float) -> str:
    """HSLをHEXカラーへ変換する。"""
    # sat/lightは0.0〜1.0で受け取る。
    c = (1 - abs(2 * light - 1)) * sat
    x = c * (1 - abs(((hue / 60.0) % 2) - 1))
    m = light - c / 2

    if hue < 60:
        r, g, b = c, x, 0
    elif hue < 120:
        r, g, b = x, c, 0
    elif hue < 180:
        r, g, b = 0, c, x
    elif hue < 240:
        r, g, b = 0, x, c
    elif hue < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x

    rr = int(round((r + m) * 255))
    gg = int(round((g + m) * 255))
    bb = int(round((b + m) * 255))
    return f"#{rr:02x}{gg:02x}{bb:02x}"


def default_color_for_key(key: str) -> str:
    """文字列キーから既定色を安定生成する。"""
    hue = stable_hash(key) % 360
    return hsl_to_hex(hue, 0.62, 0.34)


def tab_palette(tab_name: str) -> Tuple[str, str]:
    """タブ表示用の背景色とアクセント色を返す。"""
    hue = stable_hash(tab_name) % 360
    bg = hsl_to_hex(hue, 0.55, 0.96)
    accent = hsl_to_hex(hue, 0.45, 0.56)
    return bg, accent


def build_id_name_counter(messages: Sequence[ChatMessage]) -> Tuple[Dict[str, int], Dict[str, Counter[str]]]:
    """IDごとの総発言数と、名前の出現回数を集計する。"""
    id_totals: Dict[str, int] = defaultdict(int)
    id_name_counts: Dict[str, Counter[str]] = defaultdict(Counter)
    for msg in messages:
        id_totals[msg.speaker_id] += 1
        id_name_counts[msg.speaker_id][msg.speaker_name] += 1
    return dict(id_totals), dict(id_name_counts)


def build_speaker_groups(
    messages: Sequence[ChatMessage],
    grouping_config: Dict[str, Any],
) -> Tuple[List[SpeakerGroup], Dict[str, str]]:
    """
    ID統合ルールに従って発言者グループを作る。

    戻り値:
    - グループ一覧
    - id -> group_key の対応
    """
    id_totals, id_name_counts = build_id_name_counter(messages)
    all_ids = sorted(id_totals.keys())

    # 統合を無効化した場合は、ID単位でグループを作って返す。
    if not grouping_config["enabled"]:
        groups: List[SpeakerGroup] = []
        id_to_group_key: Dict[str, str] = {}
        for speaker_id in all_ids:
            aliases = sorted(
                id_name_counts[speaker_id].items(),
                key=lambda item: (-item[1], item[0]),
            )
            representative = aliases[0][0] if aliases else speaker_id
            group_key = f"id::{speaker_id}"
            groups.append(
                SpeakerGroup(
                    group_key=group_key,
                    representative_name=representative,
                    member_ids=(speaker_id,),
                    aliases_sorted=tuple(aliases),
                    message_count=id_totals[speaker_id],
                    default_color=default_color_for_key(group_key),
                )
            )
            id_to_group_key[speaker_id] = group_key
        return groups, id_to_group_key

    # 統合ありの場合はUnion-FindでID群をまとめる。
    uf = UnionFind(all_ids)

    # IDごとの「有効名」を作る。
    # 例外発言を減らすため、少なすぎる頻度の名前は一致判定から除外する。
    id_valid_names: Dict[str, set[str]] = {}
    for speaker_id in all_ids:
        total = id_totals[speaker_id]
        min_count = max(
            int(grouping_config["min_name_count"]),
            int(math.ceil(float(grouping_config["name_ratio_threshold"]) * total)),
        )
        valid = {
            name
            for name, count in id_name_counts[speaker_id].items()
            if count >= min_count
        }
        id_valid_names[speaker_id] = valid

    # IDペアごとに重なりを見て、条件を満たせば統合する。
    for a, b in combinations(all_ids, 2):
        total_a = id_totals[a]
        total_b = id_totals[b]
        if min(total_a, total_b) < int(grouping_config["min_messages_for_merge"]):
            continue

        valid_a = id_valid_names[a]
        valid_b = id_valid_names[b]
        common_names = valid_a & valid_b
        if not common_names:
            continue

        overlap = sum(min(id_name_counts[a][n], id_name_counts[b][n]) for n in common_names)
        score = overlap / float(min(total_a, total_b))

        if (
            overlap >= int(grouping_config["min_overlap_messages"])
            and score >= float(grouping_config["min_overlap_ratio"])
        ):
            uf.union(a, b)

    # rootごとにIDをまとめる。
    root_to_ids: Dict[str, List[str]] = defaultdict(list)
    for speaker_id in all_ids:
        root_to_ids[uf.find(speaker_id)].append(speaker_id)

    groups: List[SpeakerGroup] = []
    id_to_group_key: Dict[str, str] = {}

    # グループ情報を整形する。
    for root_id, member_ids in sorted(root_to_ids.items(), key=lambda item: item[0]):
        alias_counter: Counter[str] = Counter()
        total_messages = 0
        for speaker_id in member_ids:
            alias_counter.update(id_name_counts[speaker_id])
            total_messages += id_totals[speaker_id]

        aliases_sorted = sorted(alias_counter.items(), key=lambda item: (-item[1], item[0]))
        representative = aliases_sorted[0][0] if aliases_sorted else root_id
        group_key = f"group::{root_id}"

        group = SpeakerGroup(
            group_key=group_key,
            representative_name=representative,
            member_ids=tuple(sorted(member_ids)),
            aliases_sorted=tuple(aliases_sorted),
            message_count=total_messages,
            default_color=default_color_for_key(group_key),
        )
        groups.append(group)
        for speaker_id in member_ids:
            id_to_group_key[speaker_id] = group_key

    # 発言数が多い順で見やすく並べる。
    groups.sort(key=lambda g: (-g.message_count, g.representative_name))
    return groups, id_to_group_key


def make_alias_preview(aliases: Sequence[Tuple[str, int]], max_chars: int) -> str:
    """別名一覧の短いプレビュー文字列を作る。"""
    names = [name for name, _ in aliases]
    raw = ", ".join(names)
    if len(raw) <= max_chars:
        return raw
    return raw[: max_chars - 1] + "…"


def create_hidden_root() -> tk.Tk:
    """tkinterの非表示ルートを作る。"""
    if tk is None:  # pragma: no cover - 実行環境依存
        raise RuntimeError("tkinterが利用できません。GUI実行環境で動かしてください。")
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    return root


def show_progress(message: str) -> None:
    """進捗をコンソールへ表示する。"""
    print(message, flush=True)


def present_dialog(dialog: tk.Toplevel, width: int, height: int) -> None:
    """
    ダイアログを確実に前面表示し、画面中央へ配置する。

    ここを共通化して「裏で待機して見えない」状態を防ぐ。
    """
    dialog.withdraw()
    dialog.update_idletasks()

    screen_w = dialog.winfo_screenwidth()
    screen_h = dialog.winfo_screenheight()
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 2)
    dialog.geometry(f"{width}x{height}+{x}+{y}")

    dialog.deiconify()
    dialog.lift()
    try:
        dialog.focus_set()
    except Exception:
        pass


def choose_input_zip(root: tk.Tk, input_zip_arg: str) -> Path | None:
    """入力zipを決める。引数が無ければダイアログで選ぶ。"""
    if input_zip_arg:
        return Path(input_zip_arg).expanduser().resolve()

    selected = filedialog.askopenfilename(
        parent=root,
        title="Udonariumの部屋データ(zip)を選択してください",
        filetypes=[("Zip files", "*.zip"), ("All files", "*.*")],
    )
    if not selected:
        return None
    return Path(selected).resolve()


def show_speaker_color_dialog(
    root: tk.Tk,
    groups: Sequence[SpeakerGroup],
    preview_max_chars: int,
) -> Dict[str, str] | None:
    """
    発言者グループごとの色選択ダイアログを表示する。

    戻り値:
    - 確定: group_key -> color
    - キャンセル: None
    """
    dialog = tk.Toplevel()
    dialog.title("発言者ごとの文字色設定")
    dialog.minsize(760, 420)
    show_progress("色設定ダイアログUIを構築しています...")

    # 結果を外へ返すための入れ物。
    result_holder: Dict[str, Dict[str, str] | None] = {"value": None}

    header = tk.Label(
        dialog,
        text=(
            "色を設定してください。ここで設定した色がHTML出力へ固定で反映されます。\n"
            "※ 同じグループは同じ色になります。"
        ),
        justify="left",
        anchor="w",
    )
    header.pack(fill="x", padx=10, pady=(10, 6))

    # スクロール領域を作る（グループ数が多くても扱えるようにする）。
    outer = tk.Frame(dialog)
    outer.pack(fill="both", expand=True, padx=10, pady=6)
    option_row = tk.Frame(dialog)
    option_row.pack(fill="x", padx=10, pady=(0, 4))

    canvas = tk.Canvas(outer, highlightthickness=0)
    scrollbar = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    scroll_frame = tk.Frame(canvas)

    scroll_frame.bind(
        "<Configure>",
        lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    selected_colors: Dict[str, str] = {g.group_key: g.default_color for g in groups}
    no_color_var = tk.BooleanVar(value=False)

    # 1グループ1行で色設定UIを作る。
    for group in groups:
        row = tk.Frame(scroll_frame, bd=1, relief="solid", padx=6, pady=4)
        row.pack(fill="x", pady=2)

        swatch = tk.Label(row, width=3, bg=selected_colors[group.group_key], relief="ridge")
        swatch.grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 8))

        summary_text = (
            f"{group.representative_name}  "
            f"(発言 {group.message_count}件 / ID {len(group.member_ids)}個)"
        )
        tk.Label(row, text=summary_text, anchor="w", justify="left", font=("", 10, "bold")).grid(
            row=0, column=1, sticky="w"
        )

        alias_preview = make_alias_preview(group.aliases_sorted, preview_max_chars)
        tk.Label(
            row,
            text=f"別名: {alias_preview}",
            anchor="w",
            justify="left",
            fg="#555555",
        ).grid(row=1, column=1, sticky="w")

        def choose_color(target_group: SpeakerGroup = group, target_swatch: tk.Label = swatch) -> None:
            current = selected_colors[target_group.group_key]
            _rgb, picked = colorchooser.askcolor(
                color=current,
                parent=dialog,
                title=f"色を選択: {target_group.representative_name}",
            )
            if picked:
                selected_colors[target_group.group_key] = picked.lower()
                target_swatch.configure(bg=picked)

        tk.Button(row, text="色を変更", command=choose_color).grid(row=0, column=2, rowspan=2, padx=(8, 0))

        row.grid_columnconfigure(1, weight=1)

    tk.Checkbutton(
        option_row,
        text="全員色なし（話者名を通常テキスト色で表示）",
        variable=no_color_var,
    ).pack(anchor="w")

    button_row = tk.Frame(dialog)
    button_row.pack(fill="x", padx=10, pady=(0, 10))

    def on_ok() -> None:
        if no_color_var.get():
            result_holder["value"] = {g.group_key: "" for g in groups}
        else:
            result_holder["value"] = dict(selected_colors)
        dialog.destroy()

    def on_cancel() -> None:
        result_holder["value"] = None
        dialog.destroy()

    tk.Button(button_row, text="確定", command=on_ok, width=10).pack(side="right", padx=(6, 0))
    tk.Button(button_row, text="キャンセル", command=on_cancel, width=10).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", on_cancel)
    dialog.bind("<Escape>", lambda _e: on_cancel())
    present_dialog(dialog, width=920, height=620)
    show_progress("色設定ダイアログを表示しました。")
    dialog.wait_window()
    show_progress("色設定ダイアログを終了しました。")
    return result_holder["value"]


def show_tab_selection_dialog(
    root: tk.Tk,
    tab_names: Sequence[str],
    tab_counts: Dict[str, int],
    separate_columns_default: bool,
) -> Tuple[List[str], bool] | None:
    """
    タブ選択ダイアログを表示する。

    戻り値:
    - 確定: (選択タブ一覧, 列分割モード)
    - キャンセル: None
    """
    dialog = tk.Toplevel()
    dialog.title("タブ選択")
    dialog.minsize(520, 360)
    show_progress("タブ選択ダイアログUIを構築しています...")

    result_holder: Dict[str, Tuple[List[str], bool] | None] = {"value": None}

    tk.Label(
        dialog,
        text="出力に含めるタブを選択してください（複数選択可）。",
        anchor="w",
        justify="left",
    ).pack(fill="x", padx=10, pady=(10, 6))

    options_frame = tk.Frame(dialog)
    options_frame.pack(fill="x", padx=10, pady=(0, 8))

    separate_var = tk.BooleanVar(value=separate_columns_default)
    tk.Checkbutton(
        options_frame,
        text="タブを完全に別列で表示する（OFFなら1列＋左余白インデント）",
        variable=separate_var,
    ).pack(anchor="w")

    list_frame = tk.Frame(dialog)
    list_frame.pack(fill="both", expand=True, padx=10, pady=6)

    canvas = tk.Canvas(list_frame, highlightthickness=0)
    scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas)

    inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    tab_vars: Dict[str, tk.BooleanVar] = {}
    for tab_name in tab_names:
        var = tk.BooleanVar(value=True)
        tab_vars[tab_name] = var
        count = tab_counts.get(tab_name, 0)
        tk.Checkbutton(inner, text=f"{tab_name}  ({count}件)", variable=var).pack(anchor="w", pady=1)

    button_row = tk.Frame(dialog)
    button_row.pack(fill="x", padx=10, pady=(0, 10))

    def on_ok() -> None:
        selected = [name for name in tab_names if tab_vars[name].get()]
        if not selected:
            messagebox.showwarning("タブ未選択", "少なくとも1つはタブを選択してください。", parent=dialog)
            return
        result_holder["value"] = (selected, bool(separate_var.get()))
        dialog.destroy()

    def on_cancel() -> None:
        result_holder["value"] = None
        dialog.destroy()

    tk.Button(button_row, text="確定", command=on_ok, width=10).pack(side="right", padx=(6, 0))
    tk.Button(button_row, text="キャンセル", command=on_cancel, width=10).pack(side="right")

    dialog.protocol("WM_DELETE_WINDOW", on_cancel)
    dialog.bind("<Escape>", lambda _e: on_cancel())
    present_dialog(dialog, width=640, height=560)
    show_progress("タブ選択ダイアログを表示しました。")
    dialog.wait_window()
    show_progress("タブ選択ダイアログを終了しました。")
    return result_holder["value"]


def make_unique_path(path: Path) -> Path:
    """既存ファイルと衝突しないパスを返す。"""
    if not path.exists():
        return path
    idx = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{idx}{path.suffix}")
        if not candidate.exists():
            return candidate
        idx += 1


def make_unique_directory(path: Path) -> Path:
    """既存フォルダと衝突しないパスを返す。"""
    if not path.exists():
        return path
    idx = 1
    while True:
        candidate = path.with_name(f"{path.name}_{idx}")
        if not candidate.exists():
            return candidate
        idx += 1


def ensure_directory(path: Path) -> None:
    """フォルダが無ければ作成する。"""
    path.mkdir(parents=True, exist_ok=True)


def build_zip_image_index(archive: zipfile.ZipFile) -> Dict[str, zipfile.ZipInfo]:
    """zip内画像を imageIdentifier(stem) で引ける辞書へ変換する。"""
    index: Dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        file_name = Path(info.filename).name
        suffix = Path(file_name).suffix.lower()
        if suffix not in IMAGE_EXTENSIONS:
            continue
        stem = Path(file_name).stem
        if stem not in index:
            index[stem] = info
    return index


def extract_used_images(
    zip_path: Path,
    messages: Sequence[ChatMessage],
    images_dir: Path,
) -> Dict[str, str]:
    """
    使われる画像だけ抽出して保存する。

    戻り値:
    - image_identifier -> HTML相対パス
    """
    ensure_directory(images_dir)
    used_ids = sorted(
        {
            msg.image_identifier
            for msg in messages
            if msg.image_identifier and msg.image_identifier != "none_icon"
        }
    )

    image_map: Dict[str, str] = {}
    used_file_names: set[str] = set()

    with zipfile.ZipFile(zip_path, "r") as archive:
        image_index = build_zip_image_index(archive)
        for image_id in used_ids:
            info = image_index.get(image_id)
            if info is None:
                continue

            original_name = Path(info.filename).name
            save_name = original_name
            if save_name in used_file_names:
                save_name = f"{image_id}{Path(original_name).suffix.lower()}"
            used_file_names.add(save_name)

            target_path = images_dir / save_name
            target_path.write_bytes(archive.read(info))
            image_map[image_id] = f"assets/images/{save_name}"

    return image_map


def chunk_messages(messages: Sequence[ChatMessage], size: int) -> List[List[ChatMessage]]:
    """メッセージをページサイズごとに分割する。"""
    # size が 0 以下ならページ分割せず、全件を1ページとして返す。
    if size <= 0:
        return [list(messages)] if messages else [[]]

    if not messages:
        return [[]]
    chunks: List[List[ChatMessage]] = []
    for i in range(0, len(messages), size):
        chunks.append(list(messages[i : i + size]))
    return chunks


def format_timestamp(timestamp: int | None) -> str:
    """タイムスタンプ(ms)を表示文字列へ変換する。"""
    if timestamp is None:
        return "-"
    try:
        dt = datetime.fromtimestamp(timestamp / 1000.0)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(timestamp)


def escape(text: str) -> str:
    """HTMLエスケープの短縮ヘルパー。"""
    return html.escape(text, quote=True)


def resolve_speaker_color(
    message: ChatMessage,
    id_to_group_key: Dict[str, str],
    group_colors: Dict[str, str],
    use_native_colors: bool,
) -> str | None:
    """
    表示に使う話者色を決める。
    - use_native_colors=True かつメッセージ内に色がある場合はそれを優先
    - ダイアログで「全員色なし」を選んだ場合は空文字が入り、Noneに変換して無彩色表示
    """
    if use_native_colors and message.native_color:
        return message.native_color

    group_key = id_to_group_key.get(message.speaker_id, f"name::{message.speaker_name}")
    chosen = group_colors.get(group_key)
    if chosen is not None:
        return chosen or None
    return default_color_for_key(group_key)


def build_html_css() -> str:
    """静的HTML用のCSSを返す。"""
    return """/* 軽量な静的ログ表示用スタイル */
:root {
  --bg: #f7f7f7;
  --panel: #ffffff;
  --text: #222;
  --muted: #666;
  --border: #d9d9d9;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Yu Gothic UI", "Hiragino Kaku Gothic ProN", "Meiryo", sans-serif;
  font-size: 14px;
  line-height: 1.45;
}

.top {
  position: sticky;
  top: 0;
  z-index: 20;
  background: #ffffffee;
  backdrop-filter: blur(2px);
  border-bottom: 1px solid var(--border);
  padding: 8px 10px;
}

.title {
  font-weight: 700;
  font-size: 14px;
}

.meta {
  margin-top: 2px;
  color: var(--muted);
  font-size: 12px;
  word-break: break-all;
}

.pager {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  font-size: 12px;
}

.pager a {
  color: #1f4ea3;
  text-decoration: none;
  border: 1px solid #b8c7e8;
  border-radius: 4px;
  padding: 2px 6px;
  background: #f4f8ff;
}

.pager .current {
  border: 1px solid #999;
  border-radius: 4px;
  padding: 2px 6px;
  background: #f0f0f0;
}

.content {
  padding: 8px 10px 14px;
}

.empty {
  border: 1px dashed var(--border);
  background: #fff;
  border-radius: 6px;
  padding: 10px;
  color: var(--muted);
}

.stream {
  display: block;
}

/* 1列モードでは左余白を持たせ、タブ単位のかたまり感を出す */
.tab-segment {
  margin: 6px 0;
  margin-left: var(--tab-indent, 0px);
  padding-left: 4px;
}

.tab-head {
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 4px;
  color: #444;
}

.msg {
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

.avatar-clickable {
  cursor: zoom-in;
}

.avatar-fallback {
  width: 32px;
  height: 32px;
  border-radius: 4px;
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e4e4e4;
  color: #555;
  font-size: 11px;
}

.msg-main {
  min-width: 0;
  flex: 1 1 auto;
}

.msg-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 1px;
}

.speaker {
  font-weight: 700;
}

.submeta {
  color: var(--muted);
  font-size: 11px;
}

.msg-text {
  white-space: pre-wrap;
  word-break: break-word;
}

/* 別列モード */
.tab-columns {
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
  margin: 0 0 4px;
  padding: 2px 4px;
  border-left: 4px solid var(--tab-accent, #999);
  background: var(--tab-bg, #f5f5f5);
  font-size: 12px;
  font-weight: 700;
}

.image-overlay {
  position: fixed;
  inset: 0;
  z-index: 999;
  display: none;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.75);
  padding: 16px;
}

.image-overlay.is-open {
  display: flex;
}

.image-overlay img {
  max-width: min(96vw, 1400px);
  max-height: 92vh;
  width: auto;
  height: auto;
  border-radius: 6px;
  background: #111;
}

@media (max-width: 640px) {
  .msg {
    gap: 6px;
    padding: 3px 4px;
  }
  .avatar, .avatar-fallback {
    width: 28px;
    height: 28px;
  }
}
"""


def build_html_js() -> str:
    """静的HTMLで使う軽量JSを返す。"""
    return """(() => {
  const overlay = document.getElementById("image-overlay");
  const overlayImage = document.getElementById("image-overlay-image");
  if (!overlay || !overlayImage) {
    return;
  }

  const closeOverlay = () => {
    overlay.classList.remove("is-open");
    overlay.setAttribute("aria-hidden", "true");
    overlayImage.removeAttribute("src");
  };

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const avatar = target.closest(".avatar-clickable");
    if (avatar instanceof HTMLImageElement) {
      const fullsrc = avatar.getAttribute("data-fullsrc") || avatar.getAttribute("src");
      if (!fullsrc) {
        return;
      }
      overlayImage.setAttribute("src", fullsrc);
      overlay.classList.add("is-open");
      overlay.setAttribute("aria-hidden", "false");
      return;
    }
    if (target === overlay) {
      closeOverlay();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeOverlay();
    }
  });
})();"""


def render_message_html(
    message: ChatMessage,
    speaker_color: str | None,
    image_src: str | None,
    show_timestamp: bool,
    show_speaker_id: bool,
    tab_bg: str,
    tab_accent: str,
) -> str:
    """1発言分のHTMLを作る。"""
    if image_src:
        avatar_html = (
            f'<img class="avatar avatar-clickable" src="{escape(image_src)}" data-fullsrc="{escape(image_src)}" '
            f'alt="{escape(message.speaker_name)} の画像" loading="lazy" />'
        )
    else:
        initial = message.speaker_name[:1] if message.speaker_name else "?"
        avatar_html = f'<div class="avatar-fallback">{escape(initial)}</div>'

    submeta_parts: List[str] = []
    if show_speaker_id:
        submeta_parts.append(f"ID: {escape(message.speaker_id)}")
    if show_timestamp:
        submeta_parts.append(f"時刻: {escape(format_timestamp(message.timestamp))}")
    submeta_html = ""
    if submeta_parts:
        submeta_html = f'<span class="submeta">{" / ".join(submeta_parts)}</span>'

    speaker_style = f' style="color:{speaker_color};"' if speaker_color else ""

    return (
        f'<article class="msg" style="--tab-bg:{tab_bg}; --tab-accent:{tab_accent};">'
        f"{avatar_html}"
        f'<div class="msg-main">'
        f'<div class="msg-head">'
        f'<span class="speaker"{speaker_style}>{escape(message.speaker_name)}</span>'
        f"{submeta_html}"
        f"</div>"
        f'<div class="msg-text">{escape(message.content)}</div>'
        f"</div>"
        f"</article>"
    )


def build_pager_html(page_index: int, total_pages: int) -> str:
    """ページャーHTMLを作る。"""
    # ページファイル名は、1ページ目だけ index.html にする。
    def file_name(idx: int) -> str:
        if idx == 0:
            return "index.html"
        return f"page-{idx + 1:03d}.html"

    parts: List[str] = ['<nav class="pager">']
    if total_pages <= 1:
        parts.append('<span class="current">1 / 1</span>')
        parts.append("</nav>")
        return "".join(parts)

    if page_index > 0:
        parts.append(f'<a href="{file_name(0)}">先頭</a>')
        parts.append(f'<a href="{file_name(page_index - 1)}">前へ</a>')

    # 現在ページの前後2ページだけ表示して、リンクを増やしすぎない。
    start = max(0, page_index - 2)
    end = min(total_pages, page_index + 3)
    for idx in range(start, end):
        if idx == page_index:
            parts.append(f'<span class="current">{idx + 1}</span>')
        else:
            parts.append(f'<a href="{file_name(idx)}">{idx + 1}</a>')

    if page_index < total_pages - 1:
        parts.append(f'<a href="{file_name(page_index + 1)}">次へ</a>')
        parts.append(f'<a href="{file_name(total_pages - 1)}">末尾</a>')

    parts.append(f'<span class="current">{page_index + 1} / {total_pages}</span>')
    parts.append("</nav>")
    return "".join(parts)


def render_stream_page_html(
    page_messages: Sequence[ChatMessage],
    tab_order: Sequence[str],
    show_timestamp: bool,
    show_speaker_id: bool,
    id_to_group_key: Dict[str, str],
    group_colors: Dict[str, str],
    image_map: Dict[str, str],
    use_native_colors: bool,
) -> str:
    """1段表示モードの本文HTMLを作る。"""
    if not page_messages:
        return '<div class="empty">このページに表示する発言がありません。</div>'

    parts: List[str] = ['<div class="stream">']
    current_tab: str | None = None
    segment_open = False
    tab_index = {tab_name: idx for idx, tab_name in enumerate(tab_order)}
    indent_step = 14

    for msg in page_messages:
        # タブが切り替わるたびにセグメントを区切る。
        if msg.tab_name != current_tab:
            if segment_open:
                parts.append("</section>")
            bg, accent = tab_palette(msg.tab_name)
            indent_px = tab_index.get(msg.tab_name, 0) * indent_step
            parts.append(
                f'<section class="tab-segment" style="--tab-bg:{bg}; --tab-accent:{accent}; --tab-indent:{indent_px}px;">'
                f'<div class="tab-head">タブ: {escape(msg.tab_name)}</div>'
            )
            segment_open = True
            current_tab = msg.tab_name

        color = resolve_speaker_color(
            message=msg,
            id_to_group_key=id_to_group_key,
            group_colors=group_colors,
            use_native_colors=use_native_colors,
        )
        image_src = image_map.get(msg.image_identifier)
        bg, accent = tab_palette(msg.tab_name)
        parts.append(
            render_message_html(
                message=msg,
                speaker_color=color,
                image_src=image_src,
                show_timestamp=show_timestamp,
                show_speaker_id=show_speaker_id,
                tab_bg=bg,
                tab_accent=accent,
            )
        )

    if segment_open:
        parts.append("</section>")
    parts.append("</div>")
    return "".join(parts)


def render_columns_page_html(
    page_messages: Sequence[ChatMessage],
    selected_tabs: Sequence[str],
    show_timestamp: bool,
    show_speaker_id: bool,
    id_to_group_key: Dict[str, str],
    group_colors: Dict[str, str],
    image_map: Dict[str, str],
    use_native_colors: bool,
) -> str:
    """タブ別列モードの本文HTMLを作る。"""
    # ページ内のメッセージをタブごとにまとめる。
    tab_to_messages: Dict[str, List[ChatMessage]] = defaultdict(list)
    for msg in page_messages:
        tab_to_messages[msg.tab_name].append(msg)

    if not page_messages:
        return '<div class="empty">このページに表示する発言がありません。</div>'

    parts: List[str] = ['<div class="tab-columns">']
    for tab_name in selected_tabs:
        tab_messages = tab_to_messages.get(tab_name, [])
        bg, accent = tab_palette(tab_name)
        parts.append(
            f'<section class="tab-column" style="--tab-bg:{bg}; --tab-accent:{accent};">'
            f'<h2 class="tab-column-title">{escape(tab_name)}</h2>'
        )
        if not tab_messages:
            parts.append('<div class="empty">このページには発言がありません。</div>')
        else:
            for msg in tab_messages:
                color = resolve_speaker_color(
                    message=msg,
                    id_to_group_key=id_to_group_key,
                    group_colors=group_colors,
                    use_native_colors=use_native_colors,
                )
                image_src = image_map.get(msg.image_identifier)
                parts.append(
                    render_message_html(
                        message=msg,
                        speaker_color=color,
                        image_src=image_src,
                        show_timestamp=show_timestamp,
                        show_speaker_id=show_speaker_id,
                        tab_bg=bg,
                        tab_accent=accent,
                    )
                )
        parts.append("</section>")
    parts.append("</div>")
    return "".join(parts)


def build_html_page(
    source_file_name: str,
    tab_order: Sequence[str],
    selected_tabs: Sequence[str],
    page_messages: Sequence[ChatMessage],
    page_index: int,
    total_pages: int,
    show_timestamp: bool,
    show_speaker_id: bool,
    separate_columns: bool,
    id_to_group_key: Dict[str, str],
    group_colors: Dict[str, str],
    image_map: Dict[str, str],
    use_native_colors: bool,
) -> str:
    """単一ページのHTML全文を作る。"""
    pager_top = build_pager_html(page_index, total_pages)
    pager_bottom = build_pager_html(page_index, total_pages)

    if separate_columns:
        content_html = render_columns_page_html(
            page_messages=page_messages,
            selected_tabs=selected_tabs,
            show_timestamp=show_timestamp,
            show_speaker_id=show_speaker_id,
            id_to_group_key=id_to_group_key,
            group_colors=group_colors,
            image_map=image_map,
            use_native_colors=use_native_colors,
        )
    else:
        content_html = render_stream_page_html(
            page_messages=page_messages,
            tab_order=tab_order,
            show_timestamp=show_timestamp,
            show_speaker_id=show_speaker_id,
            id_to_group_key=id_to_group_key,
            group_colors=group_colors,
            image_map=image_map,
            use_native_colors=use_native_colors,
        )

    tab_label = ", ".join(selected_tabs)
    mode_label = "タブ別列" if separate_columns else "時系列1列"

    return (
        "<!doctype html>"
        '<html lang="ja">'
        "<head>"
        '<meta charset="utf-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />'
        f"<title>Udonarium Log Viewer - {escape(source_file_name)} - {page_index + 1}/{total_pages}</title>"
        '<link rel="stylesheet" href="assets/style.css" />'
        "</head>"
        "<body>"
        '<header class="top">'
        '<div class="title">Udonarium Log Viewer</div>'
        f'<div class="meta">元ファイル: {escape(source_file_name)}</div>'
        f'<div class="meta">表示タブ: {escape(tab_label)}</div>'
        f'<div class="meta">表示モード: {escape(mode_label)}</div>'
        f'<div class="meta">ID表示: {"ON" if show_speaker_id else "OFF"} / 時刻表示: {"ON" if show_timestamp else "OFF"}</div>'
        f"{pager_top}"
        "</header>"
        '<main class="content">'
        f"{content_html}"
        f"{pager_bottom}"
        "</main>"
        '<div id="image-overlay" class="image-overlay" aria-hidden="true">'
        '<img id="image-overlay-image" alt="拡大画像" loading="lazy" />'
        "</div>"
        '<script src="assets/viewer.js"></script>'
        "</body>"
        "</html>"
    )


def build_human_output_text(
    source_file_name: str,
    messages: Sequence[ChatMessage],
    show_timestamp: bool,
    show_speaker_id: bool,
) -> str:
    """人間向けテキスト本文を作る。"""
    lines: List[str] = [f"元ファイル: {source_file_name}", ""]

    if not messages:
        lines.append("(発言なし)")
        return "\n".join(lines).rstrip() + "\n"

    current_tab: str | None = None
    for msg in messages:
        if msg.tab_name != current_tab:
            lines.append(f"=== タブ: {msg.tab_name} ===")
            current_tab = msg.tab_name

        meta_parts: List[str] = []
        if show_speaker_id:
            meta_parts.append(f"ID={msg.speaker_id}")
        if show_timestamp:
            meta_parts.append(f"時刻={format_timestamp(msg.timestamp)}")

        if meta_parts:
            lines.append(f"{msg.speaker_name} ({' / '.join(meta_parts)}): {msg.content}")
        else:
            lines.append(f"{msg.speaker_name}: {msg.content}")

    return "\n".join(lines).rstrip() + "\n"


def build_human_output_html(
    zip_path: Path,
    tab_order: Sequence[str],
    selected_tabs: Sequence[str],
    filtered_messages: Sequence[ChatMessage],
    show_timestamp: bool,
    show_speaker_id: bool,
    separate_columns: bool,
    messages_per_page: int,
    id_to_group_key: Dict[str, str],
    group_colors: Dict[str, str],
    use_native_colors: bool,
) -> Path:
    """
    人間向け静的HTMLを生成する。

    出力構成:
    - <zip名>_html/index.html
    - <zip名>_html/page-002.html ...
    - <zip名>_html/assets/style.css
    - <zip名>_html/assets/images/...
    """
    bundle_dir = make_unique_directory(zip_path.parent / f"{zip_path.stem}_html")
    assets_dir = bundle_dir / "assets"
    images_dir = assets_dir / "images"
    ensure_directory(images_dir)

    # 実際に表示する発言で使う画像だけ抽出する。
    image_map = extract_used_images(zip_path, filtered_messages, images_dir)

    # CSSを配置する。
    (assets_dir / "style.css").write_text(build_html_css(), encoding="utf-8")
    # アイコン拡大用の最小JSを配置する。
    (assets_dir / "viewer.js").write_text(build_html_js(), encoding="utf-8")

    # メッセージをページ分割する。
    pages = chunk_messages(filtered_messages, messages_per_page)
    total_pages = len(pages)

    # ページごとにHTMLを生成する。
    for page_index, page_messages in enumerate(pages):
        page_html = build_html_page(
            source_file_name=zip_path.name,
            tab_order=tab_order,
            selected_tabs=selected_tabs,
            page_messages=page_messages,
            page_index=page_index,
            total_pages=total_pages,
            show_timestamp=show_timestamp,
            show_speaker_id=show_speaker_id,
            separate_columns=separate_columns,
            id_to_group_key=id_to_group_key,
            group_colors=group_colors,
            image_map=image_map,
            use_native_colors=use_native_colors,
        )
        file_name = "index.html" if page_index == 0 else f"page-{page_index + 1:03d}.html"
        (bundle_dir / file_name).write_text(page_html, encoding="utf-8")

    return bundle_dir / "index.html"


def run() -> int:
    """メイン処理。"""
    args = parse_args()
    config = load_config(Path(args.config))

    root = create_hidden_root()
    try:
        # 1. 入力zipを決める。
        show_progress("入力zipを選択してください...")
        zip_path = choose_input_zip(root, args.input_zip)
        if zip_path is None:
            print("入力zipの選択がキャンセルされました。")
            return 0
        if not zip_path.exists():
            print(f"[ERROR] 入力zipが見つかりません: {zip_path}", file=sys.stderr)
            return 1
        if zip_path.suffix.lower() != ".zip":
            print(f"[ERROR] 入力ファイルがzipではありません: {zip_path}", file=sys.stderr)
            return 1

        # 2. chat XMLを解析する。
        show_progress("chat XML を解析しています...")
        root_xml, chat_file_name = load_chat_root_from_zip(zip_path)
        tab_names, messages = extract_tabs_and_messages(root_xml)
        sorted_messages = sort_messages_chronologically(messages)
        show_progress(
            f"解析完了: {chat_file_name} / 発言 {len(sorted_messages)} 件 / タブ {len(tab_names)} 個"
        )

        # 3. 話者色を決める。
        generate_html = bool(config["outputs"]["human_html"])
        use_native_colors = generate_html and chat_file_name.lower() == "fly_chat.xml"
        id_to_group_key: Dict[str, str] = {}
        chosen_colors: Dict[str, str] = {}
        if not generate_html:
            show_progress("HTML出力がOFFのため、色設定処理はスキップします。")
        elif use_native_colors:
            show_progress("Udonarium_fly 形式を検出しました。ログ内の色設定をそのまま使います。")
        else:
            show_progress("発言者グループを作成しています...")
            groups, id_to_group_key = build_speaker_groups(
                messages=sorted_messages,
                grouping_config=config["speaker_grouping"],
            )
            show_progress(f"発言者グループ {len(groups)} 件。色選択ダイアログを表示します...")
            chosen_colors = show_speaker_color_dialog(
                root=root,
                groups=groups,
                preview_max_chars=int(config["ui"]["speaker_alias_preview_max_chars"]),
            )
            if chosen_colors is None:
                print("色選択がキャンセルされました。")
                return 0

        # 4. タブ選択と列モードを選んでもらう。
        show_progress("タブ選択ダイアログを表示します...")
        tab_counts = Counter(msg.tab_name for msg in sorted_messages)
        selected = show_tab_selection_dialog(
            root=root,
            tab_names=tab_names,
            tab_counts=dict(tab_counts),
            separate_columns_default=bool(config["html"]["separate_tabs_columns_default"]),
        )
        if selected is None:
            print("タブ選択がキャンセルされました。")
            return 0
        selected_tabs, separate_columns = selected

        # 選択されたタブだけへ絞り込む。
        selected_tab_set = set(selected_tabs)
        filtered_messages = [m for m in sorted_messages if m.tab_name in selected_tab_set]
        show_progress(f"タブ絞り込み後: 発言 {len(filtered_messages)} 件")

        # 5. 出力を行う。
        show_progress("出力を作成しています...")
        created_paths: List[Path] = []
        show_timestamp = bool(config["common"]["show_timestamp"])
        show_speaker_id = bool(config["common"]["show_speaker_id"])

        if bool(config["outputs"]["human_text"]):
            text_path = make_unique_path(zip_path.with_suffix(".txt"))
            text_body = build_human_output_text(
                source_file_name=zip_path.name,
                messages=filtered_messages,
                show_timestamp=show_timestamp,
                show_speaker_id=show_speaker_id,
            )
            text_path.write_text(text_body, encoding="utf-8")
            created_paths.append(text_path)

        if bool(config["outputs"]["human_html"]):
            html_index_path = build_human_output_html(
                zip_path=zip_path,
                tab_order=tab_names,
                selected_tabs=selected_tabs,
                filtered_messages=filtered_messages,
                show_timestamp=show_timestamp,
                show_speaker_id=show_speaker_id,
                separate_columns=separate_columns,
                messages_per_page=int(config["html"]["messages_per_page"]),
                id_to_group_key=id_to_group_key,
                group_colors=chosen_colors,
                use_native_colors=use_native_colors,
            )
            created_paths.append(html_index_path)

        if not created_paths:
            print("[ERROR] 出力設定がすべてOFFです。config.jsonを見直してください。", file=sys.stderr)
            return 1

        # 最後に出力先を表示する。
        print("完了:")
        for path in created_paths:
            print(f"  - {path}")
        return 0
    finally:
        # ルートを確実に閉じる。
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(run())
