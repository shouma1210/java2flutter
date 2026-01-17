from __future__ import annotations

import re
import os
from typing import Any, Dict, Optional
from lxml import etree

from parser.resource_resolver import ResourceResolver


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
    filename = os.path.basename(drawable_path)
    # 拡張子を保持したまま、assets/images/に配置する想定
    return f"assets/images/{filename}"


def _parse_shape_drawable_to_boxdecoration(xml_path: str, resolver: Optional[ResourceResolver]) -> Optional[str]:
    """XML形式のshape drawableをFlutterのBoxDecorationに変換"""
    if not xml_path or not xml_path.lower().endswith(".xml"):
        return None
    
    if not os.path.exists(xml_path):
        return None
    
    try:
        tree = etree.parse(xml_path)
        root = tree.getroot()
        
        # shape要素を探す（名前空間あり/なしの両方に対応）
        shape = root.find(".//{http://schemas.android.com/apk/res/android}shape")
        if shape is None:
            # 名前空間なしのshape要素も探す
            shape = root.find(".//shape")
        if shape is None:
            # shape要素がない場合はNoneを返す
            return None
        
        decoration_parts = []
        
        # solid要素（背景色）
        solid = shape.find(".//{http://schemas.android.com/apk/res/android}solid")
        if solid is None:
            # 名前空間なしのsolid要素も探す
            solid = shape.find(".//solid")
        if solid is not None:
            # 属性はandroid:プレフィックス付きで取得
            color_attr = solid.get("{http://schemas.android.com/apk/res/android}color")
            if not color_attr:
                # android:プレフィックスなしでも試す
                color_attr = solid.get("color")
            if color_attr:
                # 色を解決（@color/xxx または直接 #RRGGBB 形式）
                # ?attr/xxx のようなテーマ属性の場合はフォールバック色を使用
                if color_attr.startswith("?attr/"):
                    # テーマ属性の場合はデフォルト色を使用（TODO: テーマから取得するように改善可能）
                    # 例: ?attr/colorOnPrimary -> Colors.white など
                    # ここでは一旦グレーを使用
                    decoration_parts.append("color: Colors.grey.shade200")
                else:
                    if resolver:
                        resolved_color = resolver.resolve(color_attr) or color_attr
                    else:
                        resolved_color = color_attr
                    color_hex = ResourceResolver.android_color_to_flutter(resolved_color)
                    if color_hex:
                        decoration_parts.append(f"color: Color({color_hex})")
                    elif not color_attr.startswith("?"):
                        # 解決できないが、テーマ属性でもない場合はデフォルト色を使用
                        decoration_parts.append("color: Colors.grey.shade200")
        
        # corners要素（角丸）
        corners = shape.find(".//{http://schemas.android.com/apk/res/android}corners")
        if corners is None:
            # 名前空間なしのcorners要素も探す
            corners = shape.find(".//corners")
        if corners is not None:
            # 個別の角丸半径を取得
            top_left = corners.get("{http://schemas.android.com/apk/res/android}topLeftRadius") or corners.get("topLeftRadius")
            top_right = corners.get("{http://schemas.android.com/apk/res/android}topRightRadius") or corners.get("topRightRadius")
            bottom_left = corners.get("{http://schemas.android.com/apk/res/android}bottomLeftRadius") or corners.get("bottomLeftRadius")
            bottom_right = corners.get("{http://schemas.android.com/apk/res/android}bottomRightRadius") or corners.get("bottomRightRadius")
            radius_attr = corners.get("{http://schemas.android.com/apk/res/android}radius") or corners.get("radius")
            
            # 個別の角丸半径が指定されている場合
            if top_left or top_right or bottom_left or bottom_right:
                radius_values = []
                if top_left:
                    top_left_val = _parse_dimen(top_left, resolver)
                    if top_left_val:
                        radius_values.append(f"topLeft: {top_left_val}")
                if top_right:
                    top_right_val = _parse_dimen(top_right, resolver)
                    if top_right_val:
                        radius_values.append(f"topRight: {top_right_val}")
                if bottom_left:
                    bottom_left_val = _parse_dimen(bottom_left, resolver)
                    if bottom_left_val:
                        radius_values.append(f"bottomLeft: {bottom_left_val}")
                if bottom_right:
                    bottom_right_val = _parse_dimen(bottom_right, resolver)
                    if bottom_right_val:
                        radius_values.append(f"bottomRight: {bottom_right_val}")
                
                if radius_values:
                    decoration_parts.append(f"borderRadius: BorderRadius.only({', '.join(radius_values)})")
            elif radius_attr:
                # 全角に同じ半径が指定されている場合
                radius_val = _parse_dimen(radius_attr, resolver)
                if radius_val:
                    decoration_parts.append(f"borderRadius: BorderRadius.circular({radius_val})")
        
        # stroke要素（境界線）
        stroke = shape.find(".//{http://schemas.android.com/apk/res/android}stroke")
        if stroke is None:
            # 名前空間なしのstroke要素も探す
            stroke = shape.find(".//stroke")
        if stroke is not None:
            width_attr = stroke.get("{http://schemas.android.com/apk/res/android}width") or stroke.get("width")
            color_attr = stroke.get("{http://schemas.android.com/apk/res/android}color") or stroke.get("color")
            if width_attr and color_attr:
                width_val = _parse_dimen(width_attr, resolver)
                # テーマ属性の場合はフォールバック色を使用
                if color_attr.startswith("?attr/"):
                    # テーマ属性の場合はデフォルト色を使用
                    if width_val:
                        decoration_parts.append(f"border: Border.all(width: {width_val}, color: Colors.grey.shade400)")
                else:
                    if resolver:
                        resolved_color = resolver.resolve(color_attr) or color_attr
                    else:
                        resolved_color = color_attr
                    color_hex = ResourceResolver.android_color_to_flutter(resolved_color)
                    if width_val and color_hex:
                        decoration_parts.append(f"border: Border.all(width: {width_val}, color: Color({color_hex}))")
        
        if decoration_parts:
            return f"BoxDecoration({', '.join(decoration_parts)})"
        else:
            # decoration_partsが空の場合は、最低限の色を設定
            # デバッグ: なぜ空なのか確認
            import traceback
            print(f"[DEBUG] decoration_parts is empty for {xml_path}")
            return "BoxDecoration(color: Colors.grey.shade200)"
    except Exception as e:
        # 解析に失敗した場合はデフォルトのBoxDecorationを返す
        import traceback
        print(f"[WARN] Failed to parse shape drawable {xml_path}: {e}")
        traceback.print_exc()
        return "BoxDecoration(color: Colors.grey.shade200)"


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
            # XML形式のdrawableリソースか画像ファイルかを判定
            if drawable_path.lower().endswith(".xml"):
                # XML形式のdrawableリソース（shape drawableなど）の場合
                # XMLを解析してBoxDecorationに変換を試みる
                decoration_code = _parse_shape_drawable_to_boxdecoration(drawable_path, resolver)
                if decoration_code:
                    widget = (
                        f"Container("
                        f"decoration: {decoration_code}, "
                        f"child: {widget}"
                        f")"
                    )
                else:
                    # 解析できない場合でも、最低限のBoxDecorationを適用
                    widget = (
                        f"Container("
                        f"decoration: BoxDecoration(color: Colors.grey.shade200), "
                        f"child: {widget}"
                        f")"
                    )
            else:
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
