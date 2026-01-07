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
    ClickHandlerIR,
    FragmentIR,
    Block,
    MethodCall,
    IfStmt,
    RawStmt,
)

from .layout_rules import translate_node

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:  # type: ignore
    Environment = None  # type: ignore
    FileSystemLoader = None  # type: ignore


# ============================
# 1. 統合 IR
# ============================

@dataclass
class UnifiedScreenIR:
    """XML UI ツリーと Java ハンドラ IR を統合した中間表現."""
    xml_ir: dict
    resolver: Optional[ResourceResolver]
    handlers_by_id: Dict[str, ClickHandlerIR]
    fragments_by_id: Dict[str, FragmentIR]
    backgrounds: Dict[str, str]


# ============================
# 2. XML 側ユーティリティ
# ============================

def _collect_ids(ir: dict) -> List[str]:
    """XML IR から @+id/xxx → xxx の一覧を取得."""
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
    """1 つの XML IR から id / ルートごとの background を集計."""
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
    """他レイアウトから集めた背景情報を main_ir にマージ."""
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
    """XML IR から Button 系 View の id 一覧（xxx 部分）を取得."""
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


def _has_text_field(ir: dict) -> bool:
    """XML IR に EditText が含まれているかチェック."""
    def _walk(node: dict) -> bool:
        t = (node.get("type") or "").lower()
        if t == "edittext" or t.endswith("edittext"):
            return True
        for ch in node.get("children") or []:
            if _walk(ch):
                return True
        return False

    return _walk(ir)


# ============================
# 3. Java AST IR → Dart 文
# ============================

def _extract_activity_class_from_intent(args: str) -> Optional[str]:
    """Intent引数からActivityクラス名を抽出してDartクラス名に変換"""
    # new Intent(LoginActivity.this, OptionActivity.class) から OptionActivity を抽出
    # または new Intent(this, HomeActivity.class) から HomeActivity を抽出
    patterns = [
        r'new\s+Intent\s*\([^,]+,\s*(\w+Activity)\.class\)',  # new Intent(..., OptionActivity.class)
        r'new\s+Intent\s*\([^,]+,\s*(\w+)\.class\)',  # new Intent(..., HomeActivity.class)
    ]
    for pattern in patterns:
        m = re.search(pattern, args)
        if m:
            activity_name = m.group(1)
            # Activity を削除して Converted を付ける（例: OptionActivity → ConvertedOption）
            if activity_name.endswith("Activity"):
                base_name = activity_name[:-8]  # "Activity" の8文字を削除
                return f"Converted{base_name}"
            return f"Converted{activity_name}"
    return None


def _java_ast_block_to_dart(block: Block, known_imports: Set[str]) -> str:
    """ミニ AST(Block) から Dart のステートメント列を生成（簡易版)."""
    lines: List[str] = []

    for stmt in block.statements:
        if isinstance(stmt, MethodCall):
            target = stmt.target or ""
            args = (stmt.args or "").strip()
            
            # if(isTaskRoot())のような特殊なケースをチェック（targetがifで始まる場合）
            if target.startswith("if") and "isTaskRoot" in target:
                # if(isTaskRoot()) { startActivity(...); } のような構造
                # argsにstartActivityが含まれている可能性がある
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
                        lines.append("if (!Navigator.canPop(context)) {")
                        lines.append("  // TODO: port if body")
                        lines.append("}")
                else:
                    lines.append("if (!Navigator.canPop(context)) {")
                    lines.append("  // TODO: port if body")
                    lines.append("}")
            elif "startActivity" in target:
                # startActivity(new Intent(...)) → Navigator.push
                activity_class = _extract_activity_class_from_intent(args)
                if activity_class:
                    known_imports.add("Navigator")
                    lines.append(
                        f"Navigator.push(context, "
                        f"MaterialPageRoute(builder: (_) => const {activity_class}()));"
                    )
                else:
                    # Intent解析に失敗した場合
                    lines.append("// TODO: port startActivity → Navigator.push(...)")
            elif "finish" in target and not args:
                # finish() → Navigator.maybePop
                known_imports.add("Navigator")
                lines.append("Navigator.maybePop(context);")
            elif "finishAffinity" in target:
                # finishAffinity() → Navigator.popUntil
                known_imports.add("Navigator")
                lines.append("Navigator.popUntil(context, (route) => route.isFirst);")
            elif "Toast.makeText" in target:
                known_imports.add("ScaffoldMessenger")
                # Toast.makeText(this, msg, Toast.LENGTH_LONG) からメッセージを抽出
                msg_match = re.search(r'["\']([^"\']+)["\']', args)
                msg = msg_match.group(1) if msg_match else "TODO: port Toast"
                lines.append(
                    f"ScaffoldMessenger.of(context).showSnackBar("
                    f"SnackBar(content: Text('{msg}')));"
                )
            else:
                arg_str = args
                lines.append(f"// TODO: port Java call: {target}({arg_str})")
        elif isinstance(stmt, IfStmt):
            cond = stmt.condition.strip() or "/* condition */"
            # isTaskRoot() などのメソッド呼び出しを適切に変換
            if "isTaskRoot" in cond:
                # isTaskRoot() → Navigator.canPop(context) の否定
                known_imports.add("Navigator")
                cond = "!Navigator.canPop(context)"
            lines.append(f"if ({cond}) {{")
            # then_blockのstatementsを直接確認してstartActivityを処理
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
                                f"MaterialPageRoute(builder: (_) => const {activity_class}()));"
                            )
                            has_start_activity = True
                            break
            
            # startActivityが見つからなかった場合、再帰的に処理
            if not has_start_activity:
                inner = _java_ast_block_to_dart(stmt.then_block, known_imports)
                if inner.strip():
                    # 各行をインデントして追加（空行も保持）
                    for ln in inner.splitlines():
                        if ln.strip():
                            lines.append("  " + ln)
                        else:
                            lines.append(ln)  # 空行はそのまま
                else:
                    # innerが空の場合でも、then_blockのstatementsを確認
                    for sub_stmt in stmt.then_block.statements:
                        if isinstance(sub_stmt, RawStmt):
                            txt = sub_stmt.text.strip()
                            if "startActivity" in txt and "new Intent" in txt:
                                activity_class = _extract_activity_class_from_intent(txt)
                                if activity_class:
                                    known_imports.add("Navigator")
                                    lines.append(
                                        f"  Navigator.push(context, "
                                        f"MaterialPageRoute(builder: (_) => const {activity_class}()));"
                                    )
                                    has_start_activity = True
                                    break
            lines.append("}")
            if stmt.else_block:
                lines.append("else {")
                inner = _java_ast_block_to_dart(stmt.else_block, known_imports)
                if inner.strip():
                    for ln in inner.splitlines():
                        if ln.strip():  # 空行はスキップ
                            lines.append("  " + ln)
                lines.append("}")
        elif isinstance(stmt, RawStmt):
            txt = stmt.text.strip()
            if txt:
                # finish(); などの単独のメソッド呼び出しをチェック
                # セミコロンあり/なし、括弧あり/なしの両方に対応
                if (txt == "finish()" or txt == "finish()" or txt == "finish" or 
                    txt.endswith(".finish()") or txt.endswith("finish()") or
                    re.match(r'^\s*finish\s*\(?\s*\)?\s*;?\s*$', txt)):
                    known_imports.add("Navigator")
                    lines.append("Navigator.maybePop(context);")
                # startActivity(new Intent(...)) を検出（if文の外）
                elif "startActivity" in txt and "new Intent" in txt and not txt.startswith("if"):
                    activity_class = _extract_activity_class_from_intent(txt)
                    if activity_class:
                        known_imports.add("Navigator")
                        lines.append(
                            f"Navigator.push(context, "
                            f"MaterialPageRoute(builder: (_) => const {activity_class}()));"
                        )
                    else:
                        lines.append(f"// TODO: port: {txt}")
                # if (isTaskRoot()) { startActivity(...); } のような構造を検出
                elif txt.startswith("if") and "isTaskRoot" in txt:
                    # if文を再解析（複数行にまたがる可能性を考慮）
                    if_match = re.search(r'if\s*\([^)]*\)\s*\{(.*?)\}', txt, re.DOTALL)
                    if if_match:
                        then_body = if_match.group(1).strip()
                        # then_body内のstartActivityを処理
                        activity_class = _extract_activity_class_from_intent(then_body)
                        if activity_class:
                            known_imports.add("Navigator")
                            lines.append("if (!Navigator.canPop(context)) {")
                            lines.append(
                                f"  Navigator.push(context, "
                                f"MaterialPageRoute(builder: (_) => const {activity_class}()));"
                            )
                            lines.append("}")
                        else:
                            # startActivityが見つからない場合でも、if文は変換
                            known_imports.add("Navigator")
                            lines.append("if (!Navigator.canPop(context)) {")
                            lines.append("  // TODO: port if body")
                            lines.append("}")
                    else:
                        # if文のパターンが見つからない場合
                        known_imports.add("Navigator")
                        lines.append("if (!Navigator.canPop(context)) {")
                        lines.append("  // TODO: port if body")
                        lines.append("}")
                # if(isTaskRoot()) { のような不完全なif文（複数行にまたがる）
                elif "if" in txt and "isTaskRoot" in txt and not txt.endswith("}"):
                    # 次の行に続く可能性があるので、if文の開始だけ処理
                    known_imports.add("Navigator")
                    lines.append("if (!Navigator.canPop(context)) {")
                # } だけの行（if文の終了）- 無視する（IfStmtで既に処理済み）
                elif txt == "}" or txt.strip() == "}" or txt == "}" or re.match(r'^\s*\}\s*$', txt):
                    # IfStmtで既に処理されているので、何もしない（コメントも出力しない）
                    pass
                # finish() のバリエーション（セミコロンなし、括弧なしなど）
                elif re.match(r'^\s*finish\s*\(?\s*\)?\s*;?\s*$', txt, re.IGNORECASE):
                    known_imports.add("Navigator")
                    lines.append("Navigator.maybePop(context);")
                else:
                    # その他のRawStmtはTODOコメントとして残す
                    # ただし、}だけの場合は既に処理済みなので出力しない
                    if not (txt == "}" or txt.strip() == "}"):
                        lines.append(f"// TODO: port: {txt}")

    return "\n".join(lines)


# ============================
# 4. id → handler 名のマッピング
# ============================

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
    """view_rules._find_handler が探索する候補キーすべてに登録."""
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


# ============================
# 5. テンプレートによる Dart 生成
# ============================

def _load_template() -> Optional[object]:
    """templates/screen.dart.j2 を Jinja2 でロード（{% raw %} を除去）."""
    if Environment is None:
        return None

    # このファイルの 1 つ上が java2flutter、そこに templates/ がある構成
    project_root = os.path.dirname(os.path.dirname(__file__))  # .../java2flutter
    template_dir = os.path.join(project_root, "templates")
    template_path = os.path.join(template_dir, "screen.dart.j2")

    if not os.path.exists(template_path):
        return None

    with open(template_path, "r", encoding="utf-8") as f:
        src = f.read()

    # 全体を raw/endraw で囲ってある場合に備えて削除
    src = src.replace("{% raw %}", "").replace("{% endraw %}", "")

    env = Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
    )
    # from_string を使うことで raw 除去済みソースをそのまま使う
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
        # 追加のインポートが必要な場合はここに追加
        # Navigator などは material.dart に含まれるので通常は不要
        pass

    if tmpl is None:
        # フォールバック: Stateless または Stateful 画面
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
"""
        else:
            return f"""{imports_str}

class {class_name} extends StatelessWidget {{
  const {class_name}({{super.key}});

  @override
  Widget build(BuildContext context) {{
    return Scaffold(
      body: {widget_tree},
    );
  }}

  // ===== Auto-Generated Handlers =====
  {handlers_code}
}}
"""
    return tmpl.render(**ctx)


# ============================
# 6. logic_map とハンドラコード生成
# ============================

def _build_logic_and_handlers(ir: UnifiedScreenIR, class_name: str):
    """統合 IR から logic_map と Dart のハンドラ関数定義コードを作る."""
    logic_map: Dict[str, str] = {}
    handler_funcs: List[str] = []
    imports: Set[str] = set()

    existing_ids: Set[str] = set()

    # 6-1) Java 側で見つかったハンドラ
    for vid, handler_ir in ir.handlers_by_id.items():
        base = vid.split("/")[-1]
        if not base:
            continue
        existing_ids.add(base)
        func_name = f"_on{base[0].upper()}{base[1:]}Pressed"
        _register_logic_keys(logic_map, base, func_name)

        body = _java_ast_block_to_dart(handler_ir.ast, imports)
        if not body.strip():
            body = "// TODO: implement handler"

        handler_funcs.append(
            f"void {func_name}(BuildContext context) {{\n"
            f"{_indent(body, 2)}\n"
            f"}}"
        )

    # 6-2) Button なのに Java 側でハンドラが見つからなかったもの → スタブを生成
    button_ids = _collect_button_ids_from_xml(ir.xml_ir)
    for base in button_ids:
        if not base or base in existing_ids:
            continue

        camel = _to_camel(base)
        func_name = (
            f"_on{camel[:1].upper()}{camel[1:]}Pressed"
            if camel
            else "_onUnknownPressed"
        )
        _register_logic_keys(logic_map, base, func_name)

        body = f"// TODO: no Java onClick handler found for '{base}'"
        handler_funcs.append(
            f"void {func_name}(BuildContext context) {{\n"
            f"{_indent(body, 2)}\n"
            f"}}"
        )

    handlers_code = "\n\n".join(handler_funcs) if handler_funcs else "// no handlers"
    return logic_map, handlers_code, imports


# ============================
# 7. 公開 API
# ============================

def generate_dart_code(
    xml_path: str,
    values_dir: Optional[str],
    java_root: Optional[str],
    output_path: str,
    class_name: str,
) -> None:
    """XML + Java ファイル群から Dart 画面コードを生成するエントリポイント."""

    # 1) メイン XML
    xml_ir, resolver = parse_layout_xml(xml_path, values_dir)

    # 2) 同じ layout ディレクトリ内の他 XML から背景情報を収集
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
                # 壊れた XML があっても全体を止めない
                continue
            _collect_backgrounds_from_ir(sub_ir, bg_map, is_root=True)

    applied_backgrounds = _merge_backgrounds_into_main(xml_ir, bg_map)

    # 3) Java → ClickHandlerIR(AST ベース)
    handlers_by_id: Dict[str, ClickHandlerIR] = {}
    if java_root and os.path.exists(java_root):
        xml_ids = _collect_ids(xml_ir)
        handlers_by_id = extract_click_handlers(java_root, xml_ids)
    
    # 3.5) Fragment検出
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

    # 4) 統合 IR → logic_map / handlers_code
    logic_map, handlers_code, known_imports = _build_logic_and_handlers(unified, class_name)

    # 5) ルート要素の背景色/背景画像を取得（translate_nodeの前に処理）
    root_bg_color = None
    root_bg_image = None
    root_attrs = unified.xml_ir.get("attrs") or {}
    root_bg_raw = root_attrs.get("background")
    if root_bg_raw and resolver:
        # drawableとして解決を試みる
        drawable_path = resolver.resolve_drawable_path(root_bg_raw)
        if drawable_path:
            # 背景画像の場合
            from ..utils import get_asset_path_from_drawable
            root_bg_image = get_asset_path_from_drawable(drawable_path)
            # 背景画像属性を一時的に削除（translate_nodeの後で復元する必要はない）
            unified.xml_ir["attrs"] = {k: v for k, v in root_attrs.items() if k != "background"}
        else:
            # 色として解決を試みる
            resolved = resolver.resolve(root_bg_raw) or root_bg_raw
            root_bg_color = ResourceResolver.android_color_to_flutter(resolved)
            # ルート要素の背景色をScaffoldに設定するため、Containerでラップしないように背景色属性を削除
            if root_bg_color:
                # 背景色属性を一時的に削除（translate_nodeの後で復元する必要はない）
                unified.xml_ir["attrs"] = {k: v for k, v in root_attrs.items() if k != "background"}

    # 6) UI ツリーを Dart の Widget 式に変換
    widget_tree = translate_node(unified.xml_ir, unified.resolver, logic_map=logic_map, fragments_by_id=unified.fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)

    # 6.5) Stackが含まれているかチェック（背景画像がある場合）
    has_stack_background = "Stack(children:" in widget_tree or "Stack(children: [" in widget_tree

    # 7) TextField の検出と StatefulWidget の判定
    has_text_field = _has_text_field(unified.xml_ir)
    controllers: List[str] = []
    if has_text_field:
        # TextField がある場合は StatefulWidget が必要
        # 必要に応じて controllers を追加
        pass

    # 8) 必要なインポートを収集

    # 8) 必要なインポートを収集
    imports_list = list(known_imports)
    if "Navigator" in imports_list:
        # Navigator は material.dart に含まれるので追加のインポートは不要
        pass

    dart_src = _render_screen_with_template(
        class_name=class_name,
        widget_tree=widget_tree,
        handlers_code=handlers_code,
        controllers=controllers,
        options={
            "is_stateful": has_text_field,  # TextField がある場合は StatefulWidget
            "use_scrollview": True,
            "use_safearea": False,
            "add_appbar": False,
            "use_scaffold": True,
            "keyboard_dismiss": True,
            "page_padding": 0.0,
            "stretch": True,
            "imports": imports_list,
            "scaffold_bg_color": root_bg_color,  # ルート要素の背景色
            "scaffold_bg_image": root_bg_image,  # ルート要素の背景画像
            "has_stack_background": has_stack_background,  # Stackが含まれているか
        },
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(dart_src)

    print(f"[INFO] Generated Dart: {output_path}")
