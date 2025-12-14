# convert_tool/parser/resource_resolver.py
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

    def _load_values(self, values_dir):
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
                    self.colors[name] = text
                elif tag == "string":
                    self.strings[name] = text
                elif tag == "dimen":
                    # "16dp" / "14sp" 等
                    self.dimens[name] = text

    def _load_drawables(self, values_dir):
        """drawableディレクトリから画像ファイルを検索して登録"""
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
        
        # 各drawableディレクトリから画像ファイルを収集
        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        for drawable_dir in drawable_dirs:
            try:
                for filename in os.listdir(drawable_dir):
                    name_without_ext = os.path.splitext(filename)[0]
                    file_path = os.path.join(drawable_dir, filename)
                    if os.path.isfile(file_path) and any(filename.lower().endswith(ext) for ext in image_extensions):
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
