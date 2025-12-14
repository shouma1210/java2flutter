from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .parser.resource_resolver import ResourceResolver


def indent(code: str, spaces: int = 2) -> str:
    """指定したスペース数で各行をインデントするユーティリティ."""
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in code.splitlines())


def escape_dart(s: str) -> str:
    """Dart の文字列リテラル内で問題が出ないように最低限のエスケープを行う."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _parse_dimen(value: str, resolver: Optional[ResourceResolver]) -> Optional[float]:
    """'16dp' や '@dimen/...' などをざっくり数値に変換する補助.

    正確さよりも「ある程度の距離感」が分かればよいので、
    うまく解釈できなければ None を返す。
    """
    if not value:
        return None

    if resolver:
        try:
            resolved = resolver.resolve(value) or value
            px = resolver.parse_dimen_to_px(resolved)
            if px is not None:
                return float(px)
        except Exception:
            pass

    m = re.match(r"([0-9.]+)", value)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def _edge_insets_from_attrs(
    attrs: Dict[str, Any],
    resolver: Optional[ResourceResolver],
    prefix: str,
) -> Optional[str]:
    """layout_margin*, padding* から EdgeInsets.only(...) を生成する補助."""
    keys = {
        "Left": 0.0,
        "Top": 0.0,
        "Right": 0.0,
        "Bottom": 0.0,
    }
    found = False

    base = attrs.get(prefix)
    if base:
        v = _parse_dimen(base, resolver)
        if v:
            for k in keys:
                keys[k] = v
            found = True

    for suffix in ["Left", "Start"]:
        v = attrs.get(f"{prefix}{suffix}")
        if v:
            px = _parse_dimen(v, resolver)
            if px is not None:
                keys["Left"] = px
                found = True

    v = attrs.get(f"{prefix}Top")
    if v:
        px = _parse_dimen(v, resolver)
        if px is not None:
            keys["Top"] = px
            found = True

    for suffix in ["Right", "End"]:
        v = attrs.get(f"{prefix}{suffix}")
        if v:
            px = _parse_dimen(v, resolver)
            if px is not None:
                keys["Right"] = px
                found = True

    v = attrs.get(f"{prefix}Bottom")
    if v:
        px = _parse_dimen(v, resolver)
        if px is not None:
            keys["Bottom"] = px
            found = True

    if not found:
        return None

    left = keys["Left"]
    top = keys["Top"]
    right = keys["Right"]
    bottom = keys["Bottom"]

    return f"EdgeInsets.fromLTRB({left}, {top}, {right}, {bottom})"


def get_asset_path_from_drawable(drawable_path: str) -> Optional[str]:
    """drawableファイルパスからFlutterのアセットパスを生成"""
    if not drawable_path:
        return None
    
    # drawableディレクトリ名とファイル名を抽出
    # 例: /path/to/res/drawable/bg.png -> assets/images/bg.png
    # または: /path/to/res/drawable-hdpi/bg.png -> assets/images/bg.png
    import os
    filename = os.path.basename(drawable_path)
    # 拡張子を保持したまま、assets/images/に配置する想定
    return f"assets/images/{filename}"


def apply_layout_modifiers(
    widget: str,
    attrs: Dict[str, Any],
    resolver: Optional[ResourceResolver],
) -> str:
    """共通レイアウト属性を Flutter 側のラッパーで表現する.

    - layout_margin*        → Padding（外側）
    - padding*              → Padding（内側）
    - background(@color/..) → Container(color: ...)
    - background(@drawable/..) → Container(decoration: BoxDecoration(image: ...))
    """
    if attrs is None:
        attrs = {}

    # 1) background (色または画像を Container で反映)
    # 注意: 背景色はContainerでラップするが、画面全体に広がるようにするため、
    # ルートレベルの背景色はScaffoldのbackgroundColorに設定する方が良い
    bg_raw = attrs.get("background")
    if bg_raw:
        # まずdrawableとして解決を試みる（resolverがある場合のみ）
        drawable_path = None
        if resolver:
            drawable_path = resolver.resolve_drawable_path(bg_raw)
        
        if drawable_path:
            # 背景画像の場合
            asset_path = get_asset_path_from_drawable(drawable_path)
            if asset_path:
                # SingleChildScrollViewと衝突しないように、width/heightを指定しない
                widget = (
                    f"Container("
                    f"decoration: BoxDecoration("
                    f"image: DecorationImage("
                    f"image: AssetImage('{asset_path}'), "
                    f"fit: BoxFit.cover"
                    f")"
                    f"), "
                    f"child: {widget}"
                    f")"
                )
            else:
                widget = f"/* TODO: background image {bg_raw} */ {widget}"
        else:
            # 色として解決を試みる（@color/xxx または直接 #RRGGBB 形式）
            if resolver:
                resolved = resolver.resolve(bg_raw) or bg_raw
            else:
                resolved = bg_raw
            color_hex = ResourceResolver.android_color_to_flutter(resolved)
        if color_hex:
                # 背景色をContainerでラップする
                # ただし、ルートレベルの背景色はScaffoldのbackgroundColorに設定する方が良い
                # ここでは単純にContainerでラップする（SizedBox.expandは使わない）
                widget = f"Container(color: Color({color_hex}), child: {widget})"
        else:
                # 解決できない場合は TODO コメント
                widget = f"/* TODO: background {bg_raw} */ {widget}"

    # 2) padding（内側）
    padding_ei = _edge_insets_from_attrs(attrs, resolver, "padding")
    if padding_ei:
        widget = f"Padding(padding: {padding_ei}, child: {widget})"

    # 3) margin（外側）
    margin_ei = _edge_insets_from_attrs(attrs, resolver, "layout_margin")
    if margin_ei:
        widget = f"Padding(padding: {margin_ei}, child: {widget})"

    # Gravity / layout_gravity などはレイアウト側のルールに任せる
    return widget
