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
    else:  # horizontal
        if w == "match_parent":
            code = f"Expanded(child: {code})"
        # Expandedでラップした後は、SizedBoxでラップしない
        if h == "match_parent" and "Expanded" not in code:
            # match_parentの意図は「親の高さいっぱい」なので、double.infinityを使用（親の制約に従う）
            # MediaQueryを使うとRow内で問題が起こるため、double.infinityに戻す
            code = f"SizedBox(height: double.infinity, child: {code})"
    return code

def _axes_from_gravity_for_linear(gravity: str, orientation: str):
    """gravity を Flutter の main/cross axis に落とす。シンプルに center/horizontal/vertical を扱う。
    
    注意: MainAxisAlignment.center は SingleChildScrollView と衝突する可能性があるため、
    スクロール可能なコンテンツでは start を使用する。
    """
    g = (gravity or "").lower()
    main = "MainAxisAlignment.start"
    # crossAxisAlignment は stretch をデフォルトにする（TextField などの幅を広げるため）
    cross = "CrossAxisAlignment.stretch"

    # MainAxisAlignment.center は SingleChildScrollView と衝突するため、start を使用
    # 中央寄せが必要な場合は、個別の要素を Center でラップする
    if "center" in g:
        if orientation == "vertical":
            # main は start のまま（スクロールビューとの互換性のため）
            # cross は stretch のまま（TextField などの幅を広げるため）
            # 個別の要素（ロゴなど）を Center でラップする必要がある場合は、後で処理
            pass
        else:
            # horizontal の場合
            if "center_horizontal" in g or "center" in g:
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

    
        # ========== ScrollView ==========
    if t == "ScrollView":
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
        main, cross = _axes_from_gravity_for_linear(attrs.get("gravity", ""), orientation)

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
        dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
        body = f"Stack(children: [\n{indent(',\n'.join(dart_children))}\n])"
        return apply_layout_modifiers(body, attrs, resolver)

    # ========== ConstraintLayout ==========
    if t == "ConstraintLayout":
        # 背景画像を検出し、分類する
        bg_full, bg_top, bg_bottom, foreground = _get_background_images(children)
        
        if bg_full or bg_top or bg_bottom:
            # 背景画像がある場合、Stack構造を生成
            stack_children = []
            
            # 画面全体を覆う背景画像（1枚目のみ）
            if bg_full:
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
            
            # 前景要素
            foreground_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in foreground]
            stack_children.extend(foreground_children)
            
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
            dart_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
            # SingleChildScrollView内ではmainAxisSizeを指定しない（デフォルトのmaxを使用）
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

    # === 追加: ConstraintLayout を Column にフォールバック ===
    if t in ("androidx.constraintlayout.widget.ConstraintLayout", "ConstraintLayout"):
        # 背景画像を検出し、分類する
        bg_full, bg_top, bg_bottom, foreground = _get_background_images(children)
        
        if bg_full or bg_top or bg_bottom:
            # 背景画像がある場合、Stack構造を生成
            stack_children = []
            
            # 画面全体を覆う背景画像（1枚目のみ）
            if bg_full:
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
            
            # 前景要素
            foreground_children = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in foreground]
            stack_children.extend(foreground_children)
            
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
            child_widgets = [translate_node(ch, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir) for ch in children]
            # SingleChildScrollView内ではmainAxisSizeを指定しない（デフォルトのmaxを使用）
            body = "Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [\n  " \
               + ",\n  ".join(child_widgets) + "\n])"
        return apply_layout_modifiers(body, attrs, resolver)
    
    if t in ("LinearLayout", "FrameLayout", "RelativeLayout", "ConstraintLayout", "ScrollView", "TableLayout", "TableRow", "RadioGroup"):
        return translate_layout(node, resolver, logic_map=logic_map, fragments_by_id=fragments_by_id, layout_dir=layout_dir, values_dir=values_dir)
    from translator.view_rules import translate_view
    return translate_view(node, resolver, logic_map)
