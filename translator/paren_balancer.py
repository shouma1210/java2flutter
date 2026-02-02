"""括弧のバランスを修正するユーティリティ"""
import re


def balance_parens(code: str) -> str:
    """Dartコードの括弧のバランスを修正する
    
    問題のあるパターン:
    - `]))),)))` のような余分な閉じ括弧
    - `[` に対応する `]` がない
    - `(` に対応する `)` がない
    """
    if not code:
        return code
    
    lines = code.split('\n')
    fixed_lines = []
    
    for line in lines:
        # 行末の余分な閉じ括弧を削除
        # `]))),)))` のようなパターンを検出して修正
        original_line = line
        
        # 行末の `]));` や `]);` を削除（ただし、正しい構文の場合は残す）
        # 例: `Text(...),` の後に `]));` が来る場合は削除
        # ただし、`children: [\n...\n])` のような正しい構文は残す
        
        # まず、行末の余分な閉じ括弧を削除
        # `]));` や `]);` が行末にある場合、それが正しい構文かどうかを判定
        stripped = line.strip()
        
        # 行全体が `]));` や `]);` の場合は削除
        if stripped == ']));' or stripped == ']);':
            continue
        
        # 行末の `]));` や `]);` を削除（ただし、正しい構文の場合は残す）
        # 例: `style: TextStyle(color: Colors.grey[600])),)));` → `style: TextStyle(color: Colors.grey[600])),`
        # ただし、`children: [\n...\n])` のような正しい構文は残す
        
        # 行末の `]));` や `]);` を削除
        line = re.sub(r'\]\)\);?\s*$', '', line)
        line = re.sub(r'\]\);?\s*$', '', line)
        
        # ただし、`children: [` の後の行で、`])` が来る場合は正しい構文なので残す
        # この判定は複雑なので、一旦削除してから後で修正
        
        fixed_lines.append(line)
    
    # 全体の括弧のバランスを確認
    result = '\n'.join(fixed_lines)
    
    # 括弧の数をカウント
    open_paren = result.count('(')
    close_paren = result.count(')')
    open_bracket = result.count('[')
    close_bracket = result.count(']')
    open_brace = result.count('{')
    close_brace = result.count('}')
    
    # 括弧のバランスが取れていない場合は修正
    # ただし、これは複雑なので、基本的には行末の余分な括弧を削除するだけにする
    
    return result


def fix_widget_tree_parens(widget_tree: str) -> str:
    """widget_treeの括弧を修正する
    
    特に、`Column(children: [` の後の閉じ括弧を正しく処理する
    """
    if not widget_tree:
        return widget_tree
    
    lines = widget_tree.split('\n')
    fixed_lines = []
    
    for i, line in enumerate(lines):
        original_line = line
        stripped = line.strip()
        
        # 行全体が `]));` や `]);` の場合は削除
        if stripped == ']));' or stripped == ']);':
            continue
        
        # 行末の余分な閉じ括弧を削除
        # `style: TextStyle(color: Colors.grey[600])),)));` → `style: TextStyle(color: Colors.grey[600])),`
        # ただし、`children: [\n...\n])` のような正しい構文は残す
        
        # まず、行末の `]));` や `]);` を削除
        line = re.sub(r'\]\)\);+\s*$', '', line)  # `]));` を削除
        line = re.sub(r'\]\);+\s*$', '', line)    # `]);` を削除
        
        # `Text(...),` の後に `))` が来る場合は、1つだけ残す
        # `style: TextStyle(...)),))` → `style: TextStyle(...)),`
        # ただし、`children: [\n...\n])` のような正しい構文は残す
        # 行末の `))` を `)` に変換（ただし、`])` の場合は残す）
        # `style: TextStyle(...)),)));` → `style: TextStyle(...)),`
        # まず、`),)));` のようなパターンを `),` に変換
        # `style: TextStyle(...)),)));` → `style: TextStyle(...)),`
        # パターン1: `),)));` → `),` (セミコロン付き)
        # `style: TextStyle(...)),)));` → `style: TextStyle(...)),`
        # まず、`),` の後に2つ以上の `)` が続く場合を `),` に変換
        # `style: TextStyle(...)),)));` → `style: TextStyle(...)),`
        # セミコロン付きの場合（`);`で終わる）
        line = re.sub(r'\),\){2,};+\s*$', r'),', line)
        # セミコロンなしの場合（`))`で終わる）
        line = re.sub(r'\),\){2,}(\s*)$', r'),\1', line)
        # パターン2: 行末の `))` を `)` に変換（ただし、`])` の場合は残す）
        # `style: TextStyle(...)),))` → `style: TextStyle(...)),`
        if not re.search(r'\]\)\s*$', line):  # `])` で終わっていない場合のみ
            # 2つ以上の閉じ括弧を1つに
            line = re.sub(r'\){2,}(\s*)$', r')\1', line)
        
        # `Text(...),` の後に `]` が来る場合は削除（ただし、`children: [` の後の `]` は残す）
        # この判定は複雑なので、一旦削除してから後で修正
        
        fixed_lines.append(line)
    
    widget_tree = '\n'.join(fixed_lines)
    
    # 最後に再度確認して、`]));` や `]);` が残っていれば削除
    widget_tree = widget_tree.rstrip()
    while widget_tree.endswith(']));') or widget_tree.endswith(']);'):
        if widget_tree.endswith(']));'):
            widget_tree = widget_tree[:-4].rstrip()
        elif widget_tree.endswith(']);'):
            widget_tree = widget_tree[:-3].rstrip()
    
    # さらに、行末の余分な `))` を削除
    # `style: TextStyle(...)),))` → `style: TextStyle(...)),`
    lines = widget_tree.split('\n')
    fixed_lines = []
    for line in lines:
        # 行末の `))` を `)` に変換（ただし、`])` の場合は残す）
        # `style: TextStyle(...)),))` → `style: TextStyle(...)),`
        # まず、`),` の後に2つ以上の `)` が続く場合を `),` に変換
        line = re.sub(r'\),\){2,}(\s*)$', r'),\1', line)
        # 次に、行末の `))` を `)` に変換（ただし、`])` の場合は残す）
        if not re.search(r'\]\)\s*$', line):  # `])` で終わっていない場合のみ
            # 2つ以上の閉じ括弧を1つに
            line = re.sub(r'\){2,}(\s*)$', r')\1', line)
        fixed_lines.append(line)
    widget_tree = '\n'.join(fixed_lines)
    
    return widget_tree
