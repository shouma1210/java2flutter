from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from ..parser.xml_parser import parse_layout_xml
from ..parser.resource_resolver import ResourceResolver
from ..parser.java_parser import (
    extract_click_handlers,
    extract_fragments,
    extract_methods,
    ClickHandlerIR,
    FragmentIR,
    Block,
    MethodCall,
    IfStmt,
    RawStmt,
)

from .layout_rules import translate_node

try:
    from jinja2 import Environment
except Exception:
    Environment = None


class UnifiedScreenIR:
    xml_ir: dict
    resolver: Optional[ResourceResolver]
    handlers_by_id: Dict[str, ClickHandlerIR]
    fragments_by_id: Dict[str, FragmentIR]
    backgrounds: Dict[str, str]


def _get_layout_root(ir: dict) -> dict:
    if not ir:
        return ir
    if ir.get("type") != "document":
        return ir

    children = ir.get("children") or []
    # コメント / PI ノードなど View 以外を除外して、最初の要素をルートとみなす
    for ch in children:
        t = ch.get("type")
        if t in ("comment", "pi") or t is None:
            continue
        return ch
    # フォールバック：子が無い場合は document 自体を返す
    return ir


def _collect_ids(ir: dict) -> List[str]:
    ids: List[str] = []

    def _walk(node: dict) -> None:
        attrs = node.get("attrs") or {}
        raw_id = attrs.get("id")
        if raw_id:
            ids.append(raw_id.split("/")[-1])
        for ch in node.get("children") or []:
            _walk(ch)

    _walk(ir)
    return ids


def _collect_button_ids_from_xml(ir: dict) -> List[str]:
    ids: List[str] = []

    def _walk(node: dict) -> None:
        t = (node.get("type") or "").lower()
        attrs = node.get("attrs") or {}
        raw_id = attrs.get("id")
        if raw_id and (t.endswith("button") or t == "button"):
            ids.append(raw_id.split("/")[-1])
        for ch in node.get("children") or []:
            _walk(ch)

    _walk(ir)
    return ids


def _collect_onclick_methods_from_xml(ir: dict) -> Dict[str, str]:
    onclick_map: Dict[str, str] = {}

    def _walk(node: dict) -> None:
        attrs = node.get("attrs") or {}
        raw_id = attrs.get("id")
        xml_onclick = attrs.get("onClick") or attrs.get("android:onClick")
        if raw_id and xml_onclick:
            view_id = raw_id.split("/")[-1]
            onclick_map[view_id] = xml_onclick
        for ch in node.get("children") or []:
            _walk(ch)

    _walk(ir)
    return onclick_map


def _has_text_field(ir: dict) -> bool:
    def _walk(node: dict) -> bool:
        t = (node.get("type") or "").lower()
        if t == "edittext" or t.endswith("edittext"):
            return True
        if t == "checkbox" or t.endswith("checkbox"):
            return True
        if t == "switch" or t.endswith("switch"):
            return True
        if t == "togglebutton" or t.endswith("togglebutton"):
            return True
        for ch in node.get("children") or []:
            if _walk(ch):
                return True
        return False

    return _walk(ir)


def _collect_text_controllers_from_xml(ir: dict) -> List[str]:
    controllers: Set[str] = set()

    def _walk(node: dict) -> None:
        t = (node.get("type") or "").lower()
        attrs = node.get("attrs") or {}

        if t == "edittext" or t.endswith("edittext"):
            raw_id = attrs.get("id")
            if raw_id:
                field_id = raw_id.split("/")[-1]
                base = field_id.replace("edit", "").replace("Edit", "")
                if base:
                    name = f"_{base[0].lower()}{base[1:]}Controller"
                    controllers.add(name)

        for ch in node.get("children") or []:
            _walk(ch)

    _walk(ir)
    return sorted(controllers)


def _to_camel(s: str) -> str:
    if not s:
        return s
    parts = s.replace("-", "_").split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _to_snake(s: str) -> str:
    if not s:
        return s
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    return s.replace("-", "_").lower()


def _register_logic_keys(logic_map: Dict[str, str], xml_id: str, func_name: str) -> None:
    cands = {
        xml_id,
        xml_id.lower(),
        xml_id.capitalize(),
        _to_camel(xml_id),
        _to_snake(xml_id),
    }
    for k in cands:
        if k:
            logic_map[k] = func_name


def _indent(code: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in code.splitlines())


def _java_ast_block_to_dart(block: Block, known_imports: Set[str]) -> str:
    """
    Java の AST Block を Dart のコード文字列に変換する。
    この関数は既存の実装（tree-sitter ベース）を維持しており、
    Android 特有の API（startActivity / finish / Toast 等）も変換している。
    """
    lines: List[str] = []

    for stmt in block.statements:
        if isinstance(stmt, MethodCall):
            target = stmt.target or ""
            args = (stmt.args or "").strip()

            # ++ / -- の単項演算子（i++ など）
            if re.match(r"^\w+\+\+$", target) or re.match(r"^\w+--$", target):
                var_name = target.rstrip("+-")
                op = "++" if "++" in target else "--"
                lines.append(f"setState(() {{ {var_name}{op}; }});")
                continue

            # setOnClickListener 自体はここでは処理しない（java_parser 側で ClickHandlerIR にしている想定）
            if target.endswith(".setOnClickListener") or target == "setOnClickListener":
                continue

            # startActivity(new Intent(this, SomeActivity.class))
            if "startActivity" in target:
                # Intent から Activity クラス名をざっくり抜き、Flutter の Navigator.push に変換
                m = re.search(
                    r"new\s+Intent\s*\([^,]+,\s*([A-Za-z0-9_]+)\s*\.class",
                    args,
                )
                if m:
                    activity_class = m.group(1)
                    known_imports.add("Navigator")
                    lines.append(
                        f"Navigator.push("
                        f"context, MaterialPageRoute(builder: (_) => {activity_class}()));"
                    )
                else:
                    # うまく取れない場合はそのまま Dart 呼び出しとして残す
                    full = f"{target}({args})" if target else args
                    lines.append(f"{full};")
                continue

            # finish() / this.finish()
            if target.endswith(".finish") or target == "finish":
                known_imports.add("Navigator")
                lines.append("Navigator.maybePop(context);")
                continue

            # Toast.makeText(...).show()
            if "Toast.makeText" in target or "Toast.makeText" in args:
                full_expr = f"{target}({args})" if target else args
                m = re.search(
                    r"Toast\.makeText\s*\([^,]+,\s*(.+?),\s*Toast\.",
                    full_expr,
                )
                if m:
                    msg_expr = m.group(1).strip()
                else:
                    msg_expr = '"Notification"'
                known_imports.add("SnackBar")
                lines.append(
                    "ScaffoldMessenger.of(context).showSnackBar("
                    f"SnackBar(content: Text({msg_expr})));"
                )
                continue

            # その他のメソッド呼び出しは、そのまま Dart の文として落とす
            full = f"{target}({args})" if target else args
            if full and not full.endswith(";"):
                full = full + ";"
            lines.append(full)

        elif isinstance(stmt, IfStmt):
            cond = (stmt.condition or "").strip()
            then_body = _java_ast_block_to_dart(stmt.then_block, known_imports)
            lines.append(f"if ({cond}) {{")
            if then_body.strip():
                lines.append(_indent(then_body, 2))
            lines.append("}")
            if stmt.else_block:
                else_body = _java_ast_block_to_dart(stmt.else_block, known_imports)
                lines.append("else {")
                if else_body.strip():
                    lines.append(_indent(else_body, 2))
                lines.append("}")

        elif isinstance(stmt, RawStmt):
            raw = (stmt.text or "").strip()
            if not raw:
                continue
            # tree-sitter で構造化できなかった行は、そのまま Dart コードとして
            # 実行すると壊れる可能性が高いので、コメントとして残す。
            lines.append("// " + raw)

    dart_src = "\n".join(lines)

    # 余計な空行を削る
    dart_src = re.sub(r"\n\s*\n\s*\n+", "\n\n", dart_src)
    return dart_src


def _load_template():
    """
    Try to load a Jinja2 template, if Jinja2 is installed and the screen.dart.j2
    template is available. Otherwise return None and use a fallback.
    """
    if Environment is None:
        return None

    here = os.path.dirname(os.path.abspath(__file__))
    tmpl_path = os.path.join(here, "screen.dart.j2")
    if not os.path.isfile(tmpl_path):
        return None

    with open(tmpl_path, "r", encoding="utf-8") as f:
        contents = f.read()

    env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
    return env.from_string(contents)


def _render_template(
    class_name: str,
    widget_tree: str,
    handlers_code: str,
    controllers: List[str],
    options: Dict[str, object] | None,
) -> str:
    tmpl = _load_template()
    options = options or {}
    force_min_height = bool(options.get("force_min_height"))
    ctx = {
        "class_name": class_name,
        "widget_tree": widget_tree,
        "handlers_code": handlers_code,
        "controllers": controllers,
        "options": options,
    }
    if tmpl is not None:
        return tmpl.render(**ctx)

    # fallback
    # Jinja2 テンプレートが無い環境でも確実に Flutter の型が解決できるよう、
    # フォールバックでは常に material を import する。
    imports = options.get("imports") or []
    imports_str = "import 'package:flutter/material.dart';\n"
    is_stateful = bool(options.get("is_stateful", True))

    controller_fields = ""
    if controllers:
        controller_fields = "\n".join(
            f"  final TextEditingController {name} = TextEditingController();"
            for name in controllers
        )

    dispose_body = ""
    if controllers:
        dispose_body = "\n".join(f"    {name}.dispose();" for name in controllers)

    body_wrapper_decl = ""
    body_var = "content"
    if force_min_height:
        body_wrapper_decl = """
    final body = ConstrainedBox(
      constraints: BoxConstraints(
        minHeight: MediaQuery.of(context).size.height,
      ),
      child: content,
    );"""
        body_var = "body"

    if is_stateful:
        return f"""{imports_str}

class {class_name} extends StatefulWidget {{
  const {class_name}({{super.key}});
 
  @override
  State<{class_name}> createState() => _{class_name}State();
}}

class _{class_name}State extends State<{class_name}> {{
{controller_fields}
  {handlers_code}

  @override
  void dispose() {{
{dispose_body}
    super.dispose();
  }}

  @override
  Widget build(BuildContext context) {{
    Widget content = {widget_tree};{body_wrapper_decl}

    return Scaffold(
      backgroundColor: Colors.white,
      body: SafeArea(
        child: SingleChildScrollView(
          child: {body_var},
        ),
      ),
    );
  }}
}}
"""
    else:
        return f"""{imports_str}

class {class_name} extends StatelessWidget {{
  const {class_name}({{super.key}});

  {handlers_code}

  @override
  Widget build(BuildContext context) {{
    Widget content = {widget_tree};{body_wrapper_decl}

    return Scaffold(
      backgroundColor: Colors.white,
      body: SafeArea(
        child: SingleChildScrollView(
          child: {body_var},
        ),
      ),
    );
  }}
}}
"""


def _build_logic_and_handlers(
    ir: UnifiedScreenIR,
    class_name: str,
    java_methods: Dict[str, str] = None,
):
    if java_methods is None:
        java_methods = {}

    logic_map: Dict[str, str] = {}
    handler_funcs: List[str] = []
    existing_ids: Set[str] = set()

    for vid, handler_ir in ir.handlers_by_id.items():
        base = vid.split("/")[-1]
        if not base:
            continue

        existing_ids.add(base)
        func_name = f"_on{base[0].upper()}{base[1:]}Pressed"

        _register_logic_keys(logic_map, base, func_name)
        handler_funcs.append(
            f"void {func_name}(BuildContext context) {{\n"
            f"}}"
        )

    button_ids = _collect_button_ids_from_xml(ir.xml_ir)
    onclick_map = _collect_onclick_methods_from_xml(ir.xml_ir)

    for base in button_ids:
        if not base:
            continue

        if base in existing_ids:
            continue

        onclick_method = onclick_map.get(base)

        if onclick_method:
            camel = onclick_method
            if camel.startswith("on"):
                camel = camel[2:]
            camel = _to_camel(camel)
        else:
            camel = _to_camel(base)

        func_name = (
            f"_on{camel[:1].upper()}{camel[1:]}Pressed"
            if camel
            else "_onUnknownPressed"
        )

        _register_logic_keys(logic_map, base, func_name)

        handler_funcs.append(
            f"void {func_name}(BuildContext context) {{\n"
            f"}}"
        )

    handlers_code = "\n\n".join(handler_funcs) if handler_funcs else ""
    return logic_map, handlers_code, set()


def _collect_backgrounds_from_ir(
    main_ir: dict,
    bg_map: Dict[str, Dict[str, str]],
    is_root: bool = False,
) -> None:
    # ここは既存実装のまま（背景 drawable の解決）
    attrs = main_ir.get("attrs") or {}
    bg = attrs.get("background")
    if bg:
        key = "root" if is_root else attrs.get("id") or ""
        if key:
            bg_map.setdefault(key, {})["background"] = bg

    for ch in main_ir.get("children") or []:
        _collect_backgrounds_from_ir(ch, bg_map, is_root=False)


def _merge_backgrounds_into_main(
    main_ir: dict,
    bg_map: Dict[str, Dict[str, str]],
) -> Dict[str, str]:
    applied: Dict[str, str] = {}

    attrs = main_ir.get("attrs") or {}
    if "background" in attrs:
        applied["root"] = attrs["background"]

    for key, info in bg_map.items():
        if "background" in info:
            applied[key] = info["background"]

    return applied


def generate_dart_code(
    xml_path: str,
    values_dir: Optional[str],
    java_root: Optional[str],
    output_path: str,
    class_name: str,
) -> None:
    xml_ir, resolver = parse_layout_xml(xml_path, values_dir)
    layout_root = _get_layout_root(xml_ir)

    bg_map: Dict[str, Dict[str, str]] = {}
    layout_dir = os.path.dirname(xml_path)
    if os.path.isdir(layout_dir):
        for fn in os.listdir(layout_dir):
            if not fn.endswith(".xml"):
                continue
            sub_path = os.path.join(layout_dir, fn)
            try:
                sub_ir, _ = parse_layout_xml(sub_path, values_dir)
            except Exception:
                continue
            sub_root = _get_layout_root(sub_ir)
            _collect_backgrounds_from_ir(sub_root, bg_map, is_root=True)

    applied_backgrounds = _merge_backgrounds_into_main(xml_ir, bg_map)

    handlers_by_id: Dict[str, ClickHandlerIR] = {}
    java_methods: Dict[str, str] = {}
    xml_ids: List[str] = []

    if java_root and os.path.exists(java_root):
        xml_ids = _collect_ids(layout_root)
        handlers_by_id = extract_click_handlers(java_root, xml_ids)
        java_methods = extract_methods(java_root)

    fragments_by_id: Dict[str, FragmentIR] = {}
    if java_root and os.path.exists(java_root):
        layout_dir = os.path.dirname(xml_path)
        fragments_by_id = extract_fragments(java_root, layout_dir, xml_ids)

    unified = UnifiedScreenIR()
    unified.xml_ir = layout_root
    unified.resolver = resolver
    unified.handlers_by_id = handlers_by_id
    unified.fragments_by_id = fragments_by_id
    unified.backgrounds = applied_backgrounds

    logic_map, handlers_code, known_imports = _build_logic_and_handlers(
        unified,
        class_name,
        java_methods,
    )

    root_bg_color = None
    root_bg_image = None
    root_bg_decoration = None
    root_attrs = unified.xml_ir.get("attrs") or {}
    root_bg_raw = root_attrs.get("background")

    if root_bg_raw and resolver:
        drawable_path = resolver.resolve_drawable_path(root_bg_raw)
        if drawable_path:
            if drawable_path.lower().endswith(".xml"):
                from ..utils import _parse_shape_drawable_to_boxdecoration

                root_bg_decoration = _parse_shape_drawable_to_boxdecoration(
                    drawable_path,
                    resolver,
                )
                if root_bg_decoration:
                    unified.xml_ir["attrs"] = {
                        k: v for k, v in root_attrs.items() if k != "background"
                    }
            else:
                from ..utils import parse_image_to_decoration

                root_bg_image = parse_image_to_decoration(drawable_path, resolver)
                if root_bg_image:
                    unified.xml_ir["attrs"] = {
                        k: v for k, v in root_attrs.items() if k != "background"
                    }
        else:
            resolved = resolver.resolve(root_bg_raw) or root_bg_raw
            root_bg_color = ResourceResolver.android_color_to_flutter(resolved)
            if root_bg_color:
                unified.xml_ir["attrs"] = {
                    k: v for k, v in root_attrs.items() if k != "background"
                }

    if not root_bg_color and not root_bg_image and not root_bg_decoration:
        root_bg_color = "0xFFFFFFFF"

    widget_tree = translate_node(
        unified.xml_ir,
        unified.resolver,
        logic_map=logic_map,
        fragments_by_id=unified.fragments_by_id,
        layouts_backgrounds=unified.backgrounds,
        root_bg_color=root_bg_color,
        root_bg_image=root_bg_image,
        root_bg_decoration=root_bg_decoration,
    )

    controllers: List[str] = _collect_text_controllers_from_xml(unified.xml_ir)

    root_layout_width = (root_attrs.get("layout_width") or "").lower()
    root_layout_height = (root_attrs.get("layout_height") or "").lower()
    force_min_height = (
        root_layout_width == "match_parent" and root_layout_height == "match_parent"
    )

    options: Dict[str, object] = {
        "is_stateful": True,
        "imports": list(known_imports),
        "force_min_height": force_min_height,
    }

    dart_src = _render_template(
        class_name,
        widget_tree,
        handlers_code,
        controllers,
        options,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(dart_src)