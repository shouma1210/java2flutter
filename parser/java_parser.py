# android2flutter/translator/java_parser.py
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ============================
# 1. ミニ AST 定義
# ============================

class AstNode:
    """Java ロジック用の簡易 AST ノード基底クラス"""
    pass


@dataclass
class MethodCall(AstNode):
    target: Optional[str]   # 例: "startActivity", "Toast.makeText"
    args: str               # 引数文字列（今回はそのまま保持）


@dataclass
class IfStmt(AstNode):
    condition: str
    then_block: "Block"
    else_block: Optional["Block"] = None


@dataclass
class RawStmt(AstNode):
    """うまくパースできなかった行をそのまま持つ"""
    text: str


@dataclass
class Block(AstNode):
    statements: List[AstNode] = field(default_factory=list)


# ============================
# 2. ハンドラ IR
# ============================

@dataclass
class ClickHandlerIR:
    """1つの onClick / setOnClickListener に対応する IR"""
    name: str              # Dart側の関数名（後で generator 側で付け直し可）
    view_ids: List[str]    # このハンドラが対応する XML id（複数可）
    java_src: str          # 元の Java コード断片
    ast: Block             # 上のミニ AST


# ============================
# 3. Java → AST 変換（簡易）
# ============================

def _append_simple_statements(block: Block, src: str) -> None:
    """
    セミコロン区切りでステートメントを分割し、
    MethodCall / RawStmt に振り分けて Block に追加。
    """
    # コメント削除
    src = re.sub(r'//.*', '', src)
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.DOTALL)

    # セミコロンで分割（ただし、文字列リテラル内のセミコロンは除外）
    # 簡易版: セミコロンで分割
    parts = []
    current = ""
    for char in src:
        if char == ';':
            if current.strip():
                parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())

    for stmt in parts:
        if not stmt:
            continue

        # if文はMethodCallとして解析しない
        if stmt.strip().startswith("if"):
            block.statements.append(RawStmt(text=stmt))
            continue

        # メソッド呼び出しっぽいもの（複数行にまたがる可能性を考慮）
        # startActivity(new Intent(...)) のような複雑な呼び出しも検出
        m = re.match(r'(?:(?P<recv>[\w\.]+)\s*\.)?(?P<name>\w+)\s*\((?P<args>.*)\)\s*$', stmt, re.DOTALL)
        if m:
            recv = m.group("recv")
            name = m.group("name")
            args = m.group("args") or ""
            target = f"{recv}.{name}" if recv else name
            block.statements.append(MethodCall(target=target, args=args.strip()))
        else:
            block.statements.append(RawStmt(text=stmt))


def _parse_block_to_ast(block_src: str) -> Block:
    """
    Java のブロック文字列をかなり大雑把に AST(Block) に変換する。
    - if (...) { ... } else { ... } を IfStmt に
    - foo(...); / obj.foo(...); を MethodCall に
    - その他は RawStmt として残す
    """
    block = Block()
    src = block_src.strip()

    # より堅牢なif文パターン（ネストされた{}を考慮）
    if_pattern = re.compile(
        r'if\s*\((?P<cond>[^)]*)\)\s*\{(?P<then>(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}'
        r'(\s*else\s*\{(?P<else>(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\})?',
        re.DOTALL,
    )

    pos = 0
    for m in if_pattern.finditer(src):
        # if より前の部分
        before = src[pos:m.start()].strip()
        if before:
            _append_simple_statements(block, before)

        cond = m.group("cond").strip()
        then_body = m.group("then").strip()
        else_body = (m.group("else") or "").strip() or None

        then_block = Block()
        _append_simple_statements(then_block, then_body)

        else_block = None
        if else_body:
            else_block = Block()
            _append_simple_statements(else_block, else_body)

        block.statements.append(IfStmt(cond, then_block, else_block))
        pos = m.end()

    # 残り
    tail = src[pos:].strip()
    if tail:
        _append_simple_statements(block, tail)

    return block


# ============================
# 4. Java ファイルから ClickHandlerIR を作る
# ============================

def _collect_var_to_id(src: str, id_set: set) -> Dict[str, str]:
    """
    Java から 変数名→XML id の対応を拾う。
      Button btnLogin = findViewById(R.id.btnLogin);
      tvSignup = findViewById(R.id.tvSignup);
    など。
    """
    var_to_id: Dict[str, str] = {}

    pat = re.compile(
        r'(?:\b\w+\s+)?'          # 型 (任意)
        r'(?P<var>\w+)\s*=\s*'
        r'(?:\(\s*\w+\s*\)\s*)?'  # キャスト (任意)
        r'findViewById\s*\(\s*R\.id\.(?P<id>\w+)\s*\)\s*;',
    )

    for m in pat.finditer(src):
        v = m.group("var")
        i = m.group("id")
        if i in id_set:
            var_to_id[v] = i

    return var_to_id


def _extract_onclick_body(body: str) -> str:
    """
    setOnClickListener(...) の引数部分から onClick の中身だけを抽出。
    ラムダなら { ... } の中身、匿名クラスなら onClick(...) { ... } の {...} を返す。
    """
    body = body.strip()

    # ラムダ v -> { ... }
    m = re.search(r'->\s*\{(.*)\}\s*$', body, re.DOTALL)
    if m:
        return m.group(1)

    # 匿名クラス new ... { public void onClick(...) { ... } }
    m = re.search(r'onClick\s*\([^)]*\)\s*\{(.*)\}\s*[^}]*$', body, re.DOTALL)
    if m:
        return m.group(1)

    # フォールバック
    return body


def _extract_handlers_from_src(src: str, var2id: Dict[str, str], id_set: set) -> List[ClickHandlerIR]:
    """
    1 ファイル中の setOnClickListener(...) を全部拾って ClickHandlerIR にする。
    """
    handlers: List[ClickHandlerIR] = []

    pat = re.compile(
        r'(?P<target>[\w\.]+(?:\(\s*R\.id\.(?P<id>\w+)\s*\))?)\s*'
        r'\.\s*setOnClickListener\s*\('
        r'(?P<body>'
        r'(?:\w+|\([^)]*\))\s*->\s*\{.*?\}'                                      # ラムダ
        r'|new\s+\w+(?:\.\w+)*\s*\(\)\s*\{.*?onClick\s*\([^)]*\)\s*\{.*?\}.*?\}'  # 匿名クラス
        r')\s*\)\s*;',
        re.DOTALL,
    )

    idx = 0
    for m in pat.finditer(src):
        target_expr = m.group("target")
        inline_id = m.group("id")
        body = m.group("body")

        view_ids: List[str] = []
        if inline_id and inline_id in id_set:
            view_ids.append(inline_id)
        else:
            # target: "btnLogin" or "binding.tvSignup"
            v = target_expr.split('(')[0]
            v = v.split('.')[-1]
            if v in var2id:
                view_ids.append(var2id[v])

        if not view_ids:
            continue

        inner = _extract_onclick_body(body)
        ast_block = _parse_block_to_ast(inner)
        func_name = f"_on_click_{idx}"
        idx += 1

        handlers.append(
            ClickHandlerIR(
                name=func_name,
                view_ids=view_ids,
                java_src=inner.strip(),
                ast=ast_block,
            )
        )

    return handlers


def extract_click_handlers(java_root: str, xml_ids: List[str]) -> Dict[str, ClickHandlerIR]:
    """
    java_root 以下の .java を全部見て、
      - findViewById から var→id を作り
      - setOnClickListener(...) から onClick 本体を抜き出し
      - ミニ AST(Block) にして ClickHandlerIR として返す
    最後は {id: ClickHandlerIR} の dict。
    """
    root = Path(java_root)
    java_files = list(root.rglob("*.java"))

    id_set = set(xml_ids)
    handlers_by_id: Dict[str, ClickHandlerIR] = {}

    for jf in java_files:
        src = jf.read_text(encoding="utf-8", errors="ignore")
        var2id = _collect_var_to_id(src, id_set)

        for h in _extract_handlers_from_src(src, var2id, id_set):
            for vid in h.view_ids:
                if vid not in handlers_by_id:
                    handlers_by_id[vid] = h

    return handlers_by_id


# ============================
# Fragment検出用のIR
# ============================

@dataclass
class FragmentIR:
    """Fragmentの動的追加を表すIR"""
    container_id: str          # XML内のコンテナID（例: "container"）
    fragment_class: str          # Fragmentクラス名（例: "CardViewFragment"）
    layout_file: Optional[str]  # 推測されたレイアウトファイル名（例: "fragment_card_view.xml"）


def _camel_to_snake(name: str) -> str:
    """キャメルケースをスネークケースに変換"""
    """キャメルケースをスネークケースに変換"""
    import re
    # 大文字の前にアンダースコアを挿入（最初の文字以外）
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
    # 連続する大文字の前にもアンダースコアを挿入
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
    return s2.lower()
def _guess_fragment_layout(fragment_class: str, layout_dir: str) -> Optional[str]:
    """
    Fragmentクラス名からレイアウトファイル名を推測
    
    CardViewFragment -> fragment_card_view.xml または card_view_fragment.xml
    """
    if not fragment_class.endswith("Fragment"):
        return None
    
    # Fragmentを削除
    base_name = fragment_class[:-8]  # "Fragment"の8文字を削除
    
    # キャメルケースをスネークケースに変換
    snake_case = _camel_to_snake(base_name)
    
    # 候補を生成
    candidates = [
        f"fragment_{snake_case}.xml",
        f"{snake_case}_fragment.xml",
        f"fragment_{snake_case.lower()}.xml",
        f"{snake_case.lower()}_fragment.xml",
    ]
    
    # 実際に存在するファイルを確認
    import os
    for candidate in candidates:
        candidate_path = os.path.join(layout_dir, candidate)
        if os.path.exists(candidate_path):
            return candidate
    
    # 見つからない場合は最初の候補を返す（存在確認は後で行う）
    return candidates[0] if candidates else None


def extract_fragments(java_root: str, layout_dir: str, xml_ids: List[str]) -> Dict[str, FragmentIR]:
    """
    java_root 以下の .java を全部見て、
    getFragmentManager().beginTransaction().add(R.id.xxx, FragmentClass.newInstance())
    のパターンを検出して FragmentIR として返す。
    
    戻り値: {container_id: FragmentIR} の dict
    """
    root = Path(java_root)
    java_files = list(root.rglob("*.java"))
    
    id_set = set(xml_ids)
    fragments_by_id: Dict[str, FragmentIR] = {}
    
    # Fragment追加パターンを検出
    # getFragmentManager().beginTransaction().add(R.id.xxx, FragmentClass.newInstance())
    # Fragment追加パターン（複数行に対応）
    # getFragmentManager().beginTransaction().add(R.id.xxx, FragmentClass.newInstance())
    pattern = re.compile(
        r'getFragmentManager\s*\(\s*\)\s*\.\s*beginTransaction\s*\(\s*\)\s*'
        r'(?:\s*\.\s*[^\n]*)*?\s*\.\s*add\s*\(\s*R\.id\.(\w+)\s*,\s*(\w+Fragment)\s*\.\s*newInstance\s*\(\s*\)\s*\)',
        re.DOTALL | re.MULTILINE
    )
    
    for jf in java_files:
        src = jf.read_text(encoding="utf-8", errors="ignore")
        
        for match in pattern.finditer(src):
            container_id = match.group(1)
            fragment_class = match.group(2)
            
            # コンテナIDがXMLに存在するか確認
            if container_id not in id_set:
                continue
            
            # レイアウトファイル名を推測
            layout_file = _guess_fragment_layout(fragment_class, layout_dir)
            
            fragment_ir = FragmentIR(
                container_id=container_id,
                fragment_class=fragment_class,
                layout_file=layout_file,
            )
            
            fragments_by_id[container_id] = fragment_ir
    
    return fragments_by_id
