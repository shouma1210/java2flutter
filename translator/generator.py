from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from parser.xml_parser import parse_layout_xml
from parser.resource_resolver import ResourceResolver
from parser.java_parser import (
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

from translator.layout_rules import translate_node

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    Environment = None
    FileSystemLoader = None

@dataclass
class UnifiedScreenIR:
    xml_ir: dict
    resolver: Optional[ResourceResolver]
    handlers_by_id: Dict[str, ClickHandlerIR]
    fragments_by_id: Dict[str, FragmentIR]
    backgrounds: Dict[str, str]

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

def _collect_backgrounds_from_ir(
    node: dict,
    bg_map: Dict[str, Dict[str, str]],
    is_root: bool = False,
) -> None:
    attrs = node.get("attrs") or {}

    if is_root:
        root_bg = attrs.get("background")
        if root_bg:
            bg_map.setdefault("__root__", {}).setdefault("background", root_bg)

    raw_id = attrs.get("id")
    if raw_id:
        key = raw_id.split("/")[-1]
        bg = attrs.get("background")
        if bg:
            bg_map.setdefault(key, {}).setdefault("background", bg)

    for ch in node.get("children") or []:
        _collect_backgrounds_from_ir(ch, bg_map, is_root=False)

def _merge_backgrounds_into_main(
    main_ir: dict,
    bg_map: Dict[str, Dict[str, str]],
) -> Dict[str, str]:
    applied: Dict[str, str] = {}

    attrs = main_ir.get("attrs") or {}
    if "background" not in attrs and "__root__" in bg_map:
        root_bg = bg_map["__root__"].get("background")
        if root_bg:
            attrs["background"] = root_bg
            applied["__root__"] = root_bg

    def _walk(node: dict) -> None:
        attrs = node.get("attrs") or {}
        raw_id = attrs.get("id")
        if raw_id:
            key = raw_id.split("/")[-1]
            if key in bg_map and "background" not in attrs:
                bg = bg_map[key].get("background")
                if bg:
                    attrs["background"] = bg
                    applied[key] = bg
        for ch in node.get("children") or []:
            _walk(ch)

    _walk(main_ir)
    return applied

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

def _collect_text_field_ids(ir: dict) -> List[str]:
    controllers: List[str] = []
    
    def _walk(node: dict) -> None:
        t = (node.get("type") or "").lower()
        attrs = node.get("attrs") or {}
        raw_id = attrs.get("id")
        
        if (t == "edittext" or t.endswith("edittext")) and raw_id:

            field_id = raw_id.split("/")[-1]

            controller_base = field_id.replace("edit", "").replace("Edit", "")
            if controller_base:
                controller_name = f"_{controller_base[0].lower()}{controller_base[1:]}Controller"
                if controller_name not in controllers:
                    controllers.append(controller_name)
        
        for ch in node.get("children") or []:
            _walk(ch)
    
    _walk(ir)
    return controllers

def _extract_activity_class_from_intent(args: str) -> Optional[str]:

    patterns = [
        r'new\s+Intent\s*\([^,]+,\s*(\w+Activity)\.class\)',
        r'new\s+Intent\s*\([^,]+,\s*(\w+)\.class\)',
    ]
    for pattern in patterns:
        m = re.search(pattern, args)
        if m:
            activity_name = m.group(1)

            if activity_name.endswith("Activity"):
                base_name = activity_name[:-8]
                return f"Converted{base_name}"
            return f"Converted{activity_name}"
    return None

def _java_ast_block_to_dart(block: Block, known_imports: Set[str]) -> str:
    lines: List[str] = []

    for stmt in block.statements:
        if isinstance(stmt, MethodCall):
            target = stmt.target or ""
            args = (stmt.args or "").strip()

            if re.match(r'^\w+\+\+$', target) or re.match(r'^\w+--$', target):
                var_name = target.rstrip('+-')

                if var_name == "refreshKeys":
                    pass
                else:
                    op = '++' if '++' in target else '--'
                    lines.append(f"setState(() {{ {var_name}{op}; }});")
                continue

            if target.startswith("if") and "isTaskRoot" in target:

                known_imports.add("Navigator")
                if "startActivity" in args and "new Intent" in args:
                    activity_class = _extract_activity_class_from_intent(args)
                    if activity_class:
                        lines.append("if (!Navigator.canPop(context)) {")
                        lines.append(
                            f"  Navigator.push(context, "
                            f"MaterialPageRoute(builder: (_) => const {activity_class}()));"
                        )
                        lines.append("}")
                    else:

                        pass
                else:

                    pass
            elif "startActivity" in target:

                activity_class = _extract_activity_class_from_intent(args)
                if activity_class:
                    known_imports.add("Navigator")
                    lines.append(
                        f"Navigator.push(context, "
                        f"MaterialPageRoute(builder: (_) => {activity_class}()));"
                    )
                else:

                    pass
            elif "finish" in target and not args:

                known_imports.add("Navigator")
                lines.append("Navigator.maybePop(context);")
            elif "finishAffinity" in target:

                known_imports.add("Navigator")
                lines.append("Navigator.popUntil(context, (route) => route.isFirst);")
            elif "Toast.makeText" in target:
                known_imports.add("ScaffoldMessenger")

                msg_match = re.search(r'["\']([^"\']+)["\']', args)
                msg = msg_match.group(1) if msg_match else "TODO: port Toast"
                lines.append(
                    f"ScaffoldMessenger.of(context).showSnackBar("
                    f"SnackBar(content: Text('{msg}')));"
                )

            elif re.match(r'^\w+$', target) and not args:

                if target == "refreshKeys":
                    pass
                else:

                    lines.append(f"setState(() {{ _{target}(); }});")
            elif re.match(r'^\w+$', target) and args:

                clean_args = args.rstrip(';').strip()

                if clean_args.startswith('"') and clean_args.endswith('"'):
                    clean_args = f"'{clean_args[1:-1]}'"
                lines.append(f"setState(() {{ _{target}({clean_args}); }});")
            else:

                pass
        elif isinstance(stmt, IfStmt):
            cond = stmt.condition.strip() or "/* condition */"

            if "isTaskRoot" in cond:

                known_imports.add("Navigator")
                cond = "!Navigator.canPop(context)"
            lines.append(f"if ({cond}) {{")

            has_start_activity = False
            for sub_stmt in stmt.then_block.statements:
                if isinstance(sub_stmt, MethodCall):
                    target = sub_stmt.target or ""
                    args = (sub_stmt.args or "").strip()
                    if "startActivity" in target:
                        activity_class = _extract_activity_class_from_intent(args)
                        if activity_class:
                            known_imports.add("Navigator")
                            lines.append(
                                f"  Navigator.push(context, "
                                f"MaterialPageRoute(builder: (_) => {activity_class}()));"
                            )
                            has_start_activity = True
                            break

            if not has_start_activity:
                inner = _java_ast_block_to_dart(stmt.then_block, known_imports)
                if inner.strip():

                    has_return = False
                    for ln in inner.splitlines():
                        if ln.strip() == "return;":
                            has_return = True
                            lines.append("  " + ln)
                            break
                        elif ln.strip():
                            lines.append("  " + ln)
                        else:
                            lines.append(ln)

                    if has_return:
                        lines.append("}")
                        continue
                else:

                    for sub_stmt in stmt.then_block.statements:
                        if isinstance(sub_stmt, RawStmt):
                            txt = sub_stmt.text.strip()
                            if "startActivity" in txt and "new Intent" in txt:
                                activity_class = _extract_activity_class_from_intent(txt)
                                if activity_class:
                                    known_imports.add("Navigator")
                                    lines.append(
                                        f"  Navigator.push(context, "
                                        f"MaterialPageRoute(builder: (_) => {activity_class}()));"
                                    )
                                    has_start_activity = True
                                    break

                            elif txt.strip() == "return" or re.match(r'^\s*return\s*;?\s*$', txt):
                                lines.append("  return;")
                                lines.append("}")
                                continue
            lines.append("}")
            if stmt.else_block:
                lines.append("else {")
                inner = _java_ast_block_to_dart(stmt.else_block, known_imports)
                if inner.strip():
                    for ln in inner.splitlines():
                        if ln.strip():
                            lines.append("  " + ln)
                lines.append("}")
        elif isinstance(stmt, RawStmt):
            txt = stmt.text.strip()
            if txt:

                if txt == "}" or re.match(r'^\s*\}\s*$', txt):

                    pass

                elif txt.startswith("if") and "{" in txt:

                    if_pattern = re.compile(
                        r'if\s*\((?P<cond>[^)]*)\)\s*\{(?P<then>(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}'
                        r'(\s*else\s*\{(?P<else>(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\})?',
                        re.DOTALL,
                    )
                    if_match = if_pattern.search(txt)
                    if if_match:
                        cond = if_match.group("cond").strip()
                        then_body = if_match.group("then").strip()
                        else_body = (if_match.group("else") or "").strip() or None

                        if "isTaskRoot" in cond:
                            known_imports.add("Navigator")
                            cond = "!Navigator.canPop(context)"
                        
                        lines.append(f"if ({cond}) {{")

                        from parser.java_parser import Block, _append_simple_statements
                        then_block = Block()
                        _append_simple_statements(then_block, then_body)
                        inner = _java_ast_block_to_dart(then_block, known_imports)
                        if inner.strip():

                            for ln in inner.splitlines():
                                if ln.strip():
                                    lines.append("  " + ln)
                        lines.append("}")
                        
                        if else_body:
                            lines.append("else {")
                            else_block = Block()
                            _append_simple_statements(else_block, else_body)
                            inner = _java_ast_block_to_dart(else_block, known_imports)
                            if inner.strip():
                                for ln in inner.splitlines():
                                    if ln.strip():
                                        lines.append("  " + ln)
                            lines.append("}")
                        continue

                elif "Toast.makeText" in txt:
                    known_imports.add("ScaffoldMessenger")

                    msg_match = re.search(r'["\']([^"\']+)["\']', txt)
                    msg = msg_match.group(1) if msg_match else "TODO: port Toast"
                    lines.append(
                        f"ScaffoldMessenger.of(context).showSnackBar("
                        f"SnackBar(content: Text('{msg}')));"
                    )

                elif re.search(r'^\s*long\s+\w+\s*=', txt):

                    pass

                elif re.match(r'^\s*String\s+\w+\s*=', txt) and ('getText()' in txt or '.getText()' in txt):

                    if "selectedMood" in txt or "RadioButton" in txt:

                        var_match = re.match(r'^\s*String\s+(\w+)\s*=', txt)
                        if var_match:
                            result_var = var_match.group(1)
                            lines.append(f"String {result_var} = _selectedMood; // Use state variable instead of RadioButton.getText()")
                        else:
                            lines.append(f"String mood = _selectedMood; // Use state variable instead of RadioButton.getText()")
                    else:

                        var_match = re.search(r'(\w+)\.getText\(\)', txt)
                        if var_match:
                            edit_text_var = var_match.group(1)
                            result_var_match = re.match(r'^\s*String\s+(\w+)\s*=', txt)
                            if result_var_match:
                                result_var = result_var_match.group(1)

                                controller_base = edit_text_var.replace('edit', '').replace('Edit', '')
                                controller_name = f"_{controller_base[0].lower()}{controller_base[1:]}Controller"
                                lines.append(f"String {result_var} = {controller_name}.text;")
                            else:

                                pass
                        else:

                            pass

                elif "getCheckedRadioButtonId" in txt:

                    pass

                elif "findViewById" in txt and "RadioButton" in txt:

                    pass

                elif re.search(r'selectedMood.*getText\(\)', txt) or re.search(r'selectedMood.*\.getText\(\)', txt):

                    var_match = re.search(r'String\s+(\w+)\s*=', txt)
                    if var_match:
                        result_var = var_match.group(1)
                        lines.append(f"String {result_var} = _selectedMood; // Use state variable instead of RadioButton.getText()")
                    else:
                        lines.append(f"String mood = _selectedMood; // Use state variable instead of RadioButton.getText()")

                elif re.search(r'_selected[mM]oodController', txt):

                    var_match = re.search(r'String\s+(\w+)\s*=\s*_selected[mM]oodController\.text', txt)
                    if var_match:
                        result_var = var_match.group(1)
                        lines.append(f"String {result_var} = _selectedMood; // Use state variable instead of controller")
                    else:
                        lines.append(f"String mood = _selectedMood; // Use state variable instead of controller")
                    continue

                elif "setContentView" in txt or "R.layout" in txt:

                    pass

                elif "android.content.Intent" in txt or ("new Intent" in txt and ("android.content" in txt or "Intent" in txt)):

                    pass

                elif "AppDatabase" in txt or "Room" in txt or "journalDao" in txt or "getAllJournals" in txt or "searchJournals" in txt or "insert" in txt and "Journal" in txt or "deleteById" in txt:

                    pass

                elif re.match(r'^\s*super\.(onCreate|onResume|onPause|onDestroy)', txt):

                    pass

                elif re.search(r'\bs\.toString\(\)', txt) or re.search(r'\bs\s*\.\s*toString\(\)', txt):

                    if "_performSearch" in txt:

                        dart_txt = txt.replace("s.toString()", "_searchController.text")
                        dart_txt = dart_txt.replace("s.toString()", "_searchController.text")

                        pass
                    else:

                        pass

                elif "Integer.parseInt" in txt:
                    dart_txt = txt.replace("Integer.parseInt", "int.parse")

                    if not dart_txt.endswith(';'):
                        dart_txt += ';'
                    lines.append(dart_txt)

                elif re.match(r'^\s*Calendar\s+\w+\s*=\s*Calendar\.getInstance', txt):

                    pass

                elif re.search(r'^\s*java\.util\.Calendar\s+\w+\s*=\s*java\.util\.Calendar\.getInstance', txt):

                    pass

                elif (txt == "finish()" or txt == "finish()" or txt == "finish" or 
                    txt.endswith(".finish()") or txt.endswith("finish()") or
                    re.match(r'^\s*finish\s*\(?\s*\)?\s*;?\s*$', txt)):
                    known_imports.add("Navigator")
                    lines.append("Navigator.maybePop(context);")

                elif "startActivity" in txt and "new Intent" in txt and not txt.startswith("if"):
                    activity_class = _extract_activity_class_from_intent(txt)
                    if activity_class:
                        known_imports.add("Navigator")
                        lines.append(
                            f"Navigator.push(context, "
                            f"MaterialPageRoute(builder: (_) => {activity_class}()));"
                        )
                    else:

                        pass

                elif txt.startswith("if") and "isTaskRoot" in txt:

                    if_match = re.search(r'if\s*\([^)]*\)\s*\{(.*?)\}', txt, re.DOTALL)
                    if if_match:
                        then_body = if_match.group(1).strip()

                        activity_class = _extract_activity_class_from_intent(then_body)
                        if activity_class:
                            known_imports.add("Navigator")
                            lines.append("if (!Navigator.canPop(context)) {")
                            lines.append(
                                f"  Navigator.push(context, "
                                f"MaterialPageRoute(builder: (_) => {activity_class}()));"
                            )
                            lines.append("}")
                        else:

                            pass
                    else:

                        known_imports.add("Navigator")
                        lines.append("if (!Navigator.canPop(context)) {")

                        lines.append("}")

                elif "if" in txt and "isTaskRoot" in txt and not txt.endswith("}"):

                    known_imports.add("Navigator")
                    lines.append("if (!Navigator.canPop(context)) {")

                elif re.match(r'^\s*finish\s*\(?\s*\)?\s*;?\s*$', txt, re.IGNORECASE):
                    known_imports.add("Navigator")
                    lines.append("Navigator.maybePop(context);")

                elif re.match(r'^\s*\w+\s*\+\+\s*;?\s*$', txt) or re.match(r'^\s*\w+\s*--\s*;?\s*$', txt):
                    var_match = re.match(r'^\s*(\w+)\s*(\+\+|--)\s*;?\s*$', txt)
                    if var_match:
                        var_name = var_match.group(1)

                        if var_name == "refreshKeys":
                            pass
                        else:
                            op = var_match.group(2)
                            lines.append(f"setState(() {{ {var_name}{op}; }});")
                    continue

                elif re.match(r'^\s*\w+\s*\([^)]*\)\s*;?\s*$', txt):

                    method_match = re.match(r'^\s*(\w+)\s*\(([^)]*)\)\s*;?\s*$', txt)
                    if method_match:
                        method_name = method_match.group(1)

                        if method_name == "refreshKeys":
                            pass
                        else:
                            method_args = method_match.group(2).strip()
                            if not method_args:
                                lines.append(f"setState(() {{ _{method_name}(); }});")
                            else:

                                clean_args = method_args
                                if clean_args.startswith('"') and clean_args.endswith('"'):
                                    clean_args = f"'{clean_args[1:-1]}'"
                                lines.append(f"setState(() {{ _{method_name}({clean_args}); }});")
                    continue

                elif re.search(r'\+\+|\-\-', txt) and not re.search(r'\+\=|-\=', txt):

                    var_match = re.search(r'(\w+)\s*(\+\+|--)', txt)
                    if var_match:
                        var_name = var_match.group(1)

                        if var_name == "refreshKeys":
                            pass
                        else:
                            op = var_match.group(2)
                            lines.append(f"setState(() {{ {var_name}{op}; }});")
                    continue

                elif re.search(r'\+\+|\-\-', txt) and '=' in txt:

                    if re.search(r'\+\=', txt):
                        var_match = re.match(r'^\s*(\w+)\s*\+=\s*(.+?)\s*;?\s*$', txt)
                        if var_match:
                            var_name = var_match.group(1)
                            value = var_match.group(2)
                            lines.append(f"setState(() {{ {var_name} += {value}; }});")
                        continue
                    elif re.search(r'\-=', txt):
                        var_match = re.match(r'^\s*(\w+)\s*\-=\s*(.+?)\s*;?\s*$', txt)
                        if var_match:
                            var_name = var_match.group(1)
                            value = var_match.group(2)
                            lines.append(f"setState(() {{ {var_name} -= {value}; }});")
                        continue

                elif "AlertDialog.Builder" in txt or "new AlertDialog.Builder" in txt:
                    known_imports.add("showDialog")

                    title_match = re.search(r'setTitle\s*\(\s*["\']([^"\']+)["\']', txt)

                    message_match = re.search(r'setMessage\s*\(\s*["\']([^"\']*(?:\\.[^"\']*)*)["\']', txt)
                    positive_match = re.search(r'setPositiveButton\s*\(\s*["\']([^"\']+)["\']', txt)
                    negative_match = re.search(r'setNegativeButton\s*\(\s*["\']([^"\']+)["\']', txt)
                    
                    from utils import escape_dart
                    title = title_match.group(1) if title_match else "Alert"
                    message = message_match.group(1) if message_match else ""
                    positive_text = positive_match.group(1) if positive_match else "OK"
                    negative_text = negative_match.group(1) if negative_match else None
                    
                    escaped_title = escape_dart(title)
                    escaped_message = escape_dart(message) if message else ""
                    escaped_positive = escape_dart(positive_text)
                    escaped_negative = escape_dart(negative_text) if negative_text else None
                    
                    lines.append("showDialog(")
                    lines.append("  context: context,")
                    lines.append("  builder: (BuildContext ctx) => AlertDialog(")
                    lines.append(f"    title: Text('{escaped_title}'),")
                    if message:
                        lines.append(f"    content: Text('{escaped_message}'),")
                    lines.append("    actions: [")
                    if negative_text:
                        lines.append(f"      TextButton(")
                        lines.append(f"        onPressed: () => Navigator.of(ctx).pop(),")
                        lines.append(f"        child: Text('{escaped_negative}'),")
                        lines.append(f"      ),")
                    lines.append(f"      TextButton(")
                    lines.append(f"        onPressed: () {{")

                    if "finish()" in txt:
                        lines.append(f"          Navigator.of(ctx).pop();")
                        lines.append(f"          Navigator.maybePop(context);")
                    else:
                        lines.append(f"          Navigator.of(ctx).pop();")

                    lines.append(f"        }},")
                    lines.append(f"        child: Text('{escaped_positive}'),")
                    lines.append(f"      ),")
                    lines.append("    ],")
                    lines.append("  ),")
                    lines.append(");")
                    continue

                elif txt.strip() == "return" or re.match(r'^\s*return\s*;?\s*$', txt):
                    lines.append("return;")

                    continue

                elif txt.startswith("}"):

                    remaining = txt[1:].strip()
                    if remaining:

                        pass
                    continue
                else:

                    if not (txt == "}" or txt.strip() == "}" or re.match(r'^\s*\}\s*$', txt)):

                        if txt.startswith("}"):

                            remaining = txt[1:].strip()
                            if remaining:

                                pass
                        else:

                            if re.search(r'refreshKeys\s*\(\)', txt) or re.search(r'_refreshKeys\s*\(\)', txt):

                                pass

                            elif re.search(r'setState\s*\(\s*\(\s*\)\s*\{\s*_while', txt) or re.search(r'_while\s*\(', txt) or re.search(r'cipherInputStream', txt) or re.search(r'values\.add', txt):

                                pass

                            else:
                                pass
                    else:

                        pass

    return "\n".join(lines)

def _to_camel(s: str) -> str:
    if not s:
        return s
    parts = s.replace("-", "_").split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])

def _to_snake(s: str) -> str:
    if not s:
        return s
    out: List[str] = []
    for ch in s:
        if ch.isupper():
            if out:
                out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)

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

def _load_template() -> Optional[object]:
    if Environment is None:
        return None

    project_root = os.path.dirname(os.path.dirname(__file__))
    template_dir = os.path.join(project_root, "templates")
    template_path = os.path.join(template_dir, "screen.dart.j2")

    if not os.path.exists(template_path):
        return None

    with open(template_path, "r", encoding="utf-8") as f:
        src = f.read()

    src = src.replace("{% raw %}", "").replace("{% endraw %}", "")

    env = Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )

    return env.from_string(src)

def _render_screen_with_template(
    class_name: str,
    widget_tree: str,
    handlers_code: str,
    controllers: List[str],
    options: Optional[dict] = None,
) -> str:
    tmpl = _load_template()
    ctx = {
        "class_name": class_name,
        "widget_tree": widget_tree,
        "handlers_code": handlers_code,
        "controllers": controllers,
        "options": options or {},
    }
    is_stateful = options.get("is_stateful", False)
    imports_list = options.get("imports", [])
    imports_str = "import 'package:flutter/material.dart';"
    if imports_list:

        pass

    if tmpl is None:

        if is_stateful:
            return f"""{imports_str}

class {class_name} extends StatefulWidget {{
  const {class_name}({{super.key}});

  @override
  State<{class_name}> createState() => _{class_name}State();
}}

class _{class_name}State extends State<{class_name}> {{
  @override
  Widget build(BuildContext context) {{
    return Scaffold(
      body: {widget_tree},
    );
  }}

  // ===== Auto-Generated Handlers =====
  {handlers_code}
}}
    return tmpl.render(**ctx)

def _build_logic_and_handlers(ir: UnifiedScreenIR, class_name: str, java_methods: Dict[str, str] = None):
    if java_methods is None:
        java_methods = {}
    logic_map: Dict[str, str] = {}
    handler_funcs: List[str] = []
    method_funcs: List[str] = []
    imports: Set[str] = set()

    existing_ids: Set[str] = set()

    for vid, handler_ir in ir.handlers_by_id.items():
        base = vid.split("/")[-1]
        if not base:
            continue
        existing_ids.add(base)
        func_name = f"_on{base[0].upper()}{base[1:]}Pressed"
        _register_logic_keys(logic_map, base, func_name)

        body = _java_ast_block_to_dart(handler_ir.ast, imports)
        if not body.strip() or body.strip().startswith("// TODO"):

            continue

        handler_funcs.append(
            f"void {func_name}(BuildContext context) {{\n"
            f"{_indent(body, 2)}\n"
            f"}}"
        )

    button_ids = _collect_button_ids_from_xml(ir.xml_ir)
    onclick_map = _collect_onclick_methods_from_xml(ir.xml_ir)
    
    for base in button_ids:
        if not base or base in existing_ids:
            continue

        onclick_method = onclick_map.get(base)
        if onclick_method:

            camel = onclick_method
            if camel.startswith("on"):
                camel = camel[2:]
            camel = _to_camel(camel)
            func_name = (
                f"_on{camel[:1].upper()}{camel[1:]}Pressed"
                if camel
                else "_onUnknownPressed"
            )
        else:

            camel = _to_camel(base)
            func_name = (
                f"_on{camel[:1].upper()}{camel[1:]}Pressed"
                if camel
                else "_onUnknownPressed"
            )
        _register_logic_keys(logic_map, base, func_name)

        if onclick_method and onclick_method in java_methods:

            method_body = java_methods[onclick_method]

            if any(keyword in method_body for keyword in ["AppDatabase", "Room", "journalDao", "getAllJournals", "searchJournals", "deleteById", "RecyclerView", "setAdapter", "Adapter"]):
                body = "// Button handler"
            else:

                from parser.java_parser import _parse_block_to_ast
                method_ast = _parse_block_to_ast(method_body)
                body = _java_ast_block_to_dart(method_ast, imports)

                if "setState(() { _while" in body or "cipherInputStream" in body or "values.add" in body:
                    continue

                elif not body.strip() or body.strip().startswith("// TODO"):
                    continue
        else:

            continue
        
        handler_funcs.append(
            f"void {func_name}(BuildContext context) {{\n"
            f"{_indent(body, 2)}\n"
            f"}}"
        )

    has_buttons_or_handlers = len(handler_funcs) > 0 or len(button_ids) > 0
    if has_buttons_or_handlers:
        for method_name, method_body in java_methods.items():

            if method_name in ["onCreate", "onResume", "onPause", "onDestroy", "onStart", "onStop"]:
                continue

            if any(keyword in method_body for keyword in ["AppDatabase", "Room", "journalDao", "getAllJournals", "searchJournals", "deleteById"]):
                continue

            if any(keyword in method_body for keyword in ["RecyclerView", "setAdapter", "Adapter", "loadJournals", "performSearch"]):
                continue

        from parser.java_parser import _parse_block_to_ast
        method_ast = _parse_block_to_ast(method_body)
        method_dart_body = _java_ast_block_to_dart(method_ast, imports)

        if "setState(() { _while" in method_dart_body or "cipherInputStream" in method_dart_body or "values.add" in method_dart_body:
            pass

        elif method_dart_body.strip() and not method_dart_body.strip().startswith("// TODO"):
            method_funcs.append(
                f"void _{method_name}() {{\n"
                f"{_indent(method_dart_body, 2)}\n"
                f"}}"
            )

    all_funcs = handler_funcs + method_funcs
    handlers_code = "\n\n".join(all_funcs) if all_funcs else ""
    return logic_map, handlers_code, imports

def generate_dart_code(
    xml_path: str,
    values_dir: Optional[str],
    java_root: Optional[str],
    output_path: str,
    class_name: str,
) -> None:

    xml_ir, resolver = parse_layout_xml(xml_path, values_dir)

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
            _collect_backgrounds_from_ir(sub_ir, bg_map, is_root=True)

    applied_backgrounds = _merge_backgrounds_into_main(xml_ir, bg_map)

    handlers_by_id: Dict[str, ClickHandlerIR] = {}
    java_methods: Dict[str, str] = {}
    if java_root and os.path.exists(java_root):
        xml_ids = _collect_ids(xml_ir)
        handlers_by_id = extract_click_handlers(java_root, xml_ids)
        java_methods = extract_methods(java_root)

    fragments_by_id: Dict[str, FragmentIR] = {}
    if java_root and os.path.exists(java_root):
        layout_dir = os.path.dirname(xml_path)
        fragments_by_id = extract_fragments(java_root, layout_dir, xml_ids)

    unified = UnifiedScreenIR(
        xml_ir=xml_ir,
        resolver=resolver,
        handlers_by_id=handlers_by_id,
        fragments_by_id=fragments_by_id,
        backgrounds=applied_backgrounds,
    )

    logic_map, handlers_code, known_imports = _build_logic_and_handlers(unified, class_name, java_methods)

    root_bg_color = None
    root_bg_image = None
    root_bg_decoration = None
    root_attrs = unified.xml_ir.get("attrs") or {}
    root_bg_raw = root_attrs.get("background")
    if root_bg_raw and resolver:

        drawable_path = resolver.resolve_drawable_path(root_bg_raw)
        if drawable_path:

            if drawable_path.lower().endswith(".xml"):

                from utils import _parse_shape_drawable_to_boxdecoration
                root_bg_decoration = _parse_shape_drawable_to_boxdecoration(drawable_path, resolver)

                if root_bg_decoration:
                    unified.xml_ir["attrs"] = {k: v for k, v in root_attrs.items() if k != "background"}
            else:

                from utils import get_asset_path_from_drawable
                root_bg_image = get_asset_path_from_drawable(drawable_path)

                unified.xml_ir["attrs"] = {k: v for k, v in root_attrs.items() if k != "background"}
        else:

            resolved = resolver.resolve(root_bg_raw) or root_bg_raw
            root_bg_color = ResourceResolver.android_color_to_flutter(resolved)

            if root_bg_color:

                unified.xml_ir["attrs"] = {k: v for k, v in root_attrs.items() if k != "background"}

    if not root_bg_color and not root_bg_image and not root_bg_decoration:
        root_bg_color = "0xFFFFFFFF"

    widget_tree = translate_node(unified.xml_ir, unified.resolver, logic_map=logic_map, fragments_by_id=unified.fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)

    has_stack_background = "Stack(children:" in widget_tree
    has_expanded = "Expanded(" in widget_tree

    has_listview = "ListView" in widget_tree

    has_text_field = _has_text_field(unified.xml_ir)
    controllers: List[str] = []
    if has_text_field:

        controllers = _collect_text_field_ids(unified.xml_ir)

    imports_list = list(known_imports)
    if "Navigator" in imports_list:

        pass

    dart_src = _render_screen_with_template(
        class_name=class_name,
        widget_tree=widget_tree,
        handlers_code=handlers_code,
        controllers=controllers,
        options={
            "is_stateful": has_text_field,
            "use_scrollview": not has_listview,
            "use_safearea": False,
            "add_appbar": False,
            "use_scaffold": True,
            "keyboard_dismiss": True,
            "page_padding": 0.0,
            "stretch": True,
            "imports": imports_list,
            "scaffold_bg_color": root_bg_color,
            "scaffold_bg_image": root_bg_image,
            "scaffold_bg_decoration": root_bg_decoration,
            "has_stack_background": has_stack_background, "has_expanded": has_expanded
        },
    )

    dart_src = _cleanup_dead_code(dart_src)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(dart_src)

    print(f"[INFO] Generated Dart: {output_path}")

def _cleanup_dead_code(dart_src: str) -> str:
    import re
    
    lines = dart_src.split('\n')
    cleaned_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i]

        if re.search(r'if\s*\(\s*0\.0\s*>\s*0\.0\s*\)', line):

            brace_depth = line.count('{') - line.count('}')
            j = i + 1
            while j < len(lines) and brace_depth > 0:
                brace_depth += lines[j].count('{') - lines[j].count('}')
                j += 1

            i = j
            continue

        if re.search(r'if\s*\(\s*true\s*\)\s*\{', line):

            brace_depth = line.count('{') - line.count('}')
            j = i + 1
            while j < len(lines) and brace_depth > 0:
                brace_depth += lines[j].count('{') - lines[j].count('}')
                j += 1

            inner_lines = lines[i+1:j-1]
            for inner_line in inner_lines:

                cleaned_lines.append(re.sub(r'^(\s{2,})', lambda m: m.group(1)[:-2] if len(m.group(1)) >= 2 else '', inner_line))
            i = j
            continue

        if re.search(r'if\s*\(\s*false\s*\)\s*\{', line):

            brace_depth = line.count('{') - line.count('}')
            j = i + 1
            while j < len(lines) and brace_depth > 0:
                brace_depth += lines[j].count('{') - lines[j].count('}')
                j += 1

            i = j
            continue

        if re.match(r'\s*@override\s*', line):

            if i + 3 < len(lines):
                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                dispose_line = lines[i + 2] if i + 2 < len(lines) else ""
                close_line = lines[i + 3] if i + 3 < len(lines) else ""
                if (re.match(r'\s*void\s+dispose\s*\(\s*\)\s*\{', next_line) and
                    re.match(r'\s*super\.dispose\s*\(\s*\)\s*;', dispose_line) and
                    re.match(r'\s*\}\s*', close_line)):

                    i += 4
                    continue

        if re.match(r'\s*void\s+dispose\s*\(\s*\)\s*\{', line):

            if i + 2 < len(lines):
                dispose_line = lines[i + 1] if i + 1 < len(lines) else ""
                close_line = lines[i + 2] if i + 2 < len(lines) else ""
                if (re.match(r'\s*super\.dispose\s*\(\s*\)\s*;', dispose_line) and
                    re.match(r'\s*\}\s*', close_line)):

                    i += 3
                    continue
        
        cleaned_lines.append(line)
        i += 1
    
    dart_src = '\n'.join(cleaned_lines)

    dart_src = re.sub(
        r',\s*keyboardType:\s*TextInputType\.text\s*',
        '',
        dart_src
    )
    dart_src = re.sub(
        r'\s*keyboardType:\s*TextInputType\.text\s*,',
        '',
        dart_src
    )

    dart_src = re.sub(
        r',\s*keyboardType:\s*TextInputType\.text\s*\)',
        ')',
        dart_src
    )

    dart_src = re.sub(
        r'Padding\s*\(\s*padding:\s*EdgeInsets\.(?:all|fromLTRB)\(0\.0(?:\s*,\s*0\.0)*\)\s*,\s*child:\s*([^)]+)\s*\)',
        r'\1',
        dart_src,
        flags=re.MULTILINE | re.DOTALL
    )

    dart_src = re.sub(r'\n\s*\n\s*\n+', '\n\n', dart_src)
    
    return dart_src
