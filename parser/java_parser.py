from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


class AstNode:
    pass


@dataclass
class MethodCall(AstNode):
    target: Optional[str]  
    args: str              


@dataclass
class IfStmt(AstNode):
    condition: str
    then_block: "Block"
    else_block: Optional["Block"] = None


@dataclass
class RawStmt(AstNode):
    text: str


@dataclass
class Block(AstNode):
    statements: List[AstNode] = field(default_factory=list)


@dataclass
class ClickHandlerIR:
    name: str             
    view_ids: List[str] 
    java_src: str         
    ast: Block             




def _append_simple_statements(block: Block, src: str) -> None:
    """
    セミコロン区切りでステートメントを分割し、
    MethodCall / RawStmt に振り分けて Block に追加。
    """
   
    src = re.sub(r'//.*', '', src)
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.DOTALL)

  
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

      
        if stmt.strip().startswith("if"):
            block.statements.append(RawStmt(text=stmt))
            continue

      
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

    block = Block()
    src = block_src.strip()

  
    if_pattern = re.compile(
        r'if\s*\((?P<cond>[^)]*)\)\s*\{(?P<then>(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}'
        r'(\s*else\s*\{(?P<else>(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\})?',
        re.DOTALL,
    )

    pos = 0
    for m in if_pattern.finditer(src):
    
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


    tail = src[pos:].strip()
    if tail:
        _append_simple_statements(block, tail)

    return block




def _collect_var_to_id(src: str, id_set: set) -> Dict[str, str]:
  
    var_to_id: Dict[str, str] = {}

    pat = re.compile(
        r'(?:\b\w+\s+)?'         
        r'(?P<var>\w+)\s*=\s*'
        r'(?:\(\s*\w+\s*\)\s*)?' 
        r'findViewById\s*\(\s*R\.id\.(?P<id>\w+)\s*\)\s*;',
    )

    for m in pat.finditer(src):
        v = m.group("var")
        i = m.group("id")
        if i in id_set:
            var_to_id[v] = i

    return var_to_id


def _extract_onclick_body(body: str) -> str:
  
    body = body.strip()

   
    m = re.search(r'->\s*\{(.*)\}\s*$', body, re.DOTALL)
    if m:
        return m.group(1)

  
    m = re.search(r'->\s*(.+?)\s*$', body, re.DOTALL)
    if m:
        return m.group(1).strip()

 
    m = re.search(r'onClick\s*\([^)]*\)\s*\{(.*)\}\s*[^}]*$', body, re.DOTALL)
    if m:
        return m.group(1)

  
    return body


def _extract_handlers_from_src(src: str, var2id: Dict[str, str], id_set: set) -> List[ClickHandlerIR]:

    handlers: List[ClickHandlerIR] = []

    pat = re.compile(
        r'(?P<target>[\w\.]+(?:\(\s*R\.id\.(?P<id>\w+)\s*\))?)\s*'
        r'\.\s*setOnClickListener\s*\('
        r'(?P<body>'
        r'(?:\w+|\([^)]*\))\s*->\s*(?:\{.*?\}|[^;]+)'                                     
        r'|new\s+\w+(?:\.\w+)*\s*\(\)\s*\{.*?onClick\s*\([^)]*\)\s*\{.*?\}.*?\}'  
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
        
            v = target_expr.split('(')[0]
            v = v.split('.')[-1]
            if v in var2id:
                view_ids.append(var2id[v])
            elif v in id_set:
               
                view_ids.append(v)

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


def extract_methods(java_root: str) -> Dict[str, str]:

    root = Path(java_root)
    java_files = list(root.rglob("*.java"))
    methods: Dict[str, str] = {}
    
    for jf in java_files:
        src = jf.read_text(encoding="utf-8", errors="ignore")

        method_pattern = re.compile(
            r'(?:private|public|protected)?\s*void\s+(\w+)\s*\([^)]*\)\s*\{',
            re.MULTILINE
        )
        
        for match in method_pattern.finditer(src):
            method_name = match.group(1)
            start_pos = match.end()
            
         
            brace_count = 1
            pos = start_pos
            while pos < len(src) and brace_count > 0:
                if src[pos] == '{':
                    brace_count += 1
                elif src[pos] == '}':
                    brace_count -= 1
                pos += 1
            
            if brace_count == 0:
                method_body = src[start_pos:pos-1].strip()
                if method_body and method_name not in methods:
                    methods[method_name] = method_body
    
    return methods


def extract_click_handlers(java_root: str, xml_ids: List[str]) -> Dict[str, ClickHandlerIR]:

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



@dataclass
class FragmentIR:

    container_id: str         
    fragment_class: str          
    layout_file: Optional[str]  

def _camel_to_snake(name: str) -> str:
  
    import re
   
    s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
   
    s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
    return s2.lower()
def _guess_fragment_layout(fragment_class: str, layout_dir: str) -> Optional[str]:
 
    if not fragment_class.endswith("Fragment"):
        return None
    
   
    base_name = fragment_class[:-8] 
    
   
    snake_case = _camel_to_snake(base_name)
    
  
    candidates = [
        f"fragment_{snake_case}.xml",
        f"{snake_case}_fragment.xml",
        f"fragment_{snake_case.lower()}.xml",
        f"{snake_case.lower()}_fragment.xml",
    ]
    
   
    import os
    for candidate in candidates:
        candidate_path = os.path.join(layout_dir, candidate)
        if os.path.exists(candidate_path):
            return candidate
    
 
    return candidates[0] if candidates else None


def extract_fragments(java_root: str, layout_dir: str, xml_ids: List[str]) -> Dict[str, FragmentIR]:

    root = Path(java_root)
    java_files = list(root.rglob("*.java"))
    
    id_set = set(xml_ids)
    fragments_by_id: Dict[str, FragmentIR] = {}
    
  
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
            
          
            if container_id not in id_set:
                continue
            
        
            layout_file = _guess_fragment_layout(fragment_class, layout_dir)
            
            fragment_ir = FragmentIR(
                container_id=container_id,
                fragment_class=fragment_class,
                layout_file=layout_file,
            )
            
            fragments_by_id[container_id] = fragment_ir
    
    return fragments_by_id
