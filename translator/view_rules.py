from ..parser.resource_resolver import ResourceResolver
from ..utils import indent, apply_layout_modifiers, escape_dart, get_asset_path_from_drawable


def _id_base(v: str) -> str:
    if not v:
        return ""
    return v.split("/")[-1]


def _to_camel(s: str) -> str:
    if not s:
        return s
    parts = s.replace("-", "_").split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _to_snake(s: str) -> str:
    if not s:
        return s
    out = []
    for ch in s:
        if ch.isupper():
            if out:
                out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def _handler_key_candidates(xml_id: str):
    if not xml_id:
        return []
    return [
        xml_id,
        xml_id.lower(),
        xml_id.capitalize(),
        _to_camel(xml_id),
        _to_snake(xml_id),
    ]


def _find_handler(logic_map: dict, xml_id: str):
    if not logic_map or not xml_id:
        return None
    for k in _handler_key_candidates(xml_id):
        if k in logic_map:
            return logic_map[k]
    return None


def _text_style(attrs: dict, resolver: ResourceResolver | None) -> str:
    """TextView 用: textSize / textColor を TextStyle に変換."""
    parts = []

    # textSizeの処理
    size_raw = attrs.get("textSize")
    if size_raw:
        if resolver:
            resolved = resolver.resolve(size_raw) or size_raw
            try:
                size_px = resolver.parse_dimen_to_px(resolved)
            except Exception:
                size_px = None
        else:
            # resolverがない場合も直接数値として解析を試みる
            try:
                size_px = ResourceResolver.parse_dimen_to_px(size_raw)
            except Exception:
                size_px = None
            if size_px:
                parts.append(f"fontSize: {float(size_px):.1f}")

    # textColorの処理（@color/xxx または直接 #RRGGBB 形式）
    color_raw = attrs.get("textColor")
    if color_raw:
        if resolver:
            resolved_c = resolver.resolve(color_raw) or color_raw
        else:
            resolved_c = color_raw
        color_hex = ResourceResolver.android_color_to_flutter(resolved_c)
        if color_hex:
            parts.append(f"color: Color({color_hex})")

    if not parts:
        return ""
    return ", style: TextStyle(" + ", ".join(parts) + ")"


def translate_view(
    node: dict,
    resolver: ResourceResolver | None,
    logic_map: dict | None,
):
    """個々の View を Flutter の Widget コードに変換する."""
    if logic_map is None:
        logic_map = {}

    t = node.get("type") or ""
    attrs = node.get("attrs") or {}
    children = node.get("children") or []

    # ================== Button 系 ==================
    if t.lower().endswith("button") or t == "Button":
        xml_id = _id_base(attrs.get("id", ""))

        # ラベル（@string/ を values から解決）
        label_raw = attrs.get("text", "")
        label = resolver.resolve(label_raw) if resolver else label_raw
        label = label or "Button"

        # textColor（@color/xxx または直接 #RRGGBB 形式）
        text_color_raw = attrs.get("textColor")
        text_style_part = ""
        if text_color_raw:
            if resolver:
                resolved_tc = resolver.resolve(text_color_raw) or text_color_raw
            else:
                resolved_tc = text_color_raw
            text_color_hex = ResourceResolver.android_color_to_flutter(resolved_tc)
            if text_color_hex:
                text_style_part = (
                    f", style: TextStyle(color: Color({text_color_hex}))"
                )

        label_widget = f'Text("{escape_dart(label)}"{text_style_part})'

        # backgroundTint / background → ElevatedButton.styleFrom(backgroundColor)
        bg_raw = attrs.get("backgroundTint") or attrs.get("background")
        style_part = ""
        if bg_raw:
            if resolver:
                resolved_bg = resolver.resolve(bg_raw) or bg_raw
            else:
                resolved_bg = bg_raw
            bg_color_hex = ResourceResolver.android_color_to_flutter(resolved_bg)
            if bg_color_hex:
                style_part = (
                    f", style: ElevatedButton.styleFrom("
                    f"backgroundColor: Color({bg_color_hex}))"
                )

        handler_name = _find_handler(logic_map, xml_id)
        if not handler_name:
            camel = _to_camel(xml_id)
            handler_name = (
                f"_on{camel[:1].upper()}{camel[1:]}Pressed"
                if camel
                else "_onUnknownPressed"
            )

        body = (
            "ElevatedButton("
            f"onPressed: () => {handler_name}(context), "
            f"child: {label_widget}"
            f"{style_part}"
            ")"
        )
        return apply_layout_modifiers(body, attrs, resolver)

    # ================== TextView ==================
    if t == "TextView":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)

        text_raw = attrs.get("text", "")
        text = resolver.resolve(text_raw) if resolver else text_raw
        text = text or ""

        body = f'Text("{escape_dart(text)}"{_text_style(attrs, resolver)})'

        # XML の android:onClick を拾ってフォールバック名へ接続
        xml_onclick = attrs.get("onClick") or attrs.get("android:onClick")
        if handler_name:
            body = f'InkWell(onTap: () => {handler_name}(context), child: {body})'
        elif xml_onclick:
            camel = _to_camel(xml_id)
            fallback = (
                f"_on{camel[:1].upper()}{camel[1:]}Pressed"
                if camel
                else "_onUnknownPressed"
            )
            body = f'InkWell(onTap: () => {fallback}(context), child: {body})'
        elif (attrs.get("clickable", "") or "").lower() == "true":
            # clickable=true だが Java 側で検出できなかった場合は
            # 見た目だけボタン化（論理は null）
            body = f'TextButton(onPressed: null, child: {body})'
        return apply_layout_modifiers(body, attrs, resolver)

    # ================== EditText 系 ==================
    if t == "EditText" or t.endswith("EditText"):
        hint_raw = attrs.get("hint", "")
        hint = resolver.resolve(hint_raw) if resolver else hint_raw
        hint = hint or ""

        input_type = (attrs.get("inputType") or "").lower()
        obscure = "textpassword" in input_type or "password" in hint.lower()

        dec = f'InputDecoration(hintText: "{escape_dart(hint)}")' if hint else "null"
        parts = [f"decoration: {dec}"]
        if obscure:
            parts.append("obscureText: true")

        body = f"TextField({', '.join(parts)})"
        return apply_layout_modifiers(body, attrs, resolver)

    # ================== ImageView ==================
    if t.endswith("ImageView") or t == "AppCompatImageView":
        # src属性から画像を取得（android:src または app:srcCompat）
        src_raw = attrs.get("srcCompat") or attrs.get("src") or attrs.get("android:src")
        if src_raw and resolver:
            # drawableリソースを解決
            drawable_path = resolver.resolve_drawable_path(src_raw)
            if drawable_path:
                # Flutterのアセットパスに変換
                asset_path = get_asset_path_from_drawable(drawable_path)
                if asset_path:
                    # scaleTypeの処理
                    scale_type = (attrs.get("scaleType") or attrs.get("android:scaleType") or "fitCenter").lower()
                    box_fit = "BoxFit.cover"  # デフォルト
                    if "centerCrop" in scale_type:
                        box_fit = "BoxFit.cover"
                    elif "centerInside" in scale_type or "center" in scale_type:
                        box_fit = "BoxFit.contain"
                    elif "fitXY" in scale_type:
                        box_fit = "BoxFit.fill"
                    elif "fitStart" in scale_type:
                        box_fit = "BoxFit.fitWidth"
                    elif "fitEnd" in scale_type:
                        box_fit = "BoxFit.fitHeight"
                    
                    # 背景画像として使われる可能性があるかどうかを判定（match_parentの場合）
                    width = (attrs.get("layout_width") or "").lower()
                    height = (attrs.get("layout_height") or "").lower()
                    is_background = width in ("match_parent", "fill_parent") and height in ("match_parent", "fill_parent")
                    
                    # 背景画像の場合は、errorBuilderを画面全体をカバーするように設定
                    if is_background:
                        # 背景画像の場合、errorBuilderのContainerからwidth/heightを削除
                        # Positioned.fillがサイズを決定するため
                        error_builder = "errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey[300], child: Icon(Icons.image, size: 80, color: Colors.grey[600]))"
                    else:
                        # 通常のImageViewの場合、固定サイズのerrorBuilderを使用
                        error_builder = "errorBuilder: (context, error, stackTrace) => Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey[600]))"
                    
                    # アセット画像を使用（アセットが存在しない場合のエラーを避けるため、プレースホルダーも用意）
                    # 実際のアプリでは、アセットを追加するか、ネットワーク画像を使用する
                    body = f"Image.asset('{asset_path}', fit: {box_fit}, {error_builder})"
                else:
                    # アセットパスが解決できない場合、プレースホルダーを表示
                    body = "Center(child: Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey[600])))"
            else:
                # drawableパスが解決できない場合、プレースホルダーを表示
                body = "Center(child: Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey[600])))"
        else:
            # src属性がない場合、プレースホルダーを表示
            body = "Center(child: Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey[300], borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey[600])))"
        return apply_layout_modifiers(body, attrs, resolver)

    # ================== fallback ==================
    body = f"/* TODO: translate {t} */ SizedBox()"
    return apply_layout_modifiers(body, attrs, resolver)
