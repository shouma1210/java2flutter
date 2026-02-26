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

    parts = []

    size_raw = attrs.get("textSize")
    if size_raw:
        if resolver:
            resolved = resolver.resolve(size_raw) or size_raw
            try:
                size_px = resolver.parse_dimen_to_px(resolved)
            except Exception:
                size_px = None
        else:

            try:
                size_px = ResourceResolver.parse_dimen_to_px(size_raw)
            except Exception:
                size_px = None
            if size_px:
                parts.append(f"fontSize: {float(size_px):.1f}")

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

    if logic_map is None:
        logic_map = {}

    t = node.get("type") or ""
    attrs = node.get("attrs") or {}
    children = node.get("children") or []

    CARDVIEW_TYPES = {
        "androidx.cardview.widget.CardView",
        "android.support.v7.widget.CardView",
        "com.google.android.material.card.MaterialCardView",
        "CardView",
        "MaterialCardView",
    }
    
    if t in CARDVIEW_TYPES or t.endswith("CardView") or t.endswith("MaterialCardView"):

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

        bg_color_raw = attrs.get("cardBackgroundColor") or attrs.get("app:cardBackgroundColor") or attrs.get("cardBackgroundColor")
        radius_raw = attrs.get("cardCornerRadius") or attrs.get("app:cardCornerRadius") or attrs.get("cardCornerRadius", "0dp")
        stroke_color_raw = attrs.get("strokeColor") or attrs.get("app:strokeColor")
        stroke_width_raw = attrs.get("strokeWidth") or attrs.get("app:strokeWidth", "0dp")
        elevation_raw = attrs.get("cardElevation") or attrs.get("app:cardElevation", "0dp")

        bg_color = None
        if bg_color_raw and resolver:
            resolved_bg = resolver.resolve(bg_color_raw) or bg_color_raw
            bg_color = ResourceResolver.android_color_to_flutter(resolved_bg)

        radius_val = _parse_dimen(radius_raw, resolver) or 0.0

        stroke_color = None
        if stroke_color_raw and resolver:
            resolved_stroke = resolver.resolve(stroke_color_raw) or stroke_color_raw
            stroke_color = ResourceResolver.android_color_to_flutter(resolved_stroke)

        stroke_width_val = _parse_dimen(stroke_width_raw, resolver) or 0.0

        elevation_val = _parse_dimen(elevation_raw, resolver) or 0.0

        card_parts = []

        if bg_color:
            card_parts.append(f"color: Color({bg_color})")

        shape_parts = [f"borderRadius: BorderRadius.circular({radius_val})"]
        if stroke_color and stroke_width_val > 0:
            shape_parts.append(f"side: BorderSide(color: Color({stroke_color}), width: {stroke_width_val})")
        
        if shape_parts:
            card_parts.append(f"shape: RoundedRectangleBorder({', '.join(shape_parts)})")

        if elevation_val > 0:
            card_parts.append(f"elevation: {elevation_val}")
        else:
            card_parts.append("elevation: 0")

        card_parts.append(f"child: {child_code}")
        
        body = f"Card({', '.join(card_parts)})"

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

    if t == "RadioButton":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)
        
        text_raw = attrs.get("text", "")
        text = resolver.resolve(text_raw) if resolver else text_raw
        text = text or ""
        
        checked = (attrs.get("checked") or "").lower() == "true"

        button_attr = attrs.get("button") or attrs.get("android:button")
        is_button_null = button_attr == "@null" or button_attr == "null"
        
        if is_button_null:

            width_raw = attrs.get("layout_width") or attrs.get("width") or "wrap_content"
            height_raw = attrs.get("layout_height") or attrs.get("height") or "wrap_content"
            text_size_raw = attrs.get("textSize") or attrs.get("android:textSize") or "14sp"
            
            width_val = _parse_dimen(width_raw, resolver) or 60.0
            height_val = _parse_dimen(height_raw, resolver) or 60.0
            text_size_val = _parse_dimen(text_size_raw, resolver) or 24.0

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

            attrs_copy = attrs.copy()
            attrs_copy.pop("background", None)
            attrs_copy.pop("android:background", None)
            return apply_layout_modifiers(body, attrs_copy, resolver)
        else:

            if text:
                body = f'RadioListTile(value: "{xml_id}", groupValue: null, onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); }}, title: Text("{escape_dart(text)}"))'
            else:
                body = f'Radio(value: "{xml_id}", groupValue: null, onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); }})'
        
        if handler_name:
            body = body.replace('onChanged: (value) { setState(() { /* TODO: update state */ }); }', 
                              f'onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); {handler_name}(context); }}')
        
        return apply_layout_modifiers(body, attrs, resolver)

    if t.lower().endswith("button") or t == "Button":
        xml_id = _id_base(attrs.get("id", ""))

        label_raw = attrs.get("text", "")
        label = resolver.resolve(label_raw) if resolver else label_raw
        label = label or "Button"

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
                    f"backgroundColor: Color({bg_color_hex}), foregroundColor: Colors.black87)"
                )

        xml_onclick = attrs.get("onClick") or attrs.get("android:onClick")

        if xml_onclick:

            camel = xml_onclick
            if camel.startswith("on"):
                camel = camel[2:]
            camel = _to_camel(camel)
            handler_name = (
                f"_on{camel[:1].upper()}{camel[1:]}Pressed"
                if camel
                else "_onUnknownPressed"
            )
        else:

            handler_name = _find_handler(logic_map, xml_id)
            if not handler_name:

                camel = _to_camel(xml_id)
                handler_name = (
                    f"_on{camel[:1].upper()}{camel[1:]}Pressed"
                    if camel
                    else "_onUnknownPressed"
                )

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

    if t == "TextView":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)

        text_raw = attrs.get("text", "")
        text = resolver.resolve(text_raw) if resolver else text_raw
        text = text or ""

        if not text and xml_id:
            text = f"[{xml_id}]"

        body = f'Text("{escape_dart(text)}"{_text_style(attrs, resolver)})'

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

            body = f'TextButton(onPressed: null, child: {body})'
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "EditText" or t.endswith("EditText"):
        hint_raw = attrs.get("hint", "")
        hint = resolver.resolve(hint_raw) if resolver else hint_raw
        hint = hint or ""

        text_raw = attrs.get("text", "")
        initial_text = resolver.resolve(text_raw) if resolver else text_raw
        initial_text = initial_text or ""

        input_type = (attrs.get("inputType") or "").lower()
        obscure = "textpassword" in input_type or "password" in hint.lower()
        is_multiline = "textmultiline" in input_type or "multiline" in input_type

        keyboard_type = None
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

        if hint:
            dec = f'InputDecoration(hintText: "{escape_dart(hint)}", border: OutlineInputBorder())'
        else:
            dec = 'InputDecoration(border: OutlineInputBorder())'
        
        parts = [f"decoration: {dec}"]
        if keyboard_type:
            parts.append(f"keyboardType: {keyboard_type}")
        if obscure:
            parts.append("obscureText: true")

        if is_multiline:
            parts.append("maxLines: null")

        raw_id = attrs.get("id")
        if raw_id:
            field_id = raw_id.split("/")[-1]

            controller_base = field_id.replace("edit", "").replace("Edit", "")
            if controller_base:
                controller_name = f"_{controller_base[0].lower()}{controller_base[1:]}Controller"
                parts.append(f"controller: {controller_name}")
        elif initial_text:

            parts.append(f'controller: TextEditingController(text: "{escape_dart(initial_text)}")')

        body = f"TextField({', '.join(parts)})"
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "AutoCompleteTextView":

        hint_raw = attrs.get("hint", "")
        hint = resolver.resolve(hint_raw) if resolver else hint_raw
        hint = hint or ""
        
        text_raw = attrs.get("text", "")
        initial_text = resolver.resolve(text_raw) if resolver else text_raw
        initial_text = initial_text or ""
        
        completion_threshold = attrs.get("completionThreshold", "3")
        
        dec = f'InputDecoration(hintText: "{escape_dart(hint)}", border: OutlineInputBorder())' if hint else 'InputDecoration(border: OutlineInputBorder())'
        
        parts = [f"decoration: {dec}"]
        
        if initial_text:
            parts.append(f'controller: TextEditingController(text: "{escape_dart(initial_text)}")')

        body = f"TextField({', '.join(parts)})"
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "Switch":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)

        text_raw = attrs.get("text", "")
        text = resolver.resolve(text_raw) if resolver else text_raw
        text = text or ""

        checked = (attrs.get("checked") or "").lower() == "true"

        if text:
            body = f'Switch(value: {str(checked).lower()}, onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); }}, title: Text("{escape_dart(text)}"))'
        else:
            body = f'Switch(value: {str(checked).lower()}, onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); }})'

        if handler_name:

            body = body.replace('onChanged: (value) { setState(() { /* TODO: update state */ }); }', 
                              f'onChanged: (value) {{ setState(() {{ /* TODO: update state */ }}); {handler_name}(context); }}')
        
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "Spinner":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)

        body = 'DropdownButtonFormField<String>(value: null, items: [DropdownMenuItem(value: "item1", child: Text("Item 1")), DropdownMenuItem(value: "item2", child: Text("Item 2"))], onChanged: (value) { /* TODO: update state */ })'
        
        if handler_name:
            body = body.replace('onChanged: (value) { /* TODO: update state */ }', 
                              f'onChanged: (value) {{ /* TODO: update state */ {handler_name}(context); }}')
        
        return apply_layout_modifiers(body, attrs, resolver)

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

    if t == "ToggleButton":
        xml_id = _id_base(attrs.get("id", ""))
        handler_name = _find_handler(logic_map, xml_id)
        
        checked = (attrs.get("checked") or "").lower() == "true"
        
        body = f'Switch(value: {str(checked).lower()}, onChanged: (value) {{ /* TODO: update state */ }})'
        
        if handler_name:
            body = body.replace('onChanged: (value) { /* TODO: update state */ }', 
                              f'onChanged: (value) {{ /* TODO: update state */ {handler_name}(context); }}')
        
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "View":

        bg_raw = attrs.get("background")
        height_raw = (attrs.get("layout_height") or attrs.get("height") or "1").lower()
        width_raw = (attrs.get("layout_width") or attrs.get("width") or "match_parent").lower()

        height_val = "1"
        if height_raw not in ("match_parent", "fill_parent"):
            if "dp" in height_raw or "dip" in height_raw:
                try:
                    height_val = height_raw.replace("dp", "").replace("dip", "").strip()
                except:
                    pass

        width_val = "1"
        if width_raw not in ("match_parent", "fill_parent"):
            if "dp" in width_raw or "dip" in width_raw:
                try:
                    width_val = width_raw.replace("dp", "").replace("dip", "").strip()
                except:
                    pass

        attrs_copy = attrs.copy()
        if "background" in attrs_copy:
            del attrs_copy["background"]

        if "layout_width" in attrs_copy:
            del attrs_copy["layout_width"]
        if "layout_height" in attrs_copy:
            del attrs_copy["layout_height"]
        
        if bg_raw and resolver:
            resolved_bg = resolver.resolve(bg_raw) or bg_raw
            color_hex = ResourceResolver.android_color_to_flutter(resolved_bg)
            if color_hex:

                body = f'Container(height: {height_val}, width: {width_val}, color: Color({color_hex}))'
            else:
                body = f'Container(height: {height_val}, width: {width_val}, color: Colors.grey)'
        else:
            body = f'Container(height: {height_val}, width: {width_val}, color: Colors.grey)'
        
        return apply_layout_modifiers(body, attrs_copy, resolver)

    if t.endswith("ImageView") or t == "AppCompatImageView":

        src_raw = attrs.get("srcCompat") or attrs.get("src") or attrs.get("android:src")
        if src_raw and resolver:

            drawable_path = resolver.resolve_drawable_path(src_raw)
            if drawable_path:

                if drawable_path.lower().endswith(".xml"):

                    decoration_code = _parse_shape_drawable_to_boxdecoration(drawable_path, resolver)
                    if decoration_code:

                        body = f"Container(decoration: {decoration_code})"
                    else:

                        body = f"/* TODO: ImageView drawable XML {src_raw} - parse shape drawable to BoxDecoration */ Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey.shade600))"
                else:

                    asset_path = get_asset_path_from_drawable(drawable_path)
                    if asset_path:

                        scale_type = (attrs.get("scaleType") or attrs.get("android:scaleType") or "fitCenter").lower()
                        box_fit = "BoxFit.cover"
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

                        width = (attrs.get("layout_width") or "").lower()
                        height = (attrs.get("layout_height") or "").lower()
                        is_background = width in ("match_parent", "fill_parent") and height in ("match_parent", "fill_parent")

                        if is_background:

                            error_builder = "errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey.shade300, child: Icon(Icons.image, size: 80, color: Colors.grey.shade600))"
                        else:

                            error_builder = "errorBuilder: (context, error, stackTrace) => Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey.shade600))"

                        body = f"Image.asset('{asset_path}', fit: {box_fit}, {error_builder})"
                    else:

                        body = "Center(child: Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey.shade600)))"
            else:

                body = "Center(child: Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey.shade600)))"
        else:

            body = "Center(child: Container(width: 180, height: 180, decoration: BoxDecoration(color: Colors.grey.shade300, borderRadius: BorderRadius.circular(8)), child: Icon(Icons.image, size: 80, color: Colors.grey.shade600)))"
        return apply_layout_modifiers(body, attrs, resolver)

    display_name = t.split('.')[-1]
    full_class_name = t

    custom_view_info = None
    if hasattr(resolver, '_java_root') and resolver._java_root:
        try:
            from parser.custom_view_analyzer import get_custom_view_info, find_custom_views_in_project

            custom_view_info = get_custom_view_info(full_class_name, resolver._java_root)

            if not custom_view_info:

                all_views = find_custom_views_in_project(resolver._java_root)
                for view_full_name, view_info in all_views.items():
                    if view_info.class_name == display_name:
                        custom_view_info = view_info
                        break
        except Exception as e:

            pass

    width_attr = attrs.get("layout_width", "match_parent")
    height_attr = attrs.get("layout_height", "wrap_content")

    width_val = "double.infinity" if width_attr in ("match_parent", "fill_parent") else (width_attr.replace("dp", "") if "dp" in width_attr else "200")
    height_val = "double.infinity" if height_attr in ("match_parent", "fill_parent") else (height_attr.replace("dp", "") if "dp" in height_attr else "200")

    if custom_view_info:
        view_type = custom_view_info.view_type
        
        if view_type == "TYPE_A":

            parent_class = custom_view_info.parent_class.lower()

            if "textview" in parent_class or "text" in parent_class:

                text_raw = attrs.get("text", "")
                text = resolver.resolve(text_raw) if resolver else text_raw
                text = text or display_name
                body = f'Text("{escape_dart(text)}"{_text_style(attrs, resolver)})'
            elif "imageview" in parent_class or "image" in parent_class:

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

                label = attrs.get("text", display_name)
                label = resolver.resolve(label) if resolver else label
                body = f'ElevatedButton(onPressed: () => _onUnknownPressed(context), child: Text("{escape_dart(label)}"))'
            else:

                body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.blue.shade50, border: Border.all(color: Colors.blue.shade300)), child: Center(child: Text('{display_name}\\n(extends {custom_view_info.parent_class})', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.blue.shade700))))"
        
        elif view_type == "TYPE_B":

            if custom_view_info.layout_file:

                body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.green.shade50, border: Border.all(color: Colors.green.shade300)), child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.view_compact, size: 32, color: Colors.green.shade700), SizedBox(height: 8), Text('{display_name}', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.green.shade700)), Text('(Composite View)', style: TextStyle(fontSize: 10, color: Colors.green.shade600))]))"
            else:

                body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.green.shade50, border: Border.all(color: Colors.green.shade300)), child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.view_compact, size: 32, color: Colors.green.shade700), SizedBox(height: 8), Text('{display_name}', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.green.shade700))]))"
        
        elif view_type == "TYPE_C":

            replacement_mapping = {
                "WheelLayout": "ListWheelScrollView",
                "WheelView": "ListWheelScrollView",
                "Picker": "CupertinoPicker",
                "GradientView": "Container with LinearGradient",
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

                body = f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(color: Colors.red.shade50, border: Border.all(color: Colors.red.shade300)), child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [Icon(Icons.brush, size: 32, color: Colors.red.shade700), SizedBox(height: 8), Text('{display_name}', textAlign: TextAlign.center, style: TextStyle(fontSize: 12, color: Colors.red.shade700)), Text('(Custom Drawing)', style: TextStyle(fontSize: 10, color: Colors.red.shade600)), Text('Use CustomPainter', style: TextStyle(fontSize: 9, color: Colors.red.shade500))]))"
        
        else:

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

        custom_view_mapping = {
            "NestedScrollView": f"SingleChildScrollView(child: SizedBox.shrink())",
            "BrightnessGradientView": f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(gradient: LinearGradient(colors: [Colors.white, Colors.black], begin: Alignment.topLeft, end: Alignment.bottomRight)))",
            "ColorGradientView": f"Container(width: {width_val}, height: {height_val}, decoration: BoxDecoration(gradient: LinearGradient(colors: [Colors.blue, Colors.purple], begin: Alignment.topLeft, end: Alignment.bottomRight)))",
        }
        
        if display_name in custom_view_mapping:
            body = custom_view_mapping[display_name]
        else:

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
