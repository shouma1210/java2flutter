from __future__ import annotations

import re
import os
from typing import Any, Dict, Optional
from lxml import etree

from .parser.resource_resolver import ResourceResolver


def indent(code: str, spaces: int = 2) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in code.splitlines())


def escape_dart(s: str) -> str:
    if s is None:
        return ""
    result = str(s)
    result = result.replace("\\", "\\\\")
    result = result.replace("'", "\\'")
    result = result.replace("\n", "\\n")
    result = result.replace("\r", "\\r")
    return result


def _parse_dimen(value: str, resolver: Optional[ResourceResolver]) -> Optional[float]:
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
    if not drawable_path:
        return None

    filename = os.path.basename(drawable_path)

    return f"assets/images/{filename}"


def _parse_shape_drawable_to_boxdecoration(xml_path: str, resolver: Optional[ResourceResolver]) -> Optional[str]:

    if not xml_path or not xml_path.lower().endswith(".xml"):
        return None
    
    if not os.path.exists(xml_path):
        return None
    
    try:
        tree = etree.parse(xml_path)
        root = tree.getroot()
        

        shape = root.find(".//{http://schemas.android.com/apk/res/android}shape")
        if shape is None:

            shape = root.find(".//shape")
        if shape is None:
 
            return None
        
        decoration_parts = []
        
 
        solid = shape.find(".//{http://schemas.android.com/apk/res/android}solid")
        if solid is None:
        
            solid = shape.find(".//solid")
        if solid is not None:
           
            color_attr = solid.get("{http://schemas.android.com/apk/res/android}color")
            if not color_attr:
             
                color_attr = solid.get("color")
            if color_attr:
              
                if color_attr.startswith("?attr/"):
                
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
                    
                        decoration_parts.append("color: Colors.grey.shade200")
        
    
        corners = shape.find(".//{http://schemas.android.com/apk/res/android}corners")
        if corners is None:
            
            corners = shape.find(".//corners")
        if corners is not None:
        
            top_left = corners.get("{http://schemas.android.com/apk/res/android}topLeftRadius") or corners.get("topLeftRadius")
            top_right = corners.get("{http://schemas.android.com/apk/res/android}topRightRadius") or corners.get("topRightRadius")
            bottom_left = corners.get("{http://schemas.android.com/apk/res/android}bottomLeftRadius") or corners.get("bottomLeftRadius")
            bottom_right = corners.get("{http://schemas.android.com/apk/res/android}bottomRightRadius") or corners.get("bottomRightRadius")
            radius_attr = corners.get("{http://schemas.android.com/apk/res/android}radius") or corners.get("radius")
            
        
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
            
                radius_val = _parse_dimen(radius_attr, resolver)
                if radius_val:
                    decoration_parts.append(f"borderRadius: BorderRadius.circular({radius_val})")
        
    
        stroke = shape.find(".//{http://schemas.android.com/apk/res/android}stroke")
        if stroke is None:
        
            stroke = shape.find(".//stroke")
        if stroke is not None:
            width_attr = stroke.get("{http://schemas.android.com/apk/res/android}width") or stroke.get("width")
            color_attr = stroke.get("{http://schemas.android.com/apk/res/android}color") or stroke.get("color")
            if width_attr and color_attr:
                width_val = _parse_dimen(width_attr, resolver)
            
                if color_attr.startswith("?attr/"):
              
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
  
            import traceback
            print(f"[DEBUG] decoration_parts is empty for {xml_path}")
            return "BoxDecoration(color: Colors.grey.shade200)"
    except Exception as e:
  
        import traceback
        print(f"[WARN] Failed to parse shape drawable {xml_path}: {e}")
        traceback.print_exc()
        return "BoxDecoration(color: Colors.grey.shade200)"


def apply_layout_modifiers(
    widget: str,
    attrs: Dict[str, Any],
    resolver: Optional[ResourceResolver],
) -> str:

    if attrs is None:
        attrs = {}


    bg_raw = attrs.get("background")
    if bg_raw:
      
        drawable_path = None
        if resolver:
            drawable_path = resolver.resolve_drawable_path(bg_raw)
        
        if drawable_path:
        
            if drawable_path.lower().endswith(".xml"):
           
                decoration_code = _parse_shape_drawable_to_boxdecoration(drawable_path, resolver)
                if decoration_code:
                    widget = (
                        f"Container("
                        f"decoration: {decoration_code}, "
                        f"child: {widget}"
                        f")"
                    )
                else:
                  
                    widget = (
                        f"Container("
                        f"decoration: BoxDecoration(color: Colors.grey.shade200), "
                        f"child: {widget}"
                        f")"
                    )
            else:
              
                asset_path = get_asset_path_from_drawable(drawable_path)
                if asset_path:
              
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
          
            if resolver:
                resolved = resolver.resolve(bg_raw) or bg_raw
       
                if isinstance(resolved, str) and resolved.startswith("@color/"):
                    resolved = resolver.resolve(resolved) or resolved
            else:
                resolved = bg_raw
            
         
            if isinstance(resolved, str) and resolved.startswith("@android:color/"):
                if resolver:
                    resolved = resolver.resolve(resolved) or resolved
            
            color_hex = ResourceResolver.android_color_to_flutter(resolved)
            if color_hex:

                card_corner_radius = attrs.get("cardCornerRadius") or attrs.get("card_view:cardCornerRadius")
                if card_corner_radius:
                    radius_val = _parse_dimen(card_corner_radius, resolver)
                    if radius_val:
                        widget = f"Container(decoration: BoxDecoration(color: Color({color_hex}), borderRadius: BorderRadius.circular({radius_val})), child: {widget})"
                    else:
                        widget = f"Container(color: Color({color_hex}), child: {widget})"
                else:
                    widget = f"Container(color: Color({color_hex}), child: {widget})"
            else:

                widget = widget.replace(f"/* TODO: background {bg_raw} */ ", "")

  
    padding_ei = _edge_insets_from_attrs(attrs, resolver, "padding")
    if padding_ei:
        widget = f"Padding(padding: {padding_ei}, child: {widget})"

    
    margin_ei = _edge_insets_from_attrs(attrs, resolver, "layout_margin")
    if margin_ei:
        widget = f"Padding(padding: {margin_ei}, child: {widget})"


    card_corner_radius = attrs.get("cardCornerRadius") or attrs.get("card_view:cardCornerRadius")
    if card_corner_radius and not bg_raw:
        radius_val = _parse_dimen(card_corner_radius, resolver)
        if radius_val:

            if not widget.startswith("Container("):
                widget = (
                    f"Container("
                    f"decoration: BoxDecoration("
                    f"color: Colors.white, "
                    f"borderRadius: BorderRadius.circular({radius_val})"
                    f"), "
                    f"child: {widget}"
                    f")"
                )

  
    return widget
