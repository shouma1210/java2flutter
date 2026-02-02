# android2flutter/translator/layout_rules.py
from parser.resource_resolver import ResourceResolver
import os
from utils import indent, apply_layout_modifiers

def _wrap_match_parent_for_linear(child_code: str, child_attrs: dict, parent_orientation: str) -> str:
    """LinearLayout 配下の子の match_parent を Expanded / width∞ で表現する"""
    w = (child_attrs.get("layout_width") or "").lower()
    h = (child_attrs.get("layout_height") or "").lower()
    code = child_code

    if parent_orientation == "vertical":
        if h == "match_parent":
            code = f"Expanded(child: {code})"
        # Expandedでラップした後は、SizedBoxでラップしない（ExpandedはRow/Columnの直接の子である必要がある）
        if w == "match_parent" and "Expanded" not in code:
            # match_parentの意図は「親の幅いっぱい」なので、double.infinityを使用（親の制約に従う）
            # MediaQueryを使うとRow内で問題が起こるため、double.infinityに戻す
            code = f"SizedBox(width: double.infinity, child: {code})"
    else:  # horizontal (Row)
        if w == "match_parent":
            code = f"Expanded(child: {code})"
        # Expandedでラップした後は、SizedBoxでラップしない
        # Row内ではheight: double.infinityは使えない（unbounded制約エラーの原因）
        # match_parentの場合は、RowのcrossAxisAlignmentに従うか、固定値を使用
        if h == "match_parent" and "Expanded" not in code:
            # Row内でmatch_parentの高さは、親の高さに合わせるのではなく、
            # crossAxisAlignmentに従うか、明示的な高さを指定する
            # ここでは、RowのcrossAxisAlignmentがcenterの場合を考慮して、高さを指定しない
            # または、明示的な高さが必要な場合は、適切な値を設定する
            # とりあえず、Row内ではheight制約を追加しない（crossAxisAlignmentに従う）
            pass
    return code

def _convert_relative_layout_to_column(children: list, resolver, logic_map=None, fragments_by_id=None, layout_dir=None, values_dir=None) -> str:
    """RelativeLayoutをColumn構造に変換（layout_belowを処理）"""
    # layout_belowの依存関係を構築
    # 各要素がどの要素の下に配置されるかを記録
    below_map = {}  # child_id -> below_id
    child_map = {}  # child_id -> (child_node, child_code)
    
    # layout_centerHorizontalがある要素の数をカウント
    has_center_horizontal = False
    for ch in children:
        child_attrs = ch.get("attrs", {}) or {}
        if child_attrs.get("layout_centerHorizontal", "").lower() == "true":
            has_center_horizontal = True
            break
    
    # まず、すべての子要素を変換
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
            # IDがない要素も処理（順序を保持）
            child_map[f"_no_id_{len(child_map)}"] = (ch, child_code)
    
    # layout_belowがない要素（ルート要素）を見つける
    root_ids = set(child_map.keys()) - set(below_map.values())
    
    # Column構造を構築
    column_children = []
    processed_ids = set()
    
    # ルート要素から開始（XMLの順序を保持）
    for ch in children:
        child_attrs = ch.get("attrs", {}) or {}
        raw_id = child_attrs.get("id", "")
        child_id = raw_id.split("/")[-1] if raw_id else None
        if not child_id:
            child_id = f"_no_id_{children.index(ch)}"
        
        # ルート要素の場合
        if child_id in root_ids and child_id not in processed_ids:
            ch_node, root_code = child_map[child_id]
            # ColumnのcrossAxisAlignmentを決定
            cross_axis_str = "center" if has_center_horizontal else "stretch"
            root_code = _wrap_relative_layout_child(root_code, ch_node.get("attrs", {}), column_cross_axis=cross_axis_str)
            column_children.append(root_code)
            processed_ids.add(child_id)
            
            # この要素の下に配置される要素を追加
            # 同じbelow_idを持つ要素をRowで横に並べる（XMLの順序を保持）
            same_below_ids = []
            for ch2 in children:
                child_attrs2 = ch2.get("attrs", {}) or {}
                raw_id2 = child_attrs2.get("id", "")
                child_id2 = raw_id2.split("/")[-1] if raw_id2 else None
                if not child_id2:
                    child_id2 = f"_no_id_{children.index(ch2)}"
                if child_id2 in below_map and below_map[child_id2] == child_id:
                    same_below_ids.append((children.index(ch2), child_id2))
            
            # XMLの順序でソート
            same_below_ids.sort(key=lambda x: x[0])
            
            if same_below_ids:
                row_children = []
                for _, below_id in same_below_ids:
                    if below_id not in processed_ids and below_id in child_map:
                        ch_below, below_code = child_map[below_id]
                        # ColumnのcrossAxisAlignmentを決定
                        below_code = _wrap_relative_layout_child(below_code, ch_below.get("attrs", {}), column_cross_axis=cross_axis_str)
                        row_children.append(below_code)
                        processed_ids.add(below_id)
                
                if row_children:
                    # 同じbelow_idを持つ要素をRowで横に並べる
                    row_children_joined = ",\n".join(row_children)
                    column_children.append(f"Row(children: [\n{indent(row_children_joined)}\n])")
    
    # 処理されていない要素も追加（layout_belowが循環参照している場合など）
    for child_id, (ch, child_code) in child_map.items():
        if child_id not in processed_ids:
            # ColumnのcrossAxisAlignmentを決定
            cross_axis_str = "center" if has_center_horizontal else "stretch"
            child_code = _wrap_relative_layout_child(child_code, ch.get("attrs", {}), column_cross_axis=cross_axis_str)
            column_children.append(child_code)
    
    if column_children:
        children_joined = ",\n".join(column_children)
        # layout_centerHorizontalがある場合は、crossAxisAlignmentをcenterにする
        cross_axis_str = "center" if has_center_horizontal else "stretch"
        cross_axis = "CrossAxisAlignment.center" if has_center_horizontal else "CrossAxisAlignment.stretch"
        return f"Column(crossAxisAlignment: {cross_axis}, children: [\n{indent(children_joined)}\n])"
    else:
        return "SizedBox.shrink()"

def _wrap_relative_layout_child(child_code: str, child_attrs: dict, column_cross_axis: str = "stretch") -> str:
    """RelativeLayoutの子要素をラップ（layout_alignParentLeftなどの属性を処理）"""
    layout_align_parent_left = child_attrs.get("layout_alignParentLeft", "").lower() == "true"
    layout_align_parent_right = child_attrs.get("layout_alignParentRight", "").lower() == "true"
    layout_center_horizontal = child_attrs.get("layout_centerHorizontal", "").lower() == "true"
    layout_to_left_of = child_attrs.get("layout_toLeftOf")
    layout_align_right = child_attrs.get("layout_alignRight")
    
    # 水平方向の配置を処理
    # ColumnのcrossAxisAlignmentがcenterの場合、layout_center_horizontalは冗長なのでラップしない
    if column_cross_axis == "center" and layout_center_horizontal:
        # ColumnのcrossAxisAlignmentで既に中央配置されているので、Alignでラップしない
        pass
    elif layout_center_horizontal:
        # 中央配置（ColumnのcrossAxisAlignmentがstretchの場合のみAlignを使用）
        if column_cross_axis == "stretch":
            # stretchとcenterは矛盾するので、Alignでラップしない
            pass
        else:
            # その他の場合はAlignを使用
            child_code = f"Align(alignment: Alignment.center, child: {child_code})"
    elif layout_align_parent_left:
        # 左端配置
        child_code = f"Align(alignment: Alignment.centerLeft, child: {child_code})"
    elif layout_align_parent_right:
        # 右端配置
        child_code = f"Align(alignment: Alignment.centerRight, child: {child_code})"
    elif layout_to_left_of or layout_align_right:
        # 右側に配置（簡易的な実装）
        child_code = f"Align(alignment: Alignment.centerRight, child: {child_code})"
    
    return child_code

def _axes_from_gravity_for_linear(gravity: str, orientation: str, allow_center: bool = False):
    """gravity を Flutter の main/cross axis に落とす。シンプルに center/horizontal/vertical を扱う。
    
    allow_center: Trueの場合、MainAxisAlignment.centerを許可（SingleChildScrollView外で使用）
    """
    g = (gravity or "").lower()
    main = "MainAxisAlignment.start"
    # crossAxisAlignment は stretch をデフォルトにする（TextField などの幅を広げるため）
    cross = "CrossAxisAlignment.stretch"

    # MainAxisAlignment.center は SingleChildScrollView と衝突する可能性があるため、
    # allow_centerがTrueの場合のみ使用
    if "center" in g:
        if orientation == "vertical":
            if allow_center:
                main = "MainAxisAlignment.center"
            # cross は stretch のまま（TextField などの幅を広げるため）
        else:
            # horizontal の場合
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
    """子要素が背景画像（ImageView with match_parent）かどうかを判定"""
    if not child_node:
        return False
    t = (child_node.get("type") or "").lower()
    if not (t.endswith("imageview") or t == "appcompatimageview"):
        return False
    child_attrs = child_node.get("attrs", {}) or {}
    width = (child_attrs.get("layout_width") or "").lower()
    height = (child_attrs.get("layout_height") or "").lower()
    # match_parent または fill_parent の場合、背景画像の可能性が高い
    return width in ("match_parent", "fill_parent") and height in ("match_parent", "fill_parent")

def _is_centered_in_constraint(child_attrs: dict) -> bool:
    """ConstraintLayoutの子要素が上下左右すべてparentに制約されているか（中央配置）を判定"""
    top = child_attrs.get("layout_constraintTop_toTopOf", "")
    bottom = child_attrs.get("layout_constraintBottom_toBottomOf", "")
    bottom_to_top = child_attrs.get("layout_constraintBottom_toTopOf", "")
    top_to_bottom = child_attrs.get("layout_constraintTop_toBottomOf", "")
    start = child_attrs.get("layout_constraintStart_toStartOf", "")
    end = child_attrs.get("layout_constraintEnd_toEndOf", "")
    
    # 上下左右すべてが"parent"に制約されている場合、中央配置と判定
    # または、Top/Bottomのどちらかがparentで、Vertical_biasがある場合も中央配置と判定
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
    """ConstraintLayoutのbias値を取得（vertical_bias, horizontal_bias）"""
    vertical_bias = child_attrs.get("layout_constraintVertical_bias")
    horizontal_bias = child_attrs.get("layout_constraintHorizontal_bias")
    
    v_bias = float(vertical_bias) if vertical_bias else None
    h_bias = float(horizontal_bias) if horizontal_bias else None
    
    return v_bias, h_bias

def _get_background_images(children: list) -> tuple:
    """背景画像を検出し、分類する（full, top, bottom, foreground）"""
    bg_full = []
    bg_top = []
    bg_bottom = []
    foreground = []
    
    # すべての背景画像を検出
    bg_images = [ch for ch in children if _is_background_image_view(ch)]
    
    if len(bg_images) == 1:
        # 背景画像が1枚の場合、画面全体に敷く
        bg_full.append(bg_images[0])
        foreground = [ch for ch in children if not _is_background_image_view(ch)]
    elif len(bg_images) > 1:
        # 背景画像が複数ある場合、すべてを背景として扱う
        # 最初の1枚を画面全体に敷き、残りは前景として扱う（XML内の順序を保持）
        # ただし、背景画像が複数ある場合は、すべてを背景として扱う方が確実
        # 最初の1枚を画面全体に敷く
        bg_full.append(bg_images[0])
        # 残りの背景画像も前景として扱う（XML内の順序を保持）
        foreground = [ch for ch in children if ch != bg_images[0]]
    else:
        # 背景画像がない場合
        foreground = children
    
    return bg_full, bg_top, bg_bottom, foreground

def _get_background_image_with_cover(bg_image_code: str, attrs: dict) -> str:
    """背景画像をBoxFit.coverで画面全体に表示するように変換"""
    # scaleTypeを確認（デフォルトはcenterCrop相当のcover）
    scale_type = (attrs.get("scaleType") or attrs.get("android:scaleType") or "centerCrop").lower()
    box_fit = "BoxFit.cover"  # 背景画像は通常coverを使用
    if "centerCrop" in scale_type:
        box_fit = "BoxFit.cover"
    elif "fitCenter" in scale_type or "centerInside" in scale_type:
        box_fit = "BoxFit.contain"
    elif "fitXY" in scale_type:
        box_fit = "BoxFit.fill"
    
    # Image.assetのfitパラメータを強制的に指定されたbox_fitに変更
    import re
    # fit: BoxFit.xxx を fit: {box_fit} に置き換え
    if re.search(r'fit:\s*BoxFit\.\w+', bg_image_code):
        bg_image_code = re.sub(r'fit:\s*BoxFit\.\w+', f'fit: {box_fit}', bg_image_code)
    else:
        # fitパラメータがない場合は追加（Image.asset('...', の後に fit を追加）
        bg_image_code = re.sub(
            r"Image\.asset\('([^']+)'",
            f"Image.asset('\\1', fit: {box_fit}",
            bg_image_code
        )
    
    # errorBuilderを画面全体をカバーするように修正
    # view_rules.pyで既に適切なerrorBuilderが生成されている場合は、置き換えをスキップ
    # チェック: errorBuilderにwidth/heightが含まれている場合は置き換えが必要
    if 'errorBuilder:' in bg_image_code:
        # width: 180, height: 180 または width: \d+, height: \d+ が含まれている場合は置き換え
        if re.search(r'width:\s*\d+.*height:\s*\d+', bg_image_code):
            # errorBuilder: から Image.asset の最後の閉じ括弧までを正確に置き換える
            error_builder_start = bg_image_code.find('errorBuilder:')
            if error_builder_start != -1:
                # errorBuilder: の後の部分を取得
                after_error_builder = bg_image_code[error_builder_start + len('errorBuilder:'):]
                # 括弧のバランスを取って、errorBuilderの終わりを見つける
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
                            # errorBuilderの閉じ括弧を見つけた
                            error_builder_end = error_builder_start + len('errorBuilder:') + pos + 1
                            # errorBuilderの部分を置き換え
                            replacement = 'errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey[300], child: Icon(Icons.image, size: 80, color: Colors.grey[600]))'
                            bg_image_code = bg_image_code[:error_builder_start] + replacement + bg_image_code[error_builder_end:]
                            break
                    pos += 1
    else:
        # errorBuilderがない場合は追加
        # 最後の閉じ括弧の前に追加
        last_paren = bg_image_code.rfind(')')
        if last_paren != -1:
            bg_image_code = bg_image_code[:last_paren] + f", errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey[300], child: Icon(Icons.image, size: 80, color: Colors.grey[600]))" + bg_image_code[last_paren:]
        else:
            bg_image_code += f", errorBuilder: (context, error, stackTrace) => Container(color: Colors.grey[300], child: Icon(Icons.image, size: 80, color: Colors.grey[600]))"
    
    return bg_image_code

def translate_layout(node, resolver, logic_map=None, fragments_by_id=None, layout_dir=None, values_dir=None):
    """
    ViewGroup を Flutter ウィジェットへ。
    logic_map: view_id → handler 名
    """
    t = node["type"]
    attrs = node.get("attrs", {}) or {}
    children = node.get("children", []) or []

    
        # ========== ListView ==========
    if t == "ListView":
        # ListViewはFlutterのListView.builderに変換
        # 子要素がある場合は、それらをitemsとして使用
        if children:
            dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
            children_joined = ",\n".join(dart_children)
            # ListView.builderを使用（実際のデータは後で追加する必要がある）
            body = f"ListView.builder(itemCount: {len(children)}, itemBuilder: (context, index) {{ return {dart_children[0] if dart_children else 'SizedBox.shrink()'}; }})"
        else:
            # 空のListViewの場合は、空のListView.builderを生成
            body = "ListView.builder(itemCount: 0, itemBuilder: (context, index) => SizedBox.shrink())"
        return apply_layout_modifiers(body, attrs, resolver)
    
    # ========== HorizontalScrollView ==========
    if t == "HorizontalScrollView" or t.endswith("HorizontalScrollView"):
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
        if len(dart_children) == 1:
            # 子要素が1つの場合、SingleChildScrollViewでラップ（水平方向）
            body = f"SingleChildScrollView(scrollDirection: Axis.horizontal, child: {dart_children[0]})"
        elif len(dart_children) > 1:
            # 子要素が複数の場合、RowでラップしてからSingleChildScrollViewでラップ（水平方向）
            children_joined = ",\n".join(dart_children)
            body = f"SingleChildScrollView(scrollDirection: Axis.horizontal, child: Row(children: [\n{indent(children_joined)}\n]))"
        else:
            # 子要素がない場合
            body = "SingleChildScrollView(scrollDirection: Axis.horizontal, child: SizedBox.shrink())"
        return apply_layout_modifiers(body, attrs, resolver)
    
    # ========== ScrollView / NestedScrollView ==========
    if t == "ScrollView" or t == "NestedScrollView" or t.endswith("NestedScrollView"):
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
        if len(dart_children) == 1:
            # 子要素が1つの場合、SingleChildScrollViewでラップ
            body = f"SingleChildScrollView(child: {dart_children[0]})"
        elif len(dart_children) > 1:
            # 子要素が複数の場合、ColumnでラップしてからSingleChildScrollViewでラップ
            children_joined = ",\n".join(dart_children)
            body = f"SingleChildScrollView(child: Column(children: [\n{indent(children_joined)}\n]))"
        else:
            # 子要素がない場合
            body = "SingleChildScrollView(child: SizedBox.shrink())"
        return apply_layout_modifiers(body, attrs, resolver)
    
    # ========== RadioGroup ==========
    if t == "RadioGroup":
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
        # RadioGroupはColumnで表現（RadioButtonはRadioListTileで表現）
        orientation = attrs.get("orientation", "vertical").lower()
        
        if orientation == "horizontal":
            children_joined = ",\n".join(dart_children)
            body = f"Row(children: [\n{indent(children_joined)}\n])"
        else:
            children_joined = ",\n".join(dart_children)
            body = f"Column(children: [\n{indent(children_joined)}\n])"
        
        return apply_layout_modifiers(body, attrs, resolver)

    # ========== TableLayout ==========
    if t == "TableLayout":
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
        # TableLayoutはColumnで表現（TableRowはRowで表現）
        # Row間のスペーシングを追加
        spaced_children = []
        for i, child in enumerate(dart_children):
            spaced_children.append(child)
            if i < len(dart_children) - 1:  # 最後の要素以外の後にスペーシングを追加
                spaced_children.append("SizedBox(height: 16)")
        children_joined = ",\n".join(spaced_children)
        # パディングとスペーシングを追加
        body = f"Padding(padding: EdgeInsets.all(16.0), child: Column(mainAxisAlignment: MainAxisAlignment.start, crossAxisAlignment: CrossAxisAlignment.stretch, children: [\n{indent(children_joined)}\n]))"
        return apply_layout_modifiers(body, attrs, resolver)
    
    # ========== TableRow ==========
    if t == "TableRow":
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
        # TableRowはRowで表現
        if len(dart_children) == 1:
            # 要素が1つだけの場合（通常はボタン）、中央揃え
            body = f"Row(mainAxisAlignment: MainAxisAlignment.center, children: [\n{indent(dart_children[0])}\n])"
        else:
            # ラベルと入力フィールドを適切に配置するため、Expandedを使用
            improved_children = []
            for i, child_code in enumerate(dart_children):
                # 最初の要素（通常はラベル）は固定幅、2番目以降はExpandedで伸縮
                if i == 0:
                    # ラベルは固定幅
                    improved_children.append(f"SizedBox(width: 120, child: {child_code})")
                else:
                    # 入力フィールドはExpandedで伸縮
                    improved_children.append(f"Expanded(child: {child_code})")
            
            if not improved_children:
                improved_children = dart_children
            
            children_joined = ",\n".join(improved_children)
            body = f"Row(crossAxisAlignment: CrossAxisAlignment.center, children: [\n{indent(children_joined)}\n])"
        return apply_layout_modifiers(body, attrs, resolver)

    # ========== LinearLayout ==========
    if t == "LinearLayout":
        orientation = attrs.get("orientation", "vertical").lower()
        gravity = attrs.get("gravity", "")

        dart_children_list = []
        for ch in children:
            child_code = translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
            child_attrs = ch.get("attrs", {}) or {}
            # Viewタグは固定値で処理されているので、_wrap_match_parent_for_linearをスキップ
            child_type = (ch.get("type") or "").lower()
            if child_type != "view":
                child_code = _wrap_match_parent_for_linear(child_code, child_attrs, orientation)
            dart_children_list.append(child_code)

        children_joined = ",\n".join(dart_children_list)
        # SingleChildScrollView外で使用する場合、centerを許可
        # ただし、実際の使用コンテキストを確認する必要があるため、デフォルトはFalse
        main, cross = _axes_from_gravity_for_linear(gravity, orientation, allow_center=False)
        
        # gravity="center"の場合、Centerでラップする
        needs_center_wrap = "center" in gravity.lower() and orientation == "vertical"

        if orientation == "horizontal":
            # RowではcrossAxisAlignment.stretchは問題を起こしやすいため、centerを使用
            if cross == "CrossAxisAlignment.stretch":
                cross = "CrossAxisAlignment.center"
            body = (
                f"Row(mainAxisAlignment: {main}, crossAxisAlignment: {cross}, children: [\n"
                f"{indent(children_joined)}\n])"
            )
        else:
            # SingleChildScrollView内ではmainAxisSizeを指定しない（デフォルトのmaxを使用）
            # これにより、Columnが正しい高さを持つようになる
            # gravity="center"の場合、Centerでラップ
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

    # ========== FrameLayout ==========
    if t == "FrameLayout":
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
        # Fragment検出: 空のコンテナでFragmentが検出された場合、そのレイアウトを読み込む
        if not dart_children and fragments_by_id:
            raw_id = attrs.get("id")
            if raw_id:
                container_id = raw_id.split("/")[-1]
                if container_id in fragments_by_id:
                    fragment_ir = fragments_by_id[container_id]
                    if fragment_ir.layout_file and layout_dir:
                        fragment_layout_path = os.path.join(layout_dir, fragment_ir.layout_file)
                        if os.path.exists(fragment_layout_path):
                            # Fragmentのレイアウトを読み込んで変換
                            from parser.xml_parser import parse_layout_xml
                            try:
                                fragment_ir_tree, fragment_resolver = parse_layout_xml(fragment_layout_path, values_dir)
                                # Fragmentのレイアウトを変換
                                fragment_widget = translate_node(fragment_ir_tree, fragment_resolver or resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                                body = fragment_widget
                            except Exception as e:
                                # 読み込みに失敗した場合は警告を表示
                                body = f"Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                                       f"  Icon(Icons.error_outline, size: 48, color: Colors.red),\n" \
                                       f"  SizedBox(height: 16),\n" \
                                       f"  Text('Failed to load fragment: {fragment_ir.layout_file}',\n" \
                                       f"    textAlign: TextAlign.center,\n" \
                                       f"    style: TextStyle(color: Colors.red[600])),\n" \
                                       f"]))"
                        else:
                            # レイアウトファイルが見つからない場合
                            body = f"Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                                   f"  Icon(Icons.info_outline, size: 48, color: Colors.grey),\n" \
                                   f"  SizedBox(height: 16),\n" \
                                   f"  Text('Fragment detected: {fragment_ir.fragment_class}\\nLayout file not found: {fragment_ir.layout_file}',\n" \
                                   f"    textAlign: TextAlign.center,\n" \
                                   f"    style: TextStyle(color: Colors.grey[600])),\n" \
                                   f"]))"
                    else:
                        # レイアウトファイル名が推測できなかった場合
                        body = f"Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                               f"  Icon(Icons.info_outline, size: 48, color: Colors.grey),\n" \
                               f"  SizedBox(height: 16),\n" \
                               f"  Text('Fragment detected: {fragment_ir.fragment_class}\\nCould not guess layout file name.',\n" \
                               f"    textAlign: TextAlign.center,\n" \
                               f"    style: TextStyle(color: Colors.grey[600])),\n" \
                               f"]))"
                else:
                    # Fragmentが検出されなかった場合の既存の処理
                    body = "Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                           "  Icon(Icons.info_outline, size: 48, color: Colors.grey),\n" \
                           "  SizedBox(height: 16),\n" \
                           "  Text('Empty container detected.\\nThis may be a Fragment container.',\n" \
                           "    textAlign: TextAlign.center,\n" \
                           "    style: TextStyle(color: Colors.grey[600])),\n" \
                           "]))"
            else:
                # IDがない場合の既存の処理
                body = "Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                       "  Icon(Icons.info_outline, size: 48, color: Colors.grey),\n" \
                       "  SizedBox(height: 16),\n" \
                       "  Text('Empty container detected.\\nThis may be a Fragment container.',\n" \
                       "    textAlign: TextAlign.center,\n" \
                       "    style: TextStyle(color: Colors.grey[600])),\n" \
                       "]))"
        elif not dart_children:
            # Fragment検出が無効な場合のフォールバック
            body = "Center(child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [\n" \
                   "  Icon(Icons.info_outline, size: 48, color: Colors.grey),\n" \
                   "  SizedBox(height: 16),\n" \
                   "  Text('Empty container detected.\\nThis may be a Fragment container.',\n" \
                   "    textAlign: TextAlign.center,\n" \
                   "    style: TextStyle(color: Colors.grey[600])),\n" \
                   "]))"
        else:
            body = f"Stack(children: [\n{indent(',\n'.join(dart_children))}\n])"
            # ========== RelativeLayout ==========
        return apply_layout_modifiers(body, attrs, resolver)
    if t == "RelativeLayout":
        # RelativeLayoutをColumn構造に変換（layout_belowを処理）
        # layout_belowがある場合はColumn、ない場合はStackを使用
        has_layout_below = any(ch.get("attrs", {}).get("layout_below") for ch in children)
        
        if has_layout_below:
            # layout_belowがある場合、Column構造に変換
            body = _convert_relative_layout_to_column(children, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
        else:
            # layout_belowがない場合、Stack構造に変換
            dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
            if dart_children:
                body = f"Stack(children: [\n{indent(',\n'.join(dart_children))}\n])"
            else:
                body = "SizedBox.shrink()"
        return apply_layout_modifiers(body, attrs, resolver)

    # ========== ConstraintLayout ==========
    if t == "ConstraintLayout":
        # android:background属性を背景画像として処理
        background_attr = attrs.get("background")
        has_background_attr = background_attr and not background_attr.startswith("#")
        
        # 背景画像を検出し、分類する
        bg_full, bg_top, bg_bottom, foreground = _get_background_images(children)
        
        # android:background属性がある場合、背景画像として扱う
        if has_background_attr and not bg_full:
            # 背景画像として扱う（実際の画像リソースは後で処理）
            bg_full = [{"type": "ImageView", "attrs": {"src": background_attr, "layout_width": "match_parent", "layout_height": "match_parent"}}]
            foreground = children
        
        if bg_full or bg_top or bg_bottom:
            # 背景画像がある場合、Stack構造を生成
            stack_children = []
            
            # 画面全体を覆う背景画像（1枚目のみ）
            if bg_full:
                if isinstance(bg_full[0], dict) and bg_full[0].get("type") == "ImageView":
                    # android:background属性から生成された背景画像
                    bg_attrs = bg_full[0].get("attrs", {})
                    src = bg_attrs.get("src", "")
                    if src.startswith("@drawable/") or src.startswith("@mipmap/"):
                        # リソース名を取得
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
            
            # 上の飾り画像
            for bg_img_node in bg_top:
                bg_image = translate_node(bg_img_node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                bg_attrs = bg_img_node.get("attrs", {}) or {}
                bg_image = _get_background_image_with_cover(bg_image, bg_attrs)
                stack_children.append(f"Positioned(top: 0, left: 0, right: 0, child: {bg_image})")
            
            # 下の飾り画像
            for bg_img_node in bg_bottom:
                bg_image = translate_node(bg_img_node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                bg_attrs = bg_img_node.get("attrs", {}) or {}
                bg_image = _get_background_image_with_cover(bg_image, bg_attrs)
                stack_children.append(f"Positioned(bottom: 0, left: 0, right: 0, child: {bg_image})")
            
            # 前景要素を処理（センタリングが必要な場合はCenterでラップ）
            foreground_widgets = []
            for ch in foreground:
                child_code = translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                child_attrs = ch.get("attrs", {}) or {}
                
                # ConstraintLayoutの子要素が上下左右すべてparentに制約されている場合、Centerでラップ
                if _is_centered_in_constraint(child_attrs):
                    v_bias, h_bias = _get_constraint_bias(child_attrs)
                    
                    if v_bias is not None or h_bias is not None:
                        # biasがある場合、Alignを使用
                        alignment_parts = []
                        if v_bias is not None:
                            # vertical_bias: 0.0=top, 0.5=center, 1.0=bottom
                            y = (v_bias - 0.5) * 2.0  # -1.0 to 1.0
                            alignment_parts.append(f"y: {y:.2f}")
                        if h_bias is not None:
                            # horizontal_bias: 0.0=start, 0.5=center, 1.0=end
                            x = (h_bias - 0.5) * 2.0  # -1.0 to 1.0
                            alignment_parts.append(f"x: {x:.2f}")
                        
                        if alignment_parts:
                            alignment_str = ", ".join(alignment_parts)
                            child_code = f"Align(alignment: Alignment({alignment_str}), child: {child_code})"
                        else:
                            child_code = f"Center(child: {child_code})"
                    else:
                        # biasがない場合、Centerでラップ
                        child_code = f"Center(child: {child_code})"
                
                foreground_widgets.append(child_code)
            
            stack_children.extend(foreground_widgets)
            
            if stack_children:
                # Stackを直接使用（SingleChildScrollViewの外に配置されるため、高さ制約は問題ない）
                body = (
                    f"Stack(children: [\n"
                    f"{indent(',\n'.join(stack_children))}\n"
                    f"])"
                )
            else:
                body = "SizedBox.shrink()"
        else:
            # 背景画像がない場合、通常のColumn構造
            # ただし、子要素がセンタリングされている場合は、Centerでラップ
            dart_children = []
            needs_center_wrap = False
            
            for ch in children:
                child_code = translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
                child_attrs = ch.get("attrs", {}) or {}
                
                # ConstraintLayoutの子要素が上下左右すべてparentに制約されている場合、Centerでラップ
                if _is_centered_in_constraint(child_attrs):
                    needs_center_wrap = True
                    v_bias, h_bias = _get_constraint_bias(child_attrs)
                    
                    if v_bias is not None or h_bias is not None:
                        # biasがある場合、Alignを使用
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
                # 子要素が1つでセンタリングされている場合、Centerでラップ
                body = f"Center(child: Column(mainAxisSize: MainAxisSize.min, mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.stretch, children: [\n{indent(dart_children[0])}\n]))"
            else:
                body = f"Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [\n{indent(',\n'.join(dart_children))}\n])"
        
        return apply_layout_modifiers(body, attrs, resolver)

    # fallback
    dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
    # SingleChildScrollView内ではmainAxisSizeを指定しない（デフォルトのmaxを使用）
    body = f"Column(children: [\n{indent(',\n'.join(dart_children))}\n])"
    return apply_layout_modifiers(body, attrs, resolver)

def translate_node(node: dict, resolver, logic_map=None, fragments_by_id=None, layout_dir=None, values_dir=None):
    t = (node.get("type") or "")
    attrs = node.get("attrs", {}) or {}
    children = node.get("children", []) or []

    # includeタグの処理（既にパースされているので、そのまま変換）
    if t == "include":
        # includeタグは既にパースされて、インクルードされたレイアウトのルート要素になっている
        # そのまま変換を続ける
        pass
    
    # === 追加: ConstraintLayout を Column にフォールバック ===
    if t in ("androidx.constraintlayout.widget.ConstraintLayout", "ConstraintLayout"):
        # translate_layout関数に委譲（同じ処理を共有）
        return translate_layout(node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
    
    if t in ("LinearLayout", "FrameLayout", "RelativeLayout", "ConstraintLayout", "ScrollView", "HorizontalScrollView", "NestedScrollView", "ListView", "TableLayout", "TableRow", "RadioGroup"):
        return translate_layout(node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
    # NestedScrollViewも処理（androidx.core.widget.NestedScrollViewなど）
    if t.endswith("NestedScrollView") or t.endswith("HorizontalScrollView"):
        return translate_layout(node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
    # カスタムViewや標準Viewはview_rulesに委譲（循環インポートを避けるため、ここでインポート）
    from translator.view_rules import translate_view
    return translate_view(node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
