"""
カスタムViewのタイプを判定し、変換可能な情報を抽出するモジュール
"""
import re
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass


@dataclass
class CustomViewInfo:
    """カスタムViewの情報"""
    class_name: str  # クラス名（例: "LinkageWheelLayout"）
    full_class_name: str  # 完全修飾名（例: "com.github.gzuliyujiang.wheelpicker.widget.LinkageWheelLayout"）
    parent_class: str  # 親クラス（例: "LinearLayout", "View", "AppCompatImageView"）
    view_type: str  # "TYPE_A", "TYPE_B", "TYPE_C", "UNKNOWN"
    has_on_draw: bool  # onDraw()をoverrideしているか
    has_inflate: bool  # inflate()を使用しているか
    layout_file: Optional[str] = None  # inflate()で使用されているレイアウトファイル
    methods: List[str] = None  # 公開メソッドのリスト


def analyze_custom_view_class(java_file_path: str) -> Optional[CustomViewInfo]:
    """
    JavaファイルからカスタムViewの情報を抽出
    
    Returns:
        CustomViewInfo または None（カスタムViewでない場合）
    """
    try:
        content = Path(java_file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    
    # クラス定義を検出（複数行にまたがる可能性を考慮）
    class_patterns = [
        r'public\s+(?:abstract\s+)?class\s+(\w+)\s+extends\s+(\w+(?:\.\w+)*)',
        r'class\s+(\w+)\s+extends\s+(\w+(?:\.\w+)*)',  # publicがない場合
    ]
    
    class_match = None
    for pattern in class_patterns:
        class_match = re.search(pattern, content, re.MULTILINE)
        if class_match:
            break
    
    if not class_match:
        return None
    
    class_name = class_match.group(1)
    parent_class_full = class_match.group(2)
    parent_class = parent_class_full.split('.')[-1]  # パッケージ名を除く
    
    # View系のクラスを継承しているかチェック
    # 直接継承または間接継承（BaseWheelLayout → LinearLayout など）を考慮
    view_classes = {
        "View", "ViewGroup", "LinearLayout", "RelativeLayout", "ConstraintLayout",
        "FrameLayout", "TextView", "ImageView", "Button", "EditText",
        "AppCompatImageView", "AppCompatTextView", "AppCompatButton",
        "MaterialButton", "RecyclerView", "ScrollView", "NestedScrollView",
        "CardView", "MaterialCardView", "BaseWheelLayout"  # カスタム基底クラスも含める
    }
    
    # 親クラスがView系かチェック（直接継承または間接継承）
    is_view_class = (
        parent_class in view_classes or
        any(vc in parent_class_full for vc in view_classes) or
        "View" in parent_class or
        "Layout" in parent_class
    )
    
    if not is_view_class:
        return None
    
    # パッケージ名を抽出
    package_match = re.search(r'package\s+([\w\.]+);', content)
    package_name = package_match.group(1) if package_match else ""
    full_class_name = f"{package_name}.{class_name}" if package_name else class_name
    
    # onDraw()をoverrideしているかチェック
    has_on_draw = bool(re.search(
        r'@Override\s+protected\s+void\s+onDraw\s*\([^)]*Canvas\s+\w+\)',
        content,
        re.MULTILINE
    ))
    
    # inflate()を使用しているかチェック
    has_inflate = bool(re.search(
        r'LayoutInflater.*inflate\s*\([^)]*R\.layout\.(\w+)',
        content
    ))
    
    # inflate()で使用されているレイアウトファイルを抽出
    layout_file = None
    if has_inflate:
        inflate_match = re.search(
            r'inflate\s*\([^)]*R\.layout\.(\w+)',
            content
        )
        if inflate_match:
            layout_file = inflate_match.group(1)
    
    # タイプを判定
    view_type = _determine_view_type(parent_class, has_on_draw, has_inflate, content)
    
    # 公開メソッドを抽出
    methods = _extract_public_methods(content)
    
    return CustomViewInfo(
        class_name=class_name,
        full_class_name=full_class_name,
        parent_class=parent_class,
        view_type=view_type,
        has_on_draw=has_on_draw,
        has_inflate=has_inflate,
        layout_file=layout_file,
        methods=methods
    )


def _determine_view_type(
    parent_class: str,
    has_on_draw: bool,
    has_inflate: bool,
    content: str
) -> str:
    """
    カスタムViewのタイプを判定
    
    TYPE_A: 標準UI拡張（標準Viewを継承し、onDraw()をoverrideしていない）
    TYPE_B: 複合View（LinearLayoutなどを継承し、inflate()を使用）
    TYPE_C: 純粋なカスタム描画（onDraw()をoverride、またはViewを直接継承）
    """
    # TYPE_C: onDraw()をoverrideしている、またはViewを直接継承
    if has_on_draw or parent_class == "View":
        return "TYPE_C"
    
    # TYPE_B: LinearLayoutなどを継承し、inflate()を使用
    composite_classes = {
        "LinearLayout", "RelativeLayout", "ConstraintLayout",
        "FrameLayout", "ViewGroup", "BaseWheelLayout"  # カスタム基底クラスも含める
    }
    if parent_class in composite_classes:
        if has_inflate:
            return "TYPE_B"
        # inflate()がなくても、Layout系のクラスはTYPE_Bとみなす
        return "TYPE_B"
    
    # TYPE_A: 標準Viewを継承（TextView, ImageView, Buttonなど）
    standard_view_classes = {
        "TextView", "ImageView", "Button", "EditText",
        "AppCompatImageView", "AppCompatTextView", "AppCompatButton",
        "MaterialButton", "CardView", "MaterialCardView"
    }
    if parent_class in standard_view_classes:
        return "TYPE_A"
    
    # Layout系のクラス名を含む場合はTYPE_B
    if "Layout" in parent_class:
        return "TYPE_B"
    
    return "UNKNOWN"


def _extract_public_methods(content: str) -> List[str]:
    """公開メソッドの名前を抽出"""
    methods = []
    # public メソッドを検出
    method_pattern = re.compile(
        r'public\s+(?:static\s+)?(?:[\w<>]+\s+)?(\w+)\s*\([^)]*\)',
        re.MULTILINE
    )
    for match in method_pattern.finditer(content):
        method_name = match.group(1)
        # コンストラクタやgetter/setterを除外
        if method_name not in ["getClass", "equals", "hashCode", "toString"]:
            methods.append(method_name)
    return list(set(methods))  # 重複を除去


def find_custom_views_in_project(java_root: str) -> Dict[str, CustomViewInfo]:
    """
    プロジェクト内の全てのカスタムViewを検出
    
    Returns:
        {full_class_name: CustomViewInfo} の辞書
    """
    custom_views = {}
    root = Path(java_root)
    
    for java_file in root.rglob("*.java"):
        info = analyze_custom_view_class(str(java_file))
        if info:
            custom_views[info.full_class_name] = info
    
    return custom_views


def get_custom_view_info(class_name: str, java_root: str) -> Optional[CustomViewInfo]:
    """
    クラス名からカスタムViewの情報を取得
    
    Args:
        class_name: クラス名（例: "LinkageWheelLayout" または完全修飾名）
        java_root: Javaソースのルートディレクトリ
    
    Returns:
        CustomViewInfo または None
    """
    # まず、プロジェクト内の全てのカスタムViewを検出
    all_views = find_custom_views_in_project(java_root)
    
    # 完全修飾名で検索
    if class_name in all_views:
        return all_views[class_name]
    
    # クラス名のみで検索
    for full_name, info in all_views.items():
        if info.class_name == class_name:
            return info
    
    return None
