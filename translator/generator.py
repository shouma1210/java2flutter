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


def _collect_onclick_methods_from_xml(ir: dict) -> Dict[str, str]:
    """XML IR から android:onClick 属性を収集して {view_id: method_name} の辞書を返す."""
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
    """XML IR に EditText、Checkbox、Switch が含まれているかチェック（StatefulWidgetが必要な要素）."""
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
    """XML IR から TextField/EditText の ID を収集してコントローラー名を生成."""
    controllers: List[str] = []
    
    def _walk(node: dict) -> None:
        t = (node.get("type") or "").lower()
        attrs = node.get("attrs") or {}
        raw_id = attrs.get("id")
        
        if (t == "edittext" or t.endswith("edittext")) and raw_id:
            # @+id/editTitle -> editTitle -> title -> _titleController
            field_id = raw_id.split("/")[-1]
            # editTitle, editContent などのパターンを処理
            controller_base = field_id.replace("edit", "").replace("Edit", "")
            if controller_base:
                controller_name = f"_{controller_base[0].lower()}{controller_base[1:]}Controller"
                if controller_name not in controllers:
                    controllers.append(controller_name)
        
        for ch in node.get("children") or []:
            _walk(ch)
    
    _walk(ir)
    return controllers


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
            
            # 変数のインクリメント/デクリメントを検出
            if re.match(r'^\w+\+\+$', target) or re.match(r'^\w+--$', target):
                var_name = target.rstrip('+-')
                # refreshKeys()などの未定義メソッドは無視
                if var_name == "refreshKeys":
                    pass
                else:
                    op = '++' if '++' in target else '--'
                    lines.append(f"setState(() {{ {var_name}{op}; }});")
                continue
            
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
                        # 変換できない場合は何も出力しない
                        pass
                else:
                    # 変換できない場合は何も出力しない
                    pass
            elif "startActivity" in target:
                # startActivity(new Intent(...)) → Navigator.push
                activity_class = _extract_activity_class_from_intent(args)
                if activity_class:
                    known_imports.add("Navigator")
                    lines.append(
                        f"Navigator.push(context, "
                        f"MaterialPageRoute(builder: (_) => {activity_class}()));"
                    )
                else:
                    # Intent解析に失敗した場合は何も出力しない
                    pass
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
            # メソッド呼び出しの変換（より多くのパターンに対応）
            elif re.match(r'^\w+$', target) and not args:
                # refreshKeys()などの未定義メソッドは無視
                if target == "refreshKeys":
                    pass
                else:
                    # 引数なしのメソッド呼び出し（例: tampilkanSoal()）
                    # これはカスタムメソッドの可能性が高いので、setStateでラップして呼び出す
                    lines.append(f"setState(() {{ _{target}(); }});")
            elif re.match(r'^\w+$', target) and args:
                # 引数ありのメソッド呼び出し（例: periksaJawaban("A")）
                # 引数を適切に処理
                clean_args = args.rstrip(';').strip()
                # 文字列リテラルを適切に処理
                if clean_args.startswith('"') and clean_args.endswith('"'):
                    clean_args = f"'{clean_args[1:-1]}'"
                lines.append(f"setState(() {{ _{target}({clean_args}); }});")
            else:
                # 変換できないメソッド呼び出しは無視
                pass
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
                                f"MaterialPageRoute(builder: (_) => {activity_class}()));"
                            )
                            has_start_activity = True
                            break
            
            # startActivityが見つからなかった場合、再帰的に処理
            if not has_start_activity:
                inner = _java_ast_block_to_dart(stmt.then_block, known_imports)
                if inner.strip():
                    # 各行をインデントして追加（空行も保持）
                    # return文が含まれている場合は、その後のコードを処理しない
                    has_return = False
                    for ln in inner.splitlines():
                        if ln.strip() == "return;":
                            has_return = True
                            lines.append("  " + ln)
                            break
                        elif ln.strip():
                            lines.append("  " + ln)
                        else:
                            lines.append(ln)  # 空行はそのまま
                    # return文があった場合は、その後のコードを処理しない
                    if has_return:
                        lines.append("}")
                        continue
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
                                        f"MaterialPageRoute(builder: (_) => {activity_class}()));"
                                    )
                                    has_start_activity = True
                                    break
                            # return文をチェック
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
                        if ln.strip():  # 空行はスキップ
                            lines.append("  " + ln)
                lines.append("}")
        elif isinstance(stmt, RawStmt):
            txt = stmt.text.strip()
            if txt:
                # } だけの行（if文の終了）- 無視する（IfStmtで既に処理済み）
                if txt == "}" or re.match(r'^\s*\}\s*$', txt):
                    # IfStmtで既に処理されているので、何もしない（コメントも出力しない）
                    pass
                # if文がRawStmtとして処理されている場合、IfStmtとして再解析を試みる
                elif txt.startswith("if") and "{" in txt:
                    # if文を再解析
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
                        
                        # isTaskRoot() などのメソッド呼び出しを適切に変換
                        if "isTaskRoot" in cond:
                            known_imports.add("Navigator")
                            cond = "!Navigator.canPop(context)"
                        
                        lines.append(f"if ({cond}) {{")
                        # then_blockを再帰的に処理
                        from parser.java_parser import Block, _append_simple_statements
                        then_block = Block()
                        _append_simple_statements(then_block, then_body)
                        inner = _java_ast_block_to_dart(then_block, known_imports)
                        if inner.strip():
                            # 各行をインデントして追加
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
                # Toast.makeText(...).show() を検出（MethodCallとして解析されなかった場合のフォールバック）
                elif "Toast.makeText" in txt:
                    known_imports.add("ScaffoldMessenger")
                    # Toast.makeText(this, msg, Toast.LENGTH_LONG).show() からメッセージを抽出
                    msg_match = re.search(r'["\']([^"\']+)["\']', txt)
                    msg = msg_match.group(1) if msg_match else "TODO: port Toast"
                    lines.append(
                        f"ScaffoldMessenger.of(context).showSnackBar("
                        f"SnackBar(content: Text('{msg}')));"
                    )
                # long型の変数宣言は無視
                elif re.search(r'^\s*long\s+\w+\s*=', txt):
                    # long型は無視
                    pass
                # String型の変数宣言を検出（getText()などが含まれる場合）
                elif re.match(r'^\s*String\s+\w+\s*=', txt) and ('getText()' in txt or '.getText()' in txt):
                    # String n = name.getText().toString().trim() のようなパターン
                    # RadioButtonのgetText()を検出
                    if "selectedMood" in txt or "RadioButton" in txt:
                        # String mood = (selectedMood != null) ? selectedMood.getText().toString() : "😊";
                        var_match = re.match(r'^\s*String\s+(\w+)\s*=', txt)
                        if var_match:
                            result_var = var_match.group(1)
                            lines.append(f"String {result_var} = _selectedMood; // Use state variable instead of RadioButton.getText()")
                        else:
                            lines.append(f"String mood = _selectedMood; // Use state variable instead of RadioButton.getText()")
                    else:
                        # EditText/TextFieldの変数名を抽出
                        var_match = re.search(r'(\w+)\.getText\(\)', txt)
                        if var_match:
                            edit_text_var = var_match.group(1)
                            result_var_match = re.match(r'^\s*String\s+(\w+)\s*=', txt)
                            if result_var_match:
                                result_var = result_var_match.group(1)
                                # FlutterではTextEditingControllerを使用
                                # 変数名からコントローラー名を推測（例: editTitle -> _titleController）
                                # editTitle -> title -> _titleController
                                controller_base = edit_text_var.replace('edit', '').replace('Edit', '')
                                controller_name = f"_{controller_base[0].lower()}{controller_base[1:]}Controller"
                                lines.append(f"String {result_var} = {controller_name}.text;")
                            else:
                                # TextField value extractionは無視
                                pass
                        else:
                            # TextField value extractionは無視
                            pass
                # RadioGroupの選択状態取得は無視（UI部分で処理済み）
                elif "getCheckedRadioButtonId" in txt:
                    # RadioGroupは無視
                    pass
                # RadioButtonの取得とgetText()は無視（UI部分で処理済み）
                elif "findViewById" in txt and "RadioButton" in txt:
                    # RadioButtonは無視
                    pass
                # RadioButtonのgetText()からmoodを取得する処理
                elif re.search(r'selectedMood.*getText\(\)', txt) or re.search(r'selectedMood.*\.getText\(\)', txt):
                    # String mood = (selectedMood != null) ? selectedMood.getText().toString() : "😊";
                    # 変数名を抽出
                    var_match = re.search(r'String\s+(\w+)\s*=', txt)
                    if var_match:
                        result_var = var_match.group(1)
                        lines.append(f"String {result_var} = _selectedMood; // Use state variable instead of RadioButton.getText()")
                    else:
                        lines.append(f"String mood = _selectedMood; // Use state variable instead of RadioButton.getText()")
                # _selectedmoodControllerのような誤ったコントローラー名を修正（先に処理）
                elif re.search(r'_selected[mM]oodController', txt):
                    # String mood = _selectedMoodController.text; のようなパターンを修正
                    var_match = re.search(r'String\s+(\w+)\s*=\s*_selected[mM]oodController\.text', txt)
                    if var_match:
                        result_var = var_match.group(1)
                        lines.append(f"String {result_var} = _selectedMood; // Use state variable instead of controller")
                    else:
                        lines.append(f"String mood = _selectedMood; // Use state variable instead of controller")
                    continue
                # setContentView, R.layoutなどのAndroid固有コードは無視
                elif "setContentView" in txt or "R.layout" in txt:
                    # setContentViewは無視
                    pass
                # android.content.IntentなどのAndroid固有コードは無視（startActivityで処理済み）
                elif "android.content.Intent" in txt or ("new Intent" in txt and ("android.content" in txt or "Intent" in txt)):
                    # IntentはstartActivityで処理済みなので無視
                    pass
                # AppDatabaseなどのRoom固有コードは無視（データベース変換は行わない）
                elif "AppDatabase" in txt or "Room" in txt or "journalDao" in txt or "getAllJournals" in txt or "searchJournals" in txt or "insert" in txt and "Journal" in txt or "deleteById" in txt:
                    # データベース関連は無視
                    pass
                # onCreate, onResumeなどのライフサイクルメソッド内のコードは無視
                elif re.match(r'^\s*super\.(onCreate|onResume|onPause|onDestroy)', txt):
                    # ライフサイクルメソッドは無視
                    pass
                # s.toString()のような未定義変数の使用を検出
                elif re.search(r'\bs\.toString\(\)', txt) or re.search(r'\bs\s*\.\s*toString\(\)', txt):
                    # TextWatcherのonTextChangedなどで使用される変数sを検出
                    # _performSearch(s.toString()) のようなパターンを修正
                    if "_performSearch" in txt:
                        # setState(() { _performSearch(s.toString()); }); を修正
                        dart_txt = txt.replace("s.toString()", "_searchController.text")
                        dart_txt = dart_txt.replace("s.toString()", "_searchController.text")  # 念のため2回
                        # TextWatcherは無視（UI部分で処理済み）
                        pass
                    else:
                        # TextWatcherは無視
                        pass
                # Integer.parseIntをint.parseに変換
                elif "Integer.parseInt" in txt:
                    dart_txt = txt.replace("Integer.parseInt", "int.parse")
                    # セミコロンがない場合は追加
                    if not dart_txt.endswith(';'):
                        dart_txt += ';'
                    lines.append(dart_txt)
                # Calendar.getInstance()などのJavaクラスは無視
                elif re.match(r'^\s*Calendar\s+\w+\s*=\s*Calendar\.getInstance', txt):
                    # Calendarは無視
                    pass
                # java.util.Calendar.getInstance()などのJavaクラスは無視
                elif re.search(r'^\s*java\.util\.Calendar\s+\w+\s*=\s*java\.util\.Calendar\.getInstance', txt):
                    # Calendarは無視
                    pass
                # finish(); などの単独のメソッド呼び出しをチェック
                # セミコロンあり/なし、括弧あり/なしの両方に対応
                elif (txt == "finish()" or txt == "finish()" or txt == "finish" or 
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
                            f"MaterialPageRoute(builder: (_) => {activity_class}()));"
                        )
                    else:
                        # 変換できない場合は無視
                        pass
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
                                f"MaterialPageRoute(builder: (_) => {activity_class}()));"
                            )
                            lines.append("}")
                        else:
                            # startActivityが見つからない場合は無視
                            pass
                    else:
                        # if文のパターンが見つからない場合
                        known_imports.add("Navigator")
                        lines.append("if (!Navigator.canPop(context)) {")
                        # if文の本体は無視
                        lines.append("}")
                # if(isTaskRoot()) { のような不完全なif文（複数行にまたがる）
                elif "if" in txt and "isTaskRoot" in txt and not txt.endswith("}"):
                    # 次の行に続く可能性があるので、if文の開始だけ処理
                    known_imports.add("Navigator")
                    lines.append("if (!Navigator.canPop(context)) {")
                # finish() のバリエーション（セミコロンなし、括弧なしなど）
                elif re.match(r'^\s*finish\s*\(?\s*\)?\s*;?\s*$', txt, re.IGNORECASE):
                    known_imports.add("Navigator")
                    lines.append("Navigator.maybePop(context);")
                # 変数のインクリメント/デクリメントを検出
                elif re.match(r'^\s*\w+\s*\+\+\s*;?\s*$', txt) or re.match(r'^\s*\w+\s*--\s*;?\s*$', txt):
                    var_match = re.match(r'^\s*(\w+)\s*(\+\+|--)\s*;?\s*$', txt)
                    if var_match:
                        var_name = var_match.group(1)
                        # refreshKeys()などの未定義メソッドは無視
                        if var_name == "refreshKeys":
                            pass
                        else:
                            op = var_match.group(2)
                            lines.append(f"setState(() {{ {var_name}{op}; }});")
                    continue
                # メソッド呼び出しの変換（RawStmtとして処理される場合）
                elif re.match(r'^\s*\w+\s*\([^)]*\)\s*;?\s*$', txt):
                    # メソッド呼び出し（例: tampilkanSoal(); periksaJawaban("A");）
                    method_match = re.match(r'^\s*(\w+)\s*\(([^)]*)\)\s*;?\s*$', txt)
                    if method_match:
                        method_name = method_match.group(1)
                        # refreshKeys()などの未定義メソッドは無視
                        if method_name == "refreshKeys":
                            pass
                        else:
                            method_args = method_match.group(2).strip()
                            if not method_args:
                                lines.append(f"setState(() {{ _{method_name}(); }});")
                            else:
                                # 引数を適切に処理
                                clean_args = method_args
                                if clean_args.startswith('"') and clean_args.endswith('"'):
                                    clean_args = f"'{clean_args[1:-1]}'"
                                lines.append(f"setState(() {{ _{method_name}({clean_args}); }});")
                    continue
                # 変数のインクリメント/デクリメント（RawStmtとして処理される場合）
                elif re.search(r'\+\+|\-\-', txt) and not re.search(r'\+\=|-\=', txt):
                    # currentIndex++ のような単独のインクリメント
                    var_match = re.search(r'(\w+)\s*(\+\+|--)', txt)
                    if var_match:
                        var_name = var_match.group(1)
                        # refreshKeys()などの未定義メソッドは無視
                        if var_name == "refreshKeys":
                            pass
                        else:
                            op = var_match.group(2)
                            lines.append(f"setState(() {{ {var_name}{op}; }});")
                    continue
                # 変数の代入（インクリメント/デクリメントを含む）
                elif re.search(r'\+\+|\-\-', txt) and '=' in txt:
                    # currentIndex++ のような単独のインクリメントは上で処理済み
                    # ここでは +=, -= などの複合代入を処理
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
                # AlertDialog.Builderの変換
                elif "AlertDialog.Builder" in txt or "new AlertDialog.Builder" in txt:
                    known_imports.add("showDialog")
                    # AlertDialog.Builder(this).setTitle(...).setMessage(...).show() を解析
                    # 簡易版：基本的な構造を変換
                    title_match = re.search(r'setTitle\s*\(\s*["\']([^"\']+)["\']', txt)
                    # setMessage("Do you want to delete the key \"" + alias + "\" from the keystore?") のような文字列連結に対応
                    # \"を含む文字列もマッチするように修正
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
                    # positive buttonの処理を追加（簡易版）
                    if "finish()" in txt:
                        lines.append(f"          Navigator.of(ctx).pop();")
                        lines.append(f"          Navigator.maybePop(context);")
                    else:
                        lines.append(f"          Navigator.of(ctx).pop();")
                        # positive button actionは無視
                    lines.append(f"        }},")
                    lines.append(f"        child: Text('{escaped_positive}'),")
                    lines.append(f"      ),")
                    lines.append("    ],")
                    lines.append("  ),")
                    lines.append(");")
                    continue
                # return文を検出
                elif txt.strip() == "return" or re.match(r'^\s*return\s*;?\s*$', txt):
                    lines.append("return;")
                    # return文の後は処理を続けない（関数が終了するため）
                    # ただし、}の後に続くコードがある場合は処理しない
                    continue
                # }の後に続くコードがある場合（例: "}\nlong id = ..."）
                elif txt.startswith("}"):
                    # return文の後で}が来る場合、その後のコードは関数の外に出るため処理しない
                    # }を除去して残りを確認
                    remaining = txt[1:].strip()
                    if remaining:
                        # return文の後で}が来る場合、その後のコードは無視
                        pass
                    continue
                else:
                    # その他のRawStmtはTODOコメントとして残す
                    # ただし、}だけの場合は既に処理済みなので出力しない
                    if not (txt == "}" or txt.strip() == "}" or re.match(r'^\s*\}\s*$', txt)):
                        # }の後に続くコードがある場合（例: "}\nlong id = ..."）
                        if txt.startswith("}"):
                            # }を除去して残りを処理
                            remaining = txt[1:].strip()
                            if remaining:
                                # }の後のコードは無視
                                pass
                        else:
                            # refreshKeys()などの未定義メソッド呼び出しを無視
                            if re.search(r'refreshKeys\s*\(\)', txt) or re.search(r'_refreshKeys\s*\(\)', txt):
                                # refreshKeys()は無視
                                pass
                            # whileループが不正に変換された場合を無視
                            elif re.search(r'setState\s*\(\s*\(\s*\)\s*\{\s*_while', txt) or re.search(r'_while\s*\(', txt) or re.search(r'cipherInputStream', txt) or re.search(r'values\.add', txt):
                                # setState(() { _while(...) }) のような不正な構文を無視
                                # cipherInputStreamなどのAndroid固有APIも無視
                                # values.addなどのJava固有APIも無視
                                pass
                            # 変換できないコードは無視
                            else:
                                pass
                    else:
                        # 変換できない場合は無視
                        pass

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

def _build_logic_and_handlers(ir: UnifiedScreenIR, class_name: str, java_methods: Dict[str, str] = None):
    """統合 IR から logic_map と Dart のハンドラ関数定義コードを作る."""
    if java_methods is None:
        java_methods = {}
    logic_map: Dict[str, str] = {}
    handler_funcs: List[str] = []
    method_funcs: List[str] = []
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
        if not body.strip() or body.strip().startswith("// TODO"):
            # 変換できないハンドラはスキップ
            continue

        handler_funcs.append(
            f"void {func_name}(BuildContext context) {{\n"
            f"{_indent(body, 2)}\n"
            f"}}"
        )

    # 6-2) Button なのに Java 側でハンドラが見つからなかったもの → android:onClick属性をチェック
    button_ids = _collect_button_ids_from_xml(ir.xml_ir)
    onclick_map = _collect_onclick_methods_from_xml(ir.xml_ir)
    
    for base in button_ids:
        if not base or base in existing_ids:
            continue

        # android:onClick属性がある場合、そのメソッド名からハンドラ名を生成
        onclick_method = onclick_map.get(base)
        if onclick_method:
            # android:onClick属性のメソッド名からハンドラ名を生成
            camel = onclick_method
            if camel.startswith("on"):
                camel = camel[2:]  # "on"を削除
            camel = _to_camel(camel)
            func_name = (
                f"_on{camel[:1].upper()}{camel[1:]}Pressed"
                if camel
                else "_onUnknownPressed"
            )
        else:
            # android:onClick属性がない場合、ボタンIDからハンドラ名を生成
            camel = _to_camel(base)
            func_name = (
                f"_on{camel[:1].upper()}{camel[1:]}Pressed"
                if camel
                else "_onUnknownPressed"
            )
        _register_logic_keys(logic_map, base, func_name)

        # android:onClick属性がある場合、そのメソッド本体を取得
        if onclick_method and onclick_method in java_methods:
            # android:onClick属性で指定されたメソッドが存在する場合、その本体を変換
            method_body = java_methods[onclick_method]
            # データベース関連やRecyclerView関連はスキップ
            if any(keyword in method_body for keyword in ["AppDatabase", "Room", "journalDao", "getAllJournals", "searchJournals", "deleteById", "RecyclerView", "setAdapter", "Adapter"]):
                body = "// Button handler"
            else:
                # メソッド本体をASTに変換
                from parser.java_parser import _parse_block_to_ast
                method_ast = _parse_block_to_ast(method_body)
                body = _java_ast_block_to_dart(method_ast, imports)
                # 不正な構文（setState(() { _while(...) })など）を含む場合はハンドラを生成しない
                if "setState(() { _while" in body or "cipherInputStream" in body or "values.add" in body:
                    continue
                # TODOコメントのみの場合はハンドラを生成しない
                elif not body.strip() or body.strip().startswith("// TODO"):
                    continue
        else:
            # android:onClick属性がない、またはメソッドが見つからない場合はハンドラを生成しない
            continue
        
        handler_funcs.append(
            f"void {func_name}(BuildContext context) {{\n"
            f"{_indent(body, 2)}\n"
            f"}}"
        )

    # 6-3) Javaメソッド定義をFlutterメソッドとして変換
    # XMLファイルに関連するボタンやハンドラーがある場合のみ、Javaメソッドを追加
    # （XMLファイルにボタンがない場合、ハンドラーメソッドは不要）
    has_buttons_or_handlers = len(handler_funcs) > 0 or len(button_ids) > 0
    if has_buttons_or_handlers:
        for method_name, method_body in java_methods.items():
            # onCreateなどのライフサイクルメソッドはスキップ（FlutterではinitStateを使用）
            if method_name in ["onCreate", "onResume", "onPause", "onDestroy", "onStart", "onStop"]:
                continue
            # データベース関連メソッドはスキップ
            if any(keyword in method_body for keyword in ["AppDatabase", "Room", "journalDao", "getAllJournals", "searchJournals", "deleteById"]):
                continue
            # RecyclerView関連メソッドはスキップ
            if any(keyword in method_body for keyword in ["RecyclerView", "setAdapter", "Adapter", "loadJournals", "performSearch"]):
                continue
        # メソッド本体をASTに変換
        from parser.java_parser import _parse_block_to_ast
        method_ast = _parse_block_to_ast(method_body)
        method_dart_body = _java_ast_block_to_dart(method_ast, imports)
        # 不正な構文（setState(() { _while(...) })など）を含むメソッドは無視
        if "setState(() { _while" in method_dart_body or "cipherInputStream" in method_dart_body or "values.add" in method_dart_body:
            pass
        # TODOコメントのみのメソッドはスキップ
        elif method_dart_body.strip() and not method_dart_body.strip().startswith("// TODO"):
            method_funcs.append(
                f"void _{method_name}() {{\n"
                f"{_indent(method_dart_body, 2)}\n"
                f"}}"
            )
    
    # handlers_codeにメソッド定義も追加
    all_funcs = handler_funcs + method_funcs
    handlers_code = "\n\n".join(all_funcs) if all_funcs else ""
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
    java_methods: Dict[str, str] = {}
    if java_root and os.path.exists(java_root):
        xml_ids = _collect_ids(xml_ir)
        handlers_by_id = extract_click_handlers(java_root, xml_ids)
        java_methods = extract_methods(java_root)
    
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
    logic_map, handlers_code, known_imports = _build_logic_and_handlers(unified, class_name, java_methods)

    # 5) ルート要素の背景色/背景画像を取得（translate_nodeの前に処理）
    root_bg_color = None
    root_bg_image = None
    root_bg_decoration = None  # XML drawableのBoxDecorationコード
    root_attrs = unified.xml_ir.get("attrs") or {}
    root_bg_raw = root_attrs.get("background")
    if root_bg_raw and resolver:
        # drawableとして解決を試みる
        drawable_path = resolver.resolve_drawable_path(root_bg_raw)
        if drawable_path:
            # XML形式のdrawableリソースか画像ファイルかを判定
            if drawable_path.lower().endswith(".xml"):
                # XML形式のdrawableリソース（shape drawableなど）の場合
                from utils import _parse_shape_drawable_to_boxdecoration
                root_bg_decoration = _parse_shape_drawable_to_boxdecoration(drawable_path, resolver)
                # XML drawableの場合は背景画像属性を削除（BoxDecorationとして処理）
                if root_bg_decoration:
                    unified.xml_ir["attrs"] = {k: v for k, v in root_attrs.items() if k != "background"}
            else:
                # 背景画像の場合
                from utils import get_asset_path_from_drawable
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
    
    # XMLに明示的なbackground属性がない場合、デフォルトの背景色（白）を設定
    # AndroidのLightテーマは通常白い背景を意味するため
    if not root_bg_color and not root_bg_image and not root_bg_decoration:
        root_bg_color = "0xFFFFFFFF"  # 白（#FFFFFF）

    # 6) UI ツリーを Dart の Widget 式に変換
    widget_tree = translate_node(unified.xml_ir, unified.resolver, logic_map=logic_map, fragments_by_id=unified.fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)

    # 6.5) Stackが含まれているかチェック（背景画像がある場合）
    has_stack_background = "Stack(children:" in widget_tree
    has_expanded = "Expanded(" in widget_tree
    # ListViewが含まれている場合、SingleChildScrollViewでラップしない
    has_listview = "ListView" in widget_tree

    # 7) TextField の検出と StatefulWidget の判定
    has_text_field = _has_text_field(unified.xml_ir)
    controllers: List[str] = []
    if has_text_field:
        # TextField がある場合は StatefulWidget が必要
        # XMLからTextFieldのIDを収集してコントローラー名を生成
        controllers = _collect_text_field_ids(unified.xml_ir)

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
            "use_scrollview": not has_listview,  # ListViewが含まれている場合、SingleChildScrollViewでラップしない
            "use_safearea": False,
            "add_appbar": False,
            "use_scaffold": True,
            "keyboard_dismiss": True,
            "page_padding": 0.0,
            "stretch": True,
            "imports": imports_list,
            "scaffold_bg_color": root_bg_color,  # ルート要素の背景色
            "scaffold_bg_image": root_bg_image,  # ルート要素の背景画像
            "scaffold_bg_decoration": root_bg_decoration,  # ルート要素の背景BoxDecoration（XML drawable）
            "has_stack_background": has_stack_background, "has_expanded": has_expanded  # StackまたはExpandedが含まれているか
        },
    )

    # 生成されたコードから不要な死コードを削除
    dart_src = _cleanup_dead_code(dart_src)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(dart_src)

    print(f"[INFO] Generated Dart: {output_path}")


def _cleanup_dead_code(dart_src: str) -> str:
    """生成されたDartコードから不要な死コードを削除"""
    import re
    
    lines = dart_src.split('\n')
    cleaned_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # `if (0.0 > 0.0) {` のような常にfalseの条件を検出
        if re.search(r'if\s*\(\s*0\.0\s*>\s*0\.0\s*\)', line):
            # 対応する閉じ括弧を見つける
            brace_depth = line.count('{') - line.count('}')
            j = i + 1
            while j < len(lines) and brace_depth > 0:
                brace_depth += lines[j].count('{') - lines[j].count('}')
                j += 1
            # ifブロック全体をスキップ
            i = j
            continue
        
        # `if (true) {` のような常にtrueの条件を検出
        if re.search(r'if\s*\(\s*true\s*\)\s*\{', line):
            # 対応する閉じ括弧を見つける
            brace_depth = line.count('{') - line.count('}')
            j = i + 1
            while j < len(lines) and brace_depth > 0:
                brace_depth += lines[j].count('{') - lines[j].count('}')
                j += 1
            # if文を削除して、中身だけを残す（インデントを調整）
            inner_lines = lines[i+1:j-1]
            for inner_line in inner_lines:
                # インデントを2スペース減らす（if文のインデント分）
                cleaned_lines.append(re.sub(r'^(\s{2,})', lambda m: m.group(1)[:-2] if len(m.group(1)) >= 2 else '', inner_line))
            i = j
            continue
        
        # `if (false) {` のような常にfalseの条件を検出
        if re.search(r'if\s*\(\s*false\s*\)\s*\{', line):
            # 対応する閉じ括弧を見つける
            brace_depth = line.count('{') - line.count('}')
            j = i + 1
            while j < len(lines) and brace_depth > 0:
                brace_depth += lines[j].count('{') - lines[j].count('}')
                j += 1
            # ifブロック全体をスキップ
            i = j
            continue
        
        # 空の`dispose()`メソッドを削除
        # @override\nvoid dispose() {\n  super.dispose();\n}
        if re.match(r'\s*@override\s*', line):
            # 次の数行を確認
            if i + 3 < len(lines):
                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                dispose_line = lines[i + 2] if i + 2 < len(lines) else ""
                close_line = lines[i + 3] if i + 3 < len(lines) else ""
                if (re.match(r'\s*void\s+dispose\s*\(\s*\)\s*\{', next_line) and
                    re.match(r'\s*super\.dispose\s*\(\s*\)\s*;', dispose_line) and
                    re.match(r'\s*\}\s*', close_line)):
                    # 空のdispose()メソッドをスキップ
                    i += 4
                    continue
        
        # 空の`dispose()`メソッド（@overrideなしの場合）を削除
        if re.match(r'\s*void\s+dispose\s*\(\s*\)\s*\{', line):
            # 次の数行を確認
            if i + 2 < len(lines):
                dispose_line = lines[i + 1] if i + 1 < len(lines) else ""
                close_line = lines[i + 2] if i + 2 < len(lines) else ""
                if (re.match(r'\s*super\.dispose\s*\(\s*\)\s*;', dispose_line) and
                    re.match(r'\s*\}\s*', close_line)):
                    # 空のdispose()メソッドをスキップ
                    i += 3
                    continue
        
        cleaned_lines.append(line)
        i += 1
    
    dart_src = '\n'.join(cleaned_lines)
    
    # `keyboardType: TextInputType.text` を削除（デフォルト値なので不要）
    # カンマの前後を考慮して削除
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
    # 最後のパラメータとして残っている場合
    dart_src = re.sub(
        r',\s*keyboardType:\s*TextInputType\.text\s*\)',
        ')',
        dart_src
    )
    
    # `Padding(padding: EdgeInsets.all(0.0), child: ...)` を `child` の内容に置き換え
    dart_src = re.sub(
        r'Padding\s*\(\s*padding:\s*EdgeInsets\.(?:all|fromLTRB)\(0\.0(?:\s*,\s*0\.0)*\)\s*,\s*child:\s*([^)]+)\s*\)',
        r'\1',
        dart_src,
        flags=re.MULTILINE | re.DOTALL
    )
    
    # 連続する空行を1つにまとめる
    dart_src = re.sub(r'\n\s*\n\s*\n+', '\n\n', dart_src)
    
    return dart_src
