import os
from lxml import etree

class ResourceResolver:
    def __init__(self, values_dir):
        self.colors = {}
        self.strings = {}
        self.dimens = {}
        self.drawables = {} 
        if values_dir and os.path.isdir(values_dir):
            self._load_values(values_dir)
            self._load_drawables(values_dir)

    def _load_values(self, values_dir, is_main_values=True):
    
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
                  
                    if name not in self.colors:
                        self.colors[name] = text
                elif tag == "string":
                 
                    if name not in self.strings:
                        self.strings[name] = text
                elif tag == "dimen":
                 
                    if name not in self.dimens:
                        self.dimens[name] = text
        
       
        if is_main_values:
            res_dir = os.path.dirname(values_dir) if os.path.isdir(values_dir) else None
            if res_dir:
              
                color_dir = os.path.join(res_dir, "color")
                if os.path.isdir(color_dir):
                    self._load_color_resources(color_dir)
                
           
                values_night_dir = os.path.join(res_dir, "values-night")
                if os.path.isdir(values_night_dir):
                    self._load_values(values_night_dir, is_main_values=False)
    
    def _load_color_resources(self, color_dir):
  
        for fn in os.listdir(color_dir):
            if not fn.endswith(".xml"): continue
            path = os.path.join(color_dir, fn)
            try:
                tree = etree.parse(path)
                root = tree.getroot()
              
                name_without_ext = os.path.splitext(fn)[0]
                
                
                if root.tag == "selector" or root.tag.endswith("}selector"):
                   
                    items = root.findall(".//item") + root.findall(".//{http://schemas.android.com/apk/res/android}item")
                   
                    default_item = None
                    for item in items:
                        state_checked = item.get("{http://schemas.android.com/apk/res/android}state_checked")
                        if state_checked is None:
                            default_item = item
                            break
                    
                 
                    target_item = default_item if default_item is not None else (items[0] if len(items) > 0 else None)
                    
                    if target_item is not None:
                        color_attr = target_item.get("{http://schemas.android.com/apk/res/android}color")
                        if color_attr:
                      
                            if color_attr.startswith("@color/"):
                                ref_key = color_attr.split("/", 1)[1]
                                if ref_key in self.colors:
                                    self.colors[name_without_ext] = self.colors[ref_key]
                                else:
                                
                                    self.colors[name_without_ext] = color_attr
                            else:
                              
                                self.colors[name_without_ext] = color_attr
            except Exception:
                continue

    def _load_drawables(self, values_dir):
       
      
        res_dir = os.path.dirname(values_dir) if os.path.isdir(values_dir) else None
        if not res_dir:
            return
        
     
        drawable_dirs = []
        if os.path.isdir(res_dir):
            for item in os.listdir(res_dir):
                if item.startswith("drawable"):
                    drawable_path = os.path.join(res_dir, item)
                    if os.path.isdir(drawable_path):
                        drawable_dirs.append(drawable_path)
        
        
        image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        for drawable_dir in drawable_dirs:
            try:
                for filename in os.listdir(drawable_dir):
                    name_without_ext = os.path.splitext(filename)[0]
                    file_path = os.path.join(drawable_dir, filename)
                    if os.path.isfile(file_path):
                     
                        if (any(filename.lower().endswith(ext) for ext in image_extensions) or 
                            filename.lower().endswith(".xml")):
                        
                            if name_without_ext not in self.drawables:
                                self.drawables[name_without_ext] = file_path
            except Exception:
                continue

    def resolve(self, val):
     
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
       
        if not isinstance(val, str):
            return None
        if val.startswith("@drawable/"):
            key = val.split("/", 1)[1]
            return self.drawables.get(key)
        return None

    @staticmethod
    def parse_dimen_to_px(d):
     
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
  
        if not isinstance(c, str): return None
        s = c.strip()
        if not s.startswith("#"): return None
        hexv = s[1:]
        if len(hexv) == 6:  
            return "0xFF" + hexv.upper()
        if len(hexv) == 8: 
            return "0x" + hexv.upper()
        return None
