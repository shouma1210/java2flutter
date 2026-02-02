from parser.resource_resolver import ResourceResolver
from utils import indent, apply_layout_modifiers, escape_dart, get_asset_path_from_drawable, _parse_shape_drawable_to_boxdecoration, _parse_dimen


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


def translate_view(node: dict, resolver, logic_map=None, fragments_by_id=None, layout_dir=None, values_dir=None):
    """ViewノードをFlutterウィジェットに変換"""
    if logic_map is None:
        logic_map = {}

    t = node.get("type") or ""
    attrs = node.get("attrs") or {}
    children = node.get("children") or []

    # ================== CardView / MaterialCardView ==================
    CARDVIEW_TYPES = {
        "androidx.cardview.widget.CardView",
        "android.support.v7.widget.CardView",
        "com.google.android.material.card.MaterialCardView",
        "CardView",
        "MaterialCardView",
    }
    
    if t in CARDVIEW_TYPES or t.endswith("CardView") or t.endswith("MaterialCardView"):
        # 子要素を再帰的に変換
        # 循環インポートを避けるため、ここでインポート
        from translator.layout_rules import translate_node
        dart_children = []
        if children:
            for ch in children:
                child_code = translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                dart_children.append(child_code)
        
        # 子要素が1つの場合はそのまま、複数の場合はColumnでラップ
        if len(dart_children) == 1:
            child_code = dart_children[0]
        elif len(dart_children) > 1:
            children_joined = ",\n".join(dart_children)
            child_code = f"Column(children: [\n{indent(children_joined)}\n])"
        else:
            child_code = "SizedBox.shrink()"
        
        # CardViewの属性を取得
        bg_color_raw = attrs.get("cardBackgroundColor") or attrs.get("app:cardBackgroundColor") or attrs.get("cardBackgroundColor")
        radius_raw = attrs.get("cardCornerRadius") or attrs.get("app:cardCornerRadius") or attrs.get("cardCornerRadius", "0dp")
        stroke_color_raw = attrs.get("strokeColor") or attrs.get("app:strokeColor")
        stroke_width_raw = attrs.get("strokeWidth") or attrs.get("app:strokeWidth", "0dp")
        elevation_raw = attrs.get("cardElevation") or attrs.get("app:cardElevation", "0dp")
        
        # 色を解決
        bg_color = None
        if bg_color_raw and resolver:
            resolved_bg = resolver.resolve(bg_color_raw) or bg_color_raw
            bg_color = ResourceResolver.android_color_to_flutter(resolved_bg)
        
        # 角丸を取得
        radius_val = _parse_dimen(radius_raw, resolver) or 0.0
        
        # 枠線の色を取得
        stroke_color = None
        if stroke_color_raw and resolver:
            resolved_stroke = resolver.resolve(stroke_color_raw) or stroke_color_raw
            stroke_color = ResourceResolver.android_color_to_flutter(resolved_stroke)
        
        # 枠線の幅を取得
        stroke_width_val = _parse_dimen(stroke_width_raw, resolver) or 0.0
        
        # elevationを取得（FlutterのCardではelevationは影の高さ）
        elevation_val = _parse_dimen(elevation_raw, resolver) or 0.0
        
        # Cardウィジェットを生成
        card_parts = []
        
        # color
        if bg_color:
            card_parts.append(f"color: Color({bg_color})")
        
        # shape
        shape_parts = [f"borderRadius: BorderRadius.circular({radius_val})"]
        if stroke_color and stroke_width_val > 0:
            shape_parts.append(f"side: BorderSide(color: Color({stroke_color}), width: {stroke_width_val})")
        
        if shape_parts:
            card_parts.append(f"shape: RoundedRectangleBorder({', '.join(shape_parts)})")
        
        # elevation
        if elevation_val > 0:
            card_parts.append(f"elevation: {elevation_val}")
        else:
            card_parts.append("elevation: 0")
        
        # child
        card_parts.append(f"child: {child_code}")
        
        body = f"Card({', '.join(card_parts)})"
        
        # CardViewの属性を処理済みなので、apply_layout_modifiersで重複処理しないようにする
        attrs_copy = attrs.copy()
        attrs_copy.pop("cardBackgroundColor", None)
        attrs_copy.pop("app:cardBackgroundColor", None)
        attrs_copy.pop("cardCornerRadius", None)
        attrs_copy.pop("app:cardCornerRadius", None)
        attrs_copy.pop("strokeColor", None)
        attrs_copy.pop("app:strokeColor", None)
        attrs_copy.pop("strokeWidth", None)
        attrs_copy.pop("app:strokeWidth", None)
        attrs_copy.pop("cardElevation", None)
        attrs_copy.pop("app:cardElevation", None)
        
        return apply_layout_modifiers(body, attrs_copy, resolver)
    
    # ================== RadioButton ==================
# ================== RadioButton ==================
    if t == "RadioButton":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)
        
        text_raw = attrs.get("text", "")
        text = resolver.resolve(text_raw) if resolver else text_raw
        text = text or ""
        
        checked = (attrs.get("checked") or "").lower() == "true"
        
        # android:button="@null"の場合、通常のボタンのように表示（水平方向のRadioGroup用）
        button_attr = attrs.get("button") or attrs.get("android:button")
        is_button_null = button_attr == "@null" or button_attr == "null"
        
        if is_button_null:
            # android:button="@null"の場合、選択可能なボタンとして表示
            # サイズとテキストサイズを取得
            width_raw = attrs.get("layout_width") or attrs.get("width") or "wrap_content"
            height_raw = attrs.get("layout_height") or attrs.get("height") or "wrap_content"
            text_size_raw = attrs.get("textSize") or attrs.get("android:textSize") or "14sp"
            
            width_val = _parse_dimen(width_raw, resolver) or 60.0
            height_val = _parse_dimen(height_raw, resolver) or 60.0
            text_size_val = _parse_dimen(text_size_raw, resolver) or 24.0
            
            # 背景drawableを取得（mood_selectorなど）
            bg_raw = attrs.get("background") or attrs.get("android:background")
            bg_decoration = ""
            if bg_raw and resolver:
                drawable_path = resolver.resolve_drawable_path(bg_raw)
                if drawable_path and drawable_path.lower().endswith(".xml"):
                    bg_decoration = _parse_shape_drawable_to_boxdecoration(drawable_path, resolver)
            
            if bg_decoration:
                body = f'Container(width: {width_val}, height: {height_val}, decoration: {bg_decoration}, child: Center(child: Text("{escape_dart(text)}", style: TextStyle(fontSize: {text_size_val}))))'
            else:
                body = f'Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(border: Border.all(color: Colors.grey), borderRadius: BorderRadius.circular(4)), child: Center(child: Text("{escape_dart(text)}", style: TextStyle(fontSize: {text_size_val}))))'
            
            # android:button="@null"の場合は、background属性を既に処理済みなので、
            # apply_layout_modifiersでbackgroundを処理しないようにするため、
            # background属性を一時的に削除してからapply_layout_modifiersを呼ぶ
            # layout_marginEndはapply_layout_modifiersで処理される
            attrs_copy = attrs.copy()
            attrs_copy.pop("background", None)
            attrs_copy.pop("android:background", None)
            return apply_layout_modifiers(body, attrs_copy, resolver)
        else:
            # 通常のRadioButtonはRadioListTileで表現
            if text:
                body = f'RadioListTile(value: "{xml_id}", groupValue: null, onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); }}, title: Text("{escape_dart(text)}"))'
            else:
                body = f'Radio(value: "{xml_id}", groupValue: null, onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); }})'
        
        if handler_name:
            body = body.replace('onChanged: (value) { setState(() { /* TODO: update state */ }); }', 
                              f'onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); {handler_name}(context); }}')
        
        return apply_layout_modifiers(body, attrs, resolver)
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
                # テキスト色も設定（Androidのデフォルトはダークグレー/黒）
                style_part = (
                    f", style: ElevatedButton.styleFrom("
                    f"backgroundColor: Color({bg_color_hex}), foregroundColor: Colors.black87)"
                )

        # android:onClick属性をチェック（優先度を高くする）
        xml_onclick = attrs.get("onClick") or attrs.get("android:onClick")
        
        # android:onClick属性がある場合は、そのメソッド名からハンドラー名を生成（logic_mapより優先）
        if xml_onclick:
            # onClickメソッド名が既に"on"で始まっている場合は、そのまま使用
            camel = xml_onclick
            if camel.startswith("on"):
                camel = camel[2:]  # "on"を削除
            camel = _to_camel(camel)
            handler_name = (
                f"_on{camel[:1].upper()}{camel[1:]}Pressed"
                if camel
                else "_onUnknownPressed"
            )
        else:
            # android:onClick属性がない場合、logic_mapから検索
            handler_name = _find_handler(logic_map, xml_id)
            if not handler_name:
                # logic_mapにも見つからない場合、ボタンIDからハンドラー名を生成
                camel = _to_camel(xml_id)
                handler_name = (
                    f"_on{camel[:1].upper()}{camel[1:]}Pressed"
                    if camel
                    else "_onUnknownPressed"
                )

        # Android側に背景色が設定されていない場合、Androidのデフォルトボタン色（薄いグレー）とテキスト色（ダークグレー）を設定
        if not style_part:
            style_part = ", style: ElevatedButton.styleFrom(backgroundColor: Colors.grey.shade300, foregroundColor: Colors.black87)"
        
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
        # text属性がない場合、idがあればプレースホルダーを表示
        if not text and xml_id:
            text = f"[{xml_id}]"  # プレースホルダー

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

        # android:text属性を取得
        text_raw = attrs.get("text", "")
        initial_text = resolver.resolve(text_raw) if resolver else text_raw
        initial_text = initial_text or ""

        input_type = (attrs.get("inputType") or "").lower()
        obscure = "textpassword" in input_type or "password" in hint.lower()
        is_multiline = "textmultiline" in input_type or "multiline" in input_type
        
        # inputTypeに基づいてキーボードタイプを設定
        keyboard_type = None  # デフォルト値（TextInputType.text）は不要なのでNone
        if "numberdecimal" in input_type:
            keyboard_type = "TextInputType.numberWithOptions(decimal: true)"
        elif "number" in input_type:
            keyboard_type = "TextInputType.number"
        elif "phone" in input_type:
            keyboard_type = "TextInputType.phone"
        elif "email" in input_type:
            keyboard_type = "TextInputType.emailAddress"
        elif is_multiline:
            keyboard_type = "TextInputType.multiline"

        # decorationを改善（borderを追加）
        if hint:
            dec = f'InputDecoration(hintText: "{escape_dart(hint)}", border: OutlineInputBorder())'
        else:
            dec = 'InputDecoration(border: OutlineInputBorder())'
        
        parts = [f"decoration: {dec}"]
        if keyboard_type:  # デフォルト値（TextInputType.text）以外の場合のみ追加
            parts.append(f"keyboardType: {keyboard_type}")
        if obscure:
            parts.append("obscureText: true")
        
        # 複数行対応
        if is_multiline:
            parts.append("maxLines: null")
        
        # コントローラーを設定（IDからコントローラー名を生成）
        raw_id = attrs.get("id")
        if raw_id:
            field_id = raw_id.split("/")[-1]
            # editTitle, editContent などのパターンを処理
            controller_base = field_id.replace("edit", "").replace("Edit", "")
            if controller_base:
                controller_name = f"_{controller_base[0].lower()}{controller_base[1:]}Controller"
                parts.append(f"controller: {controller_name}")
        elif initial_text:
            # IDがない場合は初期テキストからコントローラーを作成
            parts.append(f'controller: TextEditingController(text: "{escape_dart(initial_text)}")')

        body = f"TextField({', '.join(parts)})"
        return apply_layout_modifiers(body, attrs, resolver)

    
    
    # ================== AutoCompleteTextView ==================
    if t == "AutoCompleteTextView":
        # AutoCompleteTextViewはTextField + Autocompleteで表現
        hint_raw = attrs.get("hint", "")
        hint = resolver.resolve(hint_raw) if resolver else hint_raw
        hint = hint or ""
        
        text_raw = attrs.get("text", "")
        initial_text = resolver.resolve(text_raw) if resolver else text_raw
        initial_text = initial_text or ""
        
        completion_threshold = attrs.get("completionThreshold", "3")
        
        dec = f'InputDecoration(hintText: "{escape_dart(hint)}", border: OutlineInputBorder())' if hint else 'InputDecoration(border: OutlineInputBorder())'
        
        parts = [f"decoration: {dec}"]  # keyboardType: TextInputType.textはデフォルト値なので不要
        
        if initial_text:
            parts.append(f'controller: TextEditingController(text: "{escape_dart(initial_text)}")')
        
        # Autocomplete機能はTextField + Autocomplete widgetで実現
        # ただし、シンプルな実装としてTextFieldのみを生成（Autocompleteは後で手動で追加可能）
        body = f"TextField({', '.join(parts)})"
        return apply_layout_modifiers(body, attrs, resolver)

    # ================== Switch ==================
    if t == "Switch":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)
        
        # Switchのテキスト
        text_raw = attrs.get("text", "")
        text = resolver.resolve(text_raw) if resolver else text_raw
        text = text or ""
        
        # Switchのchecked状態（デフォルトはfalse）
        checked = (attrs.get("checked") or "").lower() == "true"
        
        # Switchウィジェットを生成
        # Dartでは => の後に {} は使えないので、通常の関数構文を使用
        if text:
            body = f'Switch(value: {str(checked).lower()}, onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); }}, title: Text("{escape_dart(text)}"))'
        else:
            body = f'Switch(value: {str(checked).lower()}, onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); }})'
        
        # ハンドラがある場合は追加
        if handler_name:
            # SwitchのonChangedにハンドラを追加
            body = body.replace('onChanged: (value) { setState(() { /* TODO: update state */ }); }', 
                              f'onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); {handler_name}(context); }}')
        
        return apply_layout_modifiers(body, attrs, resolver)

    
    
    # ================== Spinner ==================
    if t == "Spinner":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)
        
        # SpinnerはDropdownButtonFormFieldで表現
        # 初期値はnull（選択されていない状態）
        body = 'DropdownButtonFormField<String>(value: null, items: [DropdownMenuItem(value: "item1", child: Text("Item 1")), DropdownMenuItem(value: "item2", child: Text("Item 2"))], onChanged: (value) { /* TODO: update state */ })'
        
        if handler_name:
            body = body.replace('onChanged: (value) { /* TODO: update state */ }', 
                              f'onChanged: (value) {{ /* TODO: update state */ {handler_name}(context); }}')
        
        return apply_layout_modifiers(body, attrs, resolver)

    # ================== CheckBox ==================
    if t == "CheckBox":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)
        
        text_raw = attrs.get("text", "")
        text = resolver.resolve(text_raw) if resolver else text_raw
        text = text or ""
        
        checked = (attrs.get("checked") or "").lower() == "true"
        
        if text:
            body = f'CheckboxListTile(value: {str(checked).lower()}, onChanged: (value) {{ /* TODO: update state */ }}, title: Text("{escape_dart(text)}"))'
        else:
            body = f'Checkbox(value: {str(checked).lower()}, onChanged: (value) {{ /* TODO: update state */ }})'
        
        if handler_name:
            body = body.replace('onChanged: (value) { setState(() { /* TODO: update state */ }); }', 
                              f'onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); {handler_name}(context); }}')
        
        return apply_layout_modifiers(body, attrs, resolver)
    
    # ================== ToggleButton ==================
    

    if t == "ToggleButton":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)
        
        checked = (attrs.get("checked") or "").lower() == "true"
        
        body = f'Switch(value: {str(checked).lower()}, onChanged: (value) {{ /* TODO: update state */ }})'
        
        if handler_name:
            body = body.replace('onChanged: (value) { /* TODO: update state */ }', 
                              f'onChanged: (value) {{ /* TODO: update state */ {handler_name}(context); }}')
        
        return apply_layout_modifiers(body, attrs, resolver)
    
    # ================== View (区切り線など) ==================
    if t == "View":
        # Viewタグは通常、区切り線やスペーサーとして使用される
        bg_raw = attrs.get("background")
        height_raw = (attrs.get("layout_height") or attrs.get("height") or "1").lower()
        width_raw = (attrs.get("layout_width") or attrs.get("width") or "match_parent").lower()
        
        # 高さを解析（ViewタグはRow内に入ることが多いので、match_parentでも固定値を使用）
        height_val = "1"
        if height_raw not in ("match_parent", "fill_parent"):
            if "dp" in height_raw or "dip" in height_raw:
                try:
                    height_val = height_raw.replace("dp", "").replace("dip", "").strip()
                except:
                    pass
        # match_parentの場合は固定値1dpを使用（Row内でのオーバーフローを防ぐため）
        
        # 幅を解析（ViewタグはRow内に入ることが多いので、match_parentでも固定値を使用）
        width_val = "1"
        if width_raw not in ("match_parent", "fill_parent"):
            if "dp" in width_raw or "dip" in width_raw:
                try:
                    width_val = width_raw.replace("dp", "").replace("dip", "").strip()
                except:
                    pass
        # match_parentの場合は固定値1dpを使用（Row内でのオーバーフローを防ぐため）
        
        # 背景色を解析
        # Viewタグの背景色は既にContainerに設定されるので、apply_layout_modifiersに渡す前に背景色をattrsから削除
        attrs_copy = attrs.copy()
        if "background" in attrs_copy:
            del attrs_copy["background"]
        # layout_width/layout_heightも削除して、apply_layout_modifiersで処理されないようにする
        if "layout_width" in attrs_copy:
            del attrs_copy["layout_width"]
        if "layout_height" in attrs_copy:
            del attrs_copy["layout_height"]
        
        if bg_raw and resolver:
            resolved_bg = resolver.resolve(bg_raw) or bg_raw
            color_hex = ResourceResolver.android_color_to_flutter(resolved_bg)
            if color_hex:
                # ViewタグはRow内に入ることが多いので、固定値を使用（オーバーフローを防ぐため）
                body = f'Container(height: {height_val}, width: {width_val}, color: Color({color_hex}))'
            else:
                body = f'Container(height: {height_val}, width: {width_val}, color: Colors.grey)'
        else:
            body = f'Container(height: {height_val}, width: {width_val}, color: Colors.grey)'
        
        return apply_layout_modifiers(body, attrs_copy, resolver)

    # ================== ImageView ==================
    if t.endswith("ImageView") or t == "AppCompatImageView":
        # src属性から画像を取得（android:src または app:srcCompat）
        src_raw = attrs.get("srcCompat") or attrs.get("src") or attrs.get("android:src")
        if src_raw and resolver:
            # drawableリソースを解決
            drawable_path = resolver.resolve_drawable_path(src_raw)
            if drawable_path:
                # XML形式のdrawableリソースか画像ファイルかを判定
                if drawable_path.lower().endswith(".xml"):
                    # XML形式のdrawableリソース（shape drawableなど）の場合
                    # XMLを解析してBoxDecorationに変換を試みる
                    decoration_code = _parse_shape_drawable_to_boxdecoration(drawable_path, resolver)
                    if decoration_code:
                        # BoxDecorationに変換できた場合はContainerでラップ
                        body = f"Container(decoration: {decoration_code})"
                    else:
                        # 解析できない場合はTODOコメントとして残す
                        body = f"/* TODO: ImageView drawable XML {src_raw} - parse shape drawable to BoxDecoration */ Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey.shade600))"
                else:
                    # 画像ファイルの場合
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
                            error_builder = "errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey.shade300, child: Icon(Icons.image, size: 80, color: Colors.grey.shade600))"
                        else:
                            # 通常のImageViewの場合、固定サイズのerrorBuilderを使用
                            error_builder = "errorBuilder: (context, error, stackTrace) => Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey.shade600))"
                        
                        # アセット画像を使用（アセットが存在しない場合のエラーを避けるため、プレースホルダーも用意）
                        # 実際のアプリでは、アセットを追加するか、ネットワーク画像を使用する
                        body = f"Image.asset('{asset_path}', fit: {box_fit}, {error_builder})"
                    else:
                        # アセットパスが解決できない場合、プレースホルダーを表示
                        body = "Center(child: Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey.shade600)))"
            else:
                # drawableパスが解決できない場合、プレースホルダーを表示
                body = "Center(child: Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey.shade600)))"
        else:
            # src属性がない場合、プレースホルダーを表示
            body = "Center(child: Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey.shade600)))"
        return apply_layout_modifiers(body, attrs, resolver)

    # ================== fallback (カスタムView等) ==================
    # カスタムViewの場合は、タイプに応じて適切なFlutterウィジェットに変換
    display_name = t.split('.')[-1]  # パッケージ名を除いたクラス名
    full_class_name = t  # 完全修飾名（XMLから取得）
    
    # カスタムViewの情報を取得（java_rootが利用可能な場合）
    custom_view_info = None
    if hasattr(resolver, '_java_root') and resolver._java_root:
        try:
            from parser.custom_view_analyzer import get_custom_view_info, find_custom_views_in_project
            # まず完全修飾名で検索
            custom_view_info = get_custom_view_info(full_class_name, resolver._java_root)
            # 見つからない場合は、クラス名のみで検索
            if not custom_view_info:
                # プロジェクト内の全てのカスタムViewを検索
                all_views = find_custom_views_in_project(resolver._java_root)
                for view_full_name, view_info in all_views.items():
                    if view_info.class_name == display_name:
                        custom_view_info = view_info
                        break
        except Exception as e:
            # デバッグ用: エラーを無視して続行
            pass
    
    # サイズを取得
    width_attr = attrs.get("layout_width", "match_parent")
    height_attr = attrs.get("layout_height", "wrap_content")
    
    # サイズをFlutter形式に変換
    width_val = "double.infinity" if width_attr in ("match_parent", "fill_parent") else (width_attr.replace("dp", "") if "dp" in width_attr else "200")
    height_val = "double.infinity" if height_attr in ("match_parent", "fill_parent") else (height_attr.replace("dp", "") if "dp" in height_attr else "200")
    
    # カスタムViewのタイプに応じて変換
    if custom_view_info:
        view_type = custom_view_info.view_type
        
        if view_type == "TYPE_A":
            # TYPE_A: 標準UI拡張 → 親クラスの変換ロジックを使用
            parent_class = custom_view_info.parent_class.lower()
            # 親クラスに応じた変換を試みる
            if "textview" in parent_class or "text" in parent_class:
                # TextViewとして処理
                text_raw = attrs.get("text", "")
                text = resolver.resolve(text_raw) if resolver else text_raw
                text = text or display_name
                body = f'Text("{escape_dart(text)}"{_text_style(attrs, resolver)})'
            elif "imageview" in parent_class or "image" in parent_class:
                # ImageViewとして処理
                src_raw = attrs.get("srcCompat") or attrs.get("src") or attrs.get("android:src")
                if src_raw and resolver:
                    drawable_path = resolver.resolve_drawable_path(src_raw)
                    if drawable_path:
                        asset_path = get_asset_path_from_drawable(drawable_path)
                        if asset_path:
                            body = f"Image.asset('{asset_path}', fit: BoxFit.cover)"
                        else:
                            body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.grey.shade300))"
                    else:
                        body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.grey.shade300))"
                else:
                    body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.grey.shade300))"
            elif "button" in parent_class:
                # Buttonとして処理
                label = attrs.get("text", display_name)
                label = resolver.resolve(label) if resolver else label
                body = f'ElevatedButton(onPressed: () => _onUnknownPressed(context), child: Text("{escape_dart(label)}"))'
            else:
                # その他の標準View → 親クラス名を表示
                body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.blue.shade50, border: Border.all(color: Colors.blue.shade300)), child: Center(child: Text('{display_name}\\n(extends {custom_view_info.parent_class})', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.blue.shade700))))"
        
        elif view_type == "TYPE_B":
            # TYPE_B: 複合View → レイアウトファイルを再帰的に解析
            if custom_view_info.layout_file:
                # レイアウトファイルが特定できた場合、再帰的に変換を試みる
                # ただし、ここでは簡易的にプレースホルダーを表示
                body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.green.shade50, border: Border.all(color: Colors.green.shade300)), child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.view_compact, size: 32, color: Colors.green.shade700), SizedBox(height: 8), Text('{display_name}', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.green.shade700)), Text('(Composite View)', style: TextStyle(fontSize: 10, color: Colors.green.shade600))]))"
            else:
                # レイアウトファイルが特定できない場合
                body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.green.shade50, border: Border.all(color: Colors.green.shade300)), child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.view_compact, size: 32, color: Colors.green.shade700), SizedBox(height: 8), Text('{display_name}', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.green.shade700))]))"
        
        elif view_type == "TYPE_C":
            # TYPE_C: 純粋なカスタム描画 → 代替ウィジェットを提案
            # よく使われるカスタムViewの代替ウィジェットマッピング
            replacement_mapping = {
                "WheelLayout": "ListWheelScrollView",  # ホイールピッカー
                "WheelView": "ListWheelScrollView",
                "Picker": "CupertinoPicker",  # ピッカー
                "GradientView": "Container with LinearGradient",  # グラデーション
            }
            
            replacement = None
            for key, value in replacement_mapping.items():
                if key.lower() in display_name.lower():
                    replacement = value
                    break
            
            if replacement:
                if "ListWheelScrollView" in replacement:
                    body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.orange.shade50, border: Border.all(color: Colors.orange.shade300)), child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.view_carousel, size: 32, color: Colors.orange.shade700), SizedBox(height: 8), Text('{display_name}', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.orange.shade700)), Text('Use: {replacement}', style: TextStyle(fontSize: 10, color: Colors.orange.shade600))]))"
                elif "CupertinoPicker" in replacement:
                    body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.orange.shade50, border: Border.all(color: Colors.orange.shade300)), child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.date_range, size: 32, color: Colors.orange.shade700), SizedBox(height: 8), Text('{display_name}', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.orange.shade700)), Text('Use: {replacement}', style: TextStyle(fontSize: 10, color: Colors.orange.shade600))]))"
                else:
                    body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.orange.shade50, border: Border.all(color: Colors.orange.shade300)), child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.brush, size: 32, color: Colors.orange.shade700), SizedBox(height: 8), Text('{display_name}', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.orange.shade700)), Text('Use: {replacement}', style: TextStyle(fontSize: 10, color: Colors.orange.shade600))]))"
            else:
                # 代替ウィジェットが見つからない場合、CustomPainterのテンプレートを提案
                body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.red.shade50, border: Border.all(color: Colors.red.shade300)), child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.brush, size: 32, color: Colors.red.shade700), SizedBox(height: 8), Text('{display_name}', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.red.shade700)), Text('(Custom Drawing)', style: TextStyle(fontSize: 10, color: Colors.red.shade600)), Text('Use CustomPainter', style: TextStyle(fontSize: 9, color: Colors.red.shade500))]))"
        
        else:
            # UNKNOWN → 子要素を再帰的に変換してContainerでラップ
            from translator.layout_rules import translate_node
            dart_children = []
            if children:
                for ch in children:
                    child_code = translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                    dart_children.append(child_code)
            
            if len(dart_children) == 1:
                child_code = dart_children[0]
            elif len(dart_children) > 1:
                children_joined = ",\n".join(dart_children)
                child_code = f"Column(children: [\n{indent(children_joined)}\n])"
            else:
                child_code = "SizedBox.shrink()"
            
            body = f"Container(decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8), border: Border.all(color: Colors.grey.shade600)), child: {child_code})"
    else:
        # カスタムView情報が取得できない場合、従来のマッピングを使用
        custom_view_mapping = {
            "NestedScrollView": f"SingleChildScrollView(child: SizedBox.shrink())",  # インスタンス化が必要
            "BrightnessGradientView": f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(gradient: LinearGradient(colors: [Colors.white, Colors.black], begin: Alignment.topLeft, end: Alignment.bottomRight)))",
            "ColorGradientView": f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(gradient: LinearGradient(colors: [Colors.blue, Colors.purple], begin: Alignment.topLeft, end: Alignment.bottomRight)))",
        }
        
        if display_name in custom_view_mapping:
            body = custom_view_mapping[display_name]
        else:
            # 未知のビューでも子要素を再帰的に変換
            from translator.layout_rules import translate_node
            dart_children = []
            if children:
                for ch in children:
                    child_code = translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                    dart_children.append(child_code)
            
            if len(dart_children) == 1:
                child_code = dart_children[0]
            elif len(dart_children) > 1:
                children_joined = ",\n".join(dart_children)
                child_code = f"Column(children: [\n{indent(children_joined)}\n])"
            else:
                child_code = "SizedBox.shrink()"
            
            body = f"Container(decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8), border: Border.all(color: Colors.grey.shade600)), child: {child_code})"
    
    return apply_layout_modifiers(body, attrs, resolver)
