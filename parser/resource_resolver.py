import os
from lxml import etree

class ResourceResolver:
    def __init__(self, values_dir):
        self.colors = {}
        self.strings = {}
        self.dimens = {}
        self.drawables = {}  # drawableリソース名 → ファイルパス
        if values_dir and os.path.isdir(values_dir):
            self._load_values(values_dir)
            self._load_drawables(values_dir)

    def _load_values(self, values_dir, is_main_values=True):
        """valuesディレクトリからリソースを読み込む
        
        Args:
            values_dir: valuesディレクトリのパス
            is_main_values: メインのvaluesディレクトリかどうか（Trueの場合のみcolor/とvalues-night/を読み込む）
        """
        for fn in os.listdir(values_dir):
            if not fn.endswith(".xml"): continue
            path = os.path.join(values_dir, fn)
            try:
                root = etree.parse(path).getroot()
            except Exception:
                continue
            for child in root:
                tag = child.tag
                name = child.get("name")
                if not name: continue
                text = (child.text or "").strip()
                if tag == "color":
                    # #AARRGGBB / #RRGGBB のどちらでも来る想定
                    # 既に存在する場合は上書きしない（メインのvaluesが優先）
                    if name not in self.colors:
                        self.colors[name] = text
                elif tag == "string":
                    # 既に存在する場合は上書きしない（メインのvaluesが優先）
                    if name not in self.strings:
                        self.strings[name] = text
                elif tag == "dimen":
                    # "16dp" / "14sp" 等
                    # 既に存在する場合は上書きしない（メインのvaluesが優先）
                    if name not in self.dimens:
                        self.dimens[name] = text
        
        # メインのvaluesディレクトリの場合のみ、追加のリソースを読み込む
        if is_main_values:
            res_dir = os.path.dirname(values_dir) if os.path.isdir(values_dir) else None
            if res_dir:
                # res/color/ ディレクトリからも色リソースを読み込む（selectorなど）
                color_dir = os.path.join(res_dir, "color")
                if os.path.isdir(color_dir):
                    self._load_color_resources(color_dir)
                
                # res/values-night/ ディレクトリからも色リソースを読み込む（ダークモード）
                values_night_dir = os.path.join(res_dir, "values-night")
                if os.path.isdir(values_night_dir):
                    self._load_values(values_night_dir, is_main_values=False)
    
    def _load_color_resources(self, color_dir):
        """res/color/ ディレクトリから色リソースXML（selectorなど）を読み込む"""
        for fn in os.listdir(color_dir):
            if not fn.endswith(".xml"): continue
            path = os.path.join(color_dir, fn)
            try:
                tree = etree.parse(path)
                root = tree.getroot()
                # ファイル名（拡張子なし）をリソース名として使用
                name_without_ext = os.path.splitext(fn)[0]
                
                # selector要素の場合、デフォルトの色を取得
                if root.tag == "selector" or root.tag.endswith("}selector"):
                    # item要素を探す（名前空間あり/なしの両方に対応）
                    items = root.findall(".//item") + root.findall(".//{http://schemas.android.com/apk/res/android}item")
                    # state_checkedがないitem（デフォルト）を優先的に探す
                    default_item = None
                    for item in items:
                        state_checked = item.get("{http://schemas.android.com/apk/res/android}state_checked")
                        if state_checked is None:
                            default_item = item
                            break
                    
                    # デフォルトのitemが見つからない場合は最初のitemを使用
                    target_item = default_item if default_item is not None else (items[0] if len(items) > 0 else None)
                    
                    if target_item is not None:
                        color_attr = target_item.get("{http://schemas.android.com/apk/res/android}color")
                        if color_attr:
                            # @color/xxx の参照を解決
                            if color_attr.startswith("@color/"):
                                ref_key = color_attr.split("/", 1)[1]
                                if ref_key in self.colors:
                                    self.colors[name_without_ext] = self.colors[ref_key]
                                else:
                                    # 参照先が見つからない場合は、参照をそのまま保存
                                    self.colors[name_without_ext] = color_attr
                            else:
                                # 直接色が指定されている場合
                                self.colors[name_without_ext] = color_attr
            except Exception:
                continue

    def _load_drawables(self, values_dir):
        """drawableディレクトリから画像ファイルとXML drawableを検索して登録"""
        # values_dir の親が res ディレクトリ
        res_dir = os.path.dirname(values_dir) if os.path.isdir(values_dir) else None
        if not res_dir:
            return
        
        # res/drawable ディレクトリを探す
        drawable_dirs = []
        if os.path.isdir(res_dir):
            for item in os.listdir(res_dir):
                if item.startswith("drawable"):
                    drawable_path = os.path.join(res_dir, item)
                    if os.path.isdir(drawable_path):
                        drawable_dirs.append(drawable_path)
        
        # 各drawableディレクトリから画像ファイルとXML drawableを収集
        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        for drawable_dir in drawable_dirs:
            try:
                for filename in os.listdir(drawable_dir):
                    name_without_ext = os.path.splitext(filename)[0]
                    file_path = os.path.join(drawable_dir, filename)
                    if os.path.isfile(file_path):
                        # 画像ファイルまたはXML drawableファイルを登録
                        if (any(filename.lower().endswith(ext) for ext in image_extensions) or 
                            filename.lower().endswith(".xml")):
                            # 最初に見つかったものを優先（通常はdrawable/が優先）
                            if name_without_ext not in self.drawables:
                                self.drawables[name_without_ext] = file_path
            except Exception:
                continue

    def resolve(self, val):
        """ @color/primary → #RRGGBB / @dimen/margin → '16dp' / @drawable/bg → ファイルパス ... """
        if not isinstance(val, str): return val
        if val.startswith("@color/"):
            key = val.split("/", 1)[1]
            return self.colors.get(key, val)
        if val.startswith("@string/"):
            key = val.split("/", 1)[1]
            return self.strings.get(key, val)
        if val.startswith("@dimen/"):
            key = val.split("/", 1)[1]
            return self.dimens.get(key, val)
        if val.startswith("@drawable/"):
            key = val.split("/", 1)[1]
            return self.drawables.get(key, val)
        return val
    
    def resolve_drawable_path(self, val):
        """@drawable/xxx を実際のファイルパスに解決。見つからない場合はNone"""
        if not isinstance(val, str):
            return None
        if val.startswith("@drawable/"):
            key = val.split("/", 1)[1]
            return self.drawables.get(key)
        return None

    @staticmethod
    def parse_dimen_to_px(d):
        """ '16dp' / '14sp' / '24px' -> float に。簡易：dp,sp → px 同値扱い（MVP）"""
        if not isinstance(d, str): return d
        s = d.strip().lower()
        for suf in ("dp", "sp", "px"):
            if s.endswith(suf):
                try:
                    return float(s[:-len(suf)])
                except:
                    return None
        try:
            return float(s)
        except:
            return None

    @staticmethod
    def android_color_to_flutter(c):
        """
        '#RRGGBB' or '#AARRGGBB' → '0xAARRGGBB'
        Flutterは ARGB hex を Color(0xAARRGGBB) で使う。
        """
        if not isinstance(c, str): return None
        s = c.strip()
        if not s.startswith("#"): return None
        hexv = s[1:]
        if len(hexv) == 6:  # RRGGBB
            return "0xFF" + hexv.upper()
        if len(hexv) == 8:  # AARRGGBB
            return "0x" + hexv.upper()
        return None
