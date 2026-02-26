
from parser.resource_resolver import ResourceResolver
import os
from utils import indent, apply_layout_modifiers

def _wrap_match_parent_for_linear(child_code: str, child_attrs: dict, parent_orientation: str) -> str:

    w = (child_attrs.get("layout_width") or "").lower()
    h = (child_attrs.get("layout_height") or "").lower()
    code = child_code

    if parent_orientation == "vertical":
        if h == "match_parent":
            code = f"Expanded(child: {code})"

        if w == "match_parent" and "Expanded" not in code:

            code = f"SizedBox(width: double.infinity, child: {code})"
    else:
        if w == "match_parent":
            code = f"Expanded(child: {code})"

        if h == "match_parent" and "Expanded" not in code:

            pass
    return code

def _convert_relative_layout_to_column(children: list, resolver, logic_map=None, fragments_by_id=None, layout_dir=None, values_dir=None) -> str:

    below_map = {}
    child_map = {}

    has_center_horizontal = False
    for ch in children:
        child_attrs = ch.get("attrs", {}) or {}
        if child_attrs.get("layout_centerHorizontal", "").lower() == "true":
            has_center_horizontal = True
            break

    for ch in children:
        child_attrs = ch.get("attrs", {}) or {}
        raw_id = child_attrs.get("id", "")
        child_id = raw_id.split("/")[-1] if raw_id else None
        child_code = translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
        
        layout_below = child_attrs.get("layout_below")
        if layout_below and child_id:
            below_id = layout_below.split("/")[-1] if "/" in layout_below else layout_below
            below_map[child_id] = below_id
        
        if child_id:
            child_map[child_id] = (ch, child_code)
        else:

            child_map[f"_no_id_{len(child_map)}"] = (ch, child_code)

    root_ids = set(child_map.keys()) - set(below_map.values())

    column_children = []
    processed_ids = set()

    for ch in children:
        child_attrs = ch.get("attrs", {}) or {}
        raw_id = child_attrs.get("id", "")
        child_id = raw_id.split("/")[-1] if raw_id else None
        if not child_id:
            child_id = f"_no_id_{children.index(ch)}"

        if child_id in root_ids and child_id not in processed_ids:
            ch_node, root_code = child_map[child_id]

            cross_axis_str = "center" if has_center_horizontal else "stretch"
            root_code = _wrap_relative_layout_child(root_code, ch_node.get("attrs", {}), column_cross_axis=cross_axis_str)
            column_children.append(root_code)
            processed_ids.add(child_id)

            same_below_ids = []
            for ch2 in children:
                child_attrs2 = ch2.get("attrs", {}) or {}
                raw_id2 = child_attrs2.get("id", "")
                child_id2 = raw_id2.split("/")[-1] if raw_id2 else None
                if not child_id2:
                    child_id2 = f"_no_id_{children.index(ch2)}"
                if child_id2 in below_map and below_map[child_id2] == child_id:
                    same_below_ids.append((children.index(ch2), child_id2))

            same_below_ids.sort(key=lambda x: x[0])
            
            if same_below_ids:
                row_children = []
                for _, below_id in same_below_ids:
                    if below_id not in processed_ids and below_id in child_map:
                        ch_below, below_code = child_map[below_id]

                        below_code = _wrap_relative_layout_child(below_code, ch_below.get("attrs", {}), column_cross_axis=cross_axis_str)
                        row_children.append(below_code)
                        processed_ids.add(below_id)
                
                if row_children:

                    row_children_joined = ",\n".join(row_children)
                    column_children.append(f"Row(children: [\n{indent(row_children_joined)}\n])")

    for child_id, (ch, child_code) in child_map.items():
        if child_id not in processed_ids:

            cross_axis_str = "center" if has_center_horizontal else "stretch"
            child_code = _wrap_relative_layout_child(child_code, ch.get("attrs", {}), column_cross_axis=cross_axis_str)
            column_children.append(child_code)
    
    if column_children:
        children_joined = ",\n".join(column_children)

        cross_axis_str = "center" if has_center_horizontal else "stretch"
        cross_axis = "CrossAxisAlignment.center" if has_center_horizontal else "CrossAxisAlignment.stretch"
        return f"Column(crossAxisAlignment: {cross_axis}, children: [\n{indent(children_joined)}\n])"
    else:
        return "SizedBox.shrink()"

def _wrap_relative_layout_child(child_code: str, child_attrs: dict, column_cross_axis: str = "stretch") -> str:

    layout_align_parent_left = child_attrs.get("layout_alignParentLeft", "").lower() == "true"
    layout_align_parent_right = child_attrs.get("layout_alignParentRight", "").lower() == "true"
    layout_center_horizontal = child_attrs.get("layout_centerHorizontal", "").lower() == "true"
    layout_to_left_of = child_attrs.get("layout_toLeftOf")
    layout_align_right = child_attrs.get("layout_alignRight")

    if column_cross_axis == "center" and layout_center_horizontal:

        pass
    elif layout_center_horizontal:

        if column_cross_axis == "stretch":

            pass
        else:

            child_code = f"Align(alignment: Alignment.center, child: {child_code})"
    elif layout_align_parent_left:

        child_code = f"Align(alignment: Alignment.centerLeft, child: {child_code})"
    elif layout_align_parent_right:

        child_code = f"Align(alignment: Alignment.centerRight, child: {child_code})"
    elif layout_to_left_of or layout_align_right:

        child_code = f"Align(alignment: Alignment.centerRight, child: {child_code})"
    
    return child_code

def _axes_from_gravity_for_linear(gravity: str, orientation: str, allow_center: bool = False):

    g = (gravity or "").lower()
    main = "MainAxisAlignment.start"

    cross = "CrossAxisAlignment.stretch"

    if "center" in g:
        if orientation == "vertical":
            if allow_center:
                main = "MainAxisAlignment.center"

        else:

            if "center_horizontal" in g or "center" in g:
                if allow_center:
                    main = "MainAxisAlignment.center"
            if "center_vertical" in g or "center" in g:
                cross = "CrossAxisAlignment.center"

    if "end" in g or "right" in g:
        if orientation == "vertical":
            cross = "CrossAxisAlignment.end"
        else:
            main = "MainAxisAlignment.end"

    if "start" in g or "left" in g:
        if orientation == "vertical":
            cross = "CrossAxisAlignment.start"
        else:
            main = "MainAxisAlignment.start"

    return main, cross

def _is_background_image_view(child_node: dict) -> bool:

    if not child_node:
        return False
    t = (child_node.get("type") or "").lower()
    if not (t.endswith("imageview") or t == "appcompatimageview"):
        return False
    child_attrs = child_node.get("attrs", {}) or {}
    width = (child_attrs.get("layout_width") or "").lower()
    height = (child_attrs.get("layout_height") or "").lower()

    return width in ("match_parent", "fill_parent") and height in ("match_parent", "fill_parent")

def _is_centered_in_constraint(child_attrs: dict) -> bool:

    top = child_attrs.get("layout_constraintTop_toTopOf", "")
    bottom = child_attrs.get("layout_constraintBottom_toBottomOf", "")
    bottom_to_top = child_attrs.get("layout_constraintBottom_toTopOf", "")
    top_to_bottom = child_attrs.get("layout_constraintTop_toBottomOf", "")
    start = child_attrs.get("layout_constraintStart_toStartOf", "")
    end = child_attrs.get("layout_constraintEnd_toEndOf", "")

    vertical_bias = child_attrs.get("layout_constraintVertical_bias")
    horizontal_bias = child_attrs.get("layout_constraintHorizontal_bias")
    
    is_vertically_centered = (
        (top == "parent" or top == "@id/parent") and 
        (bottom == "parent" or bottom == "@id/parent")
    ) or (
        (top == "parent" or top == "@id/parent") and 
        vertical_bias is not None
    )
    
    is_horizontally_centered = (
        (start == "parent" or start == "@id/parent") and 
        (end == "parent" or end == "@id/parent")
    ) or (
        (start == "parent" or start == "@id/parent") and 
        horizontal_bias is not None
    )
    
    return is_vertically_centered and is_horizontally_centered

def _get_constraint_bias(child_attrs: dict) -> tuple:

    vertical_bias = child_attrs.get("layout_constraintVertical_bias")
    horizontal_bias = child_attrs.get("layout_constraintHorizontal_bias")
    
    v_bias = float(vertical_bias) if vertical_bias else None
    h_bias = float(horizontal_bias) if horizontal_bias else None
    
    return v_bias, h_bias

def _get_background_images(children: list) -> tuple:

    bg_full = []
    bg_top = []
    bg_bottom = []
    foreground = []

    bg_images = [ch for ch in children if _is_background_image_view(ch)]
    
    if len(bg_images) == 1:

        bg_full.append(bg_images[0])
        foreground = [ch for ch in children if not _is_background_image_view(ch)]
    elif len(bg_images) > 1:

        bg_full.append(bg_images[0])

        foreground = [ch for ch in children if ch != bg_images[0]]
    else:

        foreground = children
    
    return bg_full, bg_top, bg_bottom, foreground

def _get_background_image_with_cover(bg_image_code: str, attrs: dict) -> str:

    scale_type = (attrs.get("scaleType") or attrs.get("android:scaleType") or "centerCrop").lower()
    box_fit = "BoxFit.cover"
    if "centerCrop" in scale_type:
        box_fit = "BoxFit.cover"
    elif "fitCenter" in scale_type or "centerInside" in scale_type:
        box_fit = "BoxFit.contain"
    elif "fitXY" in scale_type:
        box_fit = "BoxFit.fill"

    import re

    if re.search(r'fit:\s*BoxFit\.\w+', bg_image_code):
        bg_image_code = re.sub(r'fit:\s*BoxFit\.\w+', f'fit: {box_fit}', bg_image_code)
    else:

        bg_image_code = re.sub(
            r"Image\.asset\('([^']+)'",
            f"Image.asset('\\1', fit: {box_fit}",
            bg_image_code
        )

    if 'errorBuilder:' in bg_image_code:

        if re.search(r'width:\s*\d+.*height:\s*\d+', bg_image_code):

            error_builder_start = bg_image_code.find('errorBuilder:')
            if error_builder_start != -1:

                after_error_builder = bg_image_code[error_builder_start + len('errorBuilder:'):]

                paren_count = 0
                pos = 0
                found_start = False
                while pos < len(after_error_builder):
                    char = after_error_builder[pos]
                    if char == '(':
                        paren_count += 1
                        found_start = True
                    elif char == ')':
                        paren_count -= 1
                        if found_start and paren_count == 0:

                            error_builder_end = error_builder_start + len('errorBuilder:') + pos + 1

                            replacement = 'errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey[300], child: Icon(Icons.image, size: 80, color: Colors.grey[600]))'
                            bg_image_code = bg_image_code[:error_builder_start] + replacement + bg_image_code[error_builder_end:]
                            break
                    pos += 1
    else:

        last_paren = bg_image_code.rfind(')')
        if last_paren != -1:
            bg_image_code = bg_image_code[:last_paren] + f", errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey[300], child: Icon(Icons.image, size: 80, color: Colors.grey[600]))" + bg_image_code[last_paren:]
        else:
            bg_image_code += f", errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey[300], child: Icon(Icons.image, size: 80, color: Colors.grey[600]))"
    
    return bg_image_code

def translate_layout(node, resolver, logic_map=None, fragments_by_id=None, layout_dir=None, values_dir=None):

    t = node["type"]
    attrs = node.get("attrs", {}) or {}
    children = node.get("children", []) or []

    if t == "ListView":

        if children:
            dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
            children_joined = ",\n".join(dart_children)

            body = f"ListView.builder(itemCount: {len(children)}, itemBuilder: (context, index) {{ return {dart_children[0] if dart_children else 'SizedBox.shrink()'}; }})"
        else:

            body = "ListView.builder(itemCount: 0, itemBuilder: (context, index) => SizedBox.shrink())"
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "HorizontalScrollView" or t.endswith("HorizontalScrollView"):
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
        if len(dart_children) == 1:

            body = f"SingleChildScrollView(scrollDirection: Axis.horizontal, child: {dart_children[0]})"
        elif len(dart_children) > 1:

            children_joined = ",\n".join(dart_children)
            body = f"SingleChildScrollView(scrollDirection: Axis.horizontal, child: Row(children: [\n{indent(children_joined)}\n]))"
        else:

            body = "SingleChildScrollView(scrollDirection: Axis.horizontal, child: SizedBox.shrink())"
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "ScrollView" or t == "NestedScrollView" or t.endswith("NestedScrollView"):
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
        if len(dart_children) == 1:

            body = f"SingleChildScrollView(child: {dart_children[0]})"
        elif len(dart_children) > 1:

            children_joined = ",\n".join(dart_children)
            body = f"SingleChildScrollView(child: Column(children: [\n{indent(children_joined)}\n]))"
        else:

            body = "SingleChildScrollView(child: SizedBox.shrink())"
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "RadioGroup":
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]

        orientation = attrs.get("orientation", "vertical").lower()
        
        if orientation == "horizontal":
            children_joined = ",\n".join(dart_children)
            body = f"Row(children: [\n{indent(children_joined)}\n])"
        else:
            children_joined = ",\n".join(dart_children)
            body = f"Column(children: [\n{indent(children_joined)}\n])"
        
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "TableLayout":
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]

        spaced_children = []
        for i, child in enumerate(dart_children):
            spaced_children.append(child)
            if i < len(dart_children) - 1:
                spaced_children.append("SizedBox(height: 16)")
        children_joined = ",\n".join(spaced_children)

        body = f"Padding(padding: EdgeInsets.all(16.0), child: Column(mainAxisAlignment: MainAxisAlignment.start, crossAxisAlignment: CrossAxisAlignment.stretch, children: [\n{indent(children_joined)}\n]))"
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "TableRow":
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]

        if len(dart_children) == 1:

            body = f"Row(mainAxisAlignment: MainAxisAlignment.center, children: [\n{indent(dart_children[0])}\n])"
        else:

            improved_children = []
            for i, child_code in enumerate(dart_children):

                if i == 0:

                    improved_children.append(f"SizedBox(width: 120, child: {child_code})")
                else:

                    improved_children.append(f"Expanded(child: {child_code})")
            
            if not improved_children:
                improved_children = dart_children
            
            children_joined = ",\n".join(improved_children)
            body = f"Row(crossAxisAlignment: CrossAxisAlignment.center, children: [\n{indent(children_joined)}\n])"
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "LinearLayout":
        orientation = attrs.get("orientation", "vertical").lower()
        gravity = attrs.get("gravity", "")

        dart_children_list = []
        for ch in children:
            child_code = translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
            child_attrs = ch.get("attrs", {}) or {}

            child_type = (ch.get("type") or "").lower()
            if child_type != "view":
                child_code = _wrap_match_parent_for_linear(child_code, child_attrs, orientation)
            dart_children_list.append(child_code)

        children_joined = ",\n".join(dart_children_list)

        main, cross = _axes_from_gravity_for_linear(gravity, orientation, allow_center=False)

        needs_center_wrap = "center" in gravity.lower() and orientation == "vertical"

        if orientation == "horizontal":

            if cross == "CrossAxisAlignment.stretch":
                cross = "CrossAxisAlignment.center"
            body = (
                f"Row(mainAxisAlignment: {main}, crossAxisAlignment: {cross}, children: [\n"
                f"{indent(children_joined)}\n])"
            )
        else:

            if needs_center_wrap:
                body = (
                    f"Center(child: Column(mainAxisSize: MainAxisSize.min, mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: {cross}, children: [\n"
                    f"{indent(children_joined)}\n]))"
                )
            else:
                body = (
                    f"Column(mainAxisAlignment: {main}, crossAxisAlignment: {cross}, children: [\n"
                    f"{indent(children_joined)}\n])"
                )
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "FrameLayout":
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]

        if not dart_children and fragments_by_id:
            raw_id = attrs.get("id")
            if raw_id:
                container_id = raw_id.split("/")[-1]
                if container_id in fragments_by_id:
                    fragment_ir = fragments_by_id[container_id]
                    if fragment_ir.layout_file and layout_dir:
                        fragment_layout_path = os.path.join(layout_dir, fragment_ir.layout_file)
                        if os.path.exists(fragment_layout_path):

                            from parser.xml_parser import parse_layout_xml
                            try:
                                fragment_ir_tree, fragment_resolver = parse_layout_xml(fragment_layout_path, values_dir)

                                fragment_widget = translate_node(fragment_ir_tree, fragment_resolver or resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                                body = fragment_widget
                            except Exception as e:

                                body = f"Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                                       f"  Icon(Icons.error_outline, size: 48, color: Colors.red),\n" \
                                       f"  SizedBox(height: 16),\n" \
                                       f"  Text('Failed to load fragment: {fragment_ir.layout_file}',\n" \
                                       f"    textAlign: TextAlign.center,\n" \
                                       f"    style: TextStyle(color: Colors.red[600])),\n" \
                                       f"]))"
                        else:

                            body = f"Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                                   f"  Icon(Icons.info_outline, size: 48, color: Colors.grey),\n" \
                                   f"  SizedBox(height: 16),\n" \
                                   f"  Text('Fragment detected: {fragment_ir.fragment_class}\\nLayout file not found: {fragment_ir.layout_file}',\n" \
                                   f"    textAlign: TextAlign.center,\n" \
                                   f"    style: TextStyle(color: Colors.grey[600])),\n" \
                                   f"]))"
                    else:

                        body = f"Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                               f"  Icon(Icons.info_outline, size: 48, color: Colors.grey),\n" \
                               f"  SizedBox(height: 16),\n" \
                               f"  Text('Fragment detected: {fragment_ir.fragment_class}\\nCould not guess layout file name.',\n" \
                               f"    textAlign: TextAlign.center,\n" \
                               f"    style: TextStyle(color: Colors.grey[600])),\n" \
                               f"]))"
                else:

                    body = "Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                           "  Icon(Icons.info_outline, size: 48, color: Colors.grey),\n" \
                           "  SizedBox(height: 16),\n" \
                           "  Text('Empty container detected.\\nThis may be a Fragment container.',\n" \
                           "    textAlign: TextAlign.center,\n" \
                           "    style: TextStyle(color: Colors.grey[600])),\n" \
                           "]))"
            else:

                body = "Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                       "  Icon(Icons.info_outline, size: 48, color: Colors.grey),\n" \
                       "  SizedBox(height: 16),\n" \
                       "  Text('Empty container detected.\\nThis may be a Fragment container.',\n" \
                       "    textAlign: TextAlign.center,\n" \
                       "    style: TextStyle(color: Colors.grey[600])),\n" \
                       "]))"
        elif not dart_children:

            body = "Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                   "  Icon(Icons.info_outline, size: 48, color: Colors.grey),\n" \
                   "  SizedBox(height: 16),\n" \
                   "  Text('Empty container detected.\\nThis may be a Fragment container.',\n" \
                   "    textAlign: TextAlign.center,\n" \
                   "    style: TextStyle(color: Colors.grey[600])),\n" \
                   "]))"
        else:
            body = f"Stack(children: [\n{indent(',\n'.join(dart_children))}\n])"

        return apply_layout_modifiers(body, attrs, resolver)
    if t == "RelativeLayout":

        has_layout_below = any(ch.get("attrs", {}).get("layout_below") for ch in children)
        
        if has_layout_below:

            body = _convert_relative_layout_to_column(children, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
        else:

            dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
            if dart_children:
                body = f"Stack(children: [\n{indent(',\n'.join(dart_children))}\n])"
            else:
                body = "SizedBox.shrink()"
        return apply_layout_modifiers(body, attrs, resolver)

    if t == "ConstraintLayout":

        background_attr = attrs.get("background")
        has_background_attr = background_attr and not background_attr.startswith("#")

        bg_full, bg_top, bg_bottom, foreground = _get_background_images(children)

        if has_background_attr and not bg_full:

            bg_full = [{"type": "ImageView", "attrs": {"src": background_attr, "layout_width": "match_parent", "layout_height": "match_parent"}}]
            foreground = children
        
        if bg_full or bg_top or bg_bottom:

            stack_children = []

            if bg_full:
                if isinstance(bg_full[0], dict) and bg_full[0].get("type") == "ImageView":

                    bg_attrs = bg_full[0].get("attrs", {})
                    src = bg_attrs.get("src", "")
                    if src.startswith("@drawable/") or src.startswith("@mipmap/"):

                        resource_name = src.split("/")[-1]
                        bg_image_code = f"Image.asset('assets/images/{resource_name}.png', fit: BoxFit.cover, errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey[300], child: Icon(Icons.image, size: 80, color: Colors.grey[600])))"
                    else:
                        bg_image_code = f"Image.asset('assets/images/{src}.png', fit: BoxFit.cover, errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey[300], child: Icon(Icons.image, size: 80, color: Colors.grey[600])))"
                    stack_children.append(f"Positioned.fill(child: {bg_image_code})")
                else:
                    bg_image = translate_node(bg_full[0], resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                    bg_attrs = bg_full[0].get("attrs", {}) or {}
                    bg_image = _get_background_image_with_cover(bg_image, bg_attrs)
                    stack_children.append(f"Positioned.fill(child: {bg_image})")

            for bg_img_node in bg_top:
                bg_image = translate_node(bg_img_node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                bg_attrs = bg_img_node.get("attrs", {}) or {}
                bg_image = _get_background_image_with_cover(bg_image, bg_attrs)
                stack_children.append(f"Positioned(top: 0, left: 0, right: 0, child: {bg_image})")

            for bg_img_node in bg_bottom:
                bg_image = translate_node(bg_img_node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                bg_attrs = bg_img_node.get("attrs", {}) or {}
                bg_image = _get_background_image_with_cover(bg_image, bg_attrs)
                stack_children.append(f"Positioned(bottom: 0, left: 0, right: 0, child: {bg_image})")

            foreground_widgets = []
            for ch in foreground:
                child_code = translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                child_attrs = ch.get("attrs", {}) or {}

                if _is_centered_in_constraint(child_attrs):
                    v_bias, h_bias = _get_constraint_bias(child_attrs)
                    
                    if v_bias is not None or h_bias is not None:

                        alignment_parts = []
                        if v_bias is not None:

                            y = (v_bias - 0.5) * 2.0
                            alignment_parts.append(f"y: {y:.2f}")
                        if h_bias is not None:

                            x = (h_bias - 0.5) * 2.0
                            alignment_parts.append(f"x: {x:.2f}")
                        
                        if alignment_parts:
                            alignment_str = ", ".join(alignment_parts)
                            child_code = f"Align(alignment: Alignment({alignment_str}), child: {child_code})"
                        else:
                            child_code = f"Center(child: {child_code})"
                    else:

                        child_code = f"Center(child: {child_code})"
                
                foreground_widgets.append(child_code)
            
            stack_children.extend(foreground_widgets)
            
            if stack_children:

                body = (
                    f"Stack(children: [\n"
                    f"{indent(',\n'.join(stack_children))}\n"
                    f"])"
                )
            else:
                body = "SizedBox.shrink()"
        else:

            dart_children = []
            needs_center_wrap = False
            
            for ch in children:
                child_code = translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                child_attrs = ch.get("attrs", {}) or {}

                if _is_centered_in_constraint(child_attrs):
                    needs_center_wrap = True
                    v_bias, h_bias = _get_constraint_bias(child_attrs)
                    
                    if v_bias is not None or h_bias is not None:

                        alignment_parts = []
                        if v_bias is not None:
                            y = (v_bias - 0.5) * 2.0
                            alignment_parts.append(f"y: {y:.2f}")
                        if h_bias is not None:
                            x = (h_bias - 0.5) * 2.0
                            alignment_parts.append(f"x: {x:.2f}")
                        
                        if alignment_parts:
                            alignment_str = ", ".join(alignment_parts)
                            child_code = f"Align(alignment: Alignment({alignment_str}), child: {child_code})"
                        else:
                            child_code = f"Center(child: {child_code})"
                    else:
                        child_code = f"Center(child: {child_code})"
                
                dart_children.append(child_code)
            
            if needs_center_wrap and len(dart_children) == 1:

                body = f"Center(child: Column(mainAxisSize: MainAxisSize.min, mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.stretch, children: [\n{indent(dart_children[0])}\n]))"
            else:
                body = f"Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [\n{indent(',\n'.join(dart_children))}\n])"
        
        return apply_layout_modifiers(body, attrs, resolver)

    dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]

    body = f"Column(children: [\n{indent(',\n'.join(dart_children))}\n])"
    return apply_layout_modifiers(body, attrs, resolver)

def translate_node(node: dict, resolver, logic_map=None, fragments_by_id=None, layout_dir=None, values_dir=None):
    t = (node.get("type") or "")
    attrs = node.get("attrs", {}) or {}
    children = node.get("children", []) or []

    if t == "include":

        pass

    if t in ("androidx.constraintlayout.widget.ConstraintLayout", "ConstraintLayout"):

        return translate_layout(node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
    
    if t in ("LinearLayout", "FrameLayout", "RelativeLayout", "ConstraintLayout", "ScrollView", "HorizontalScrollView", "NestedScrollView", "ListView", "TableLayout", "TableRow", "RadioGroup"):
        return translate_layout(node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)

    if t.endswith("NestedScrollView") or t.endswith("HorizontalScrollView"):
        return translate_layout(node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)

    from translator.view_rules import translate_view
    return translate_view(node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
