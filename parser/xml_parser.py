# convert_tool/parser/xml_parser.py
from lxml import etree
from .resource_resolver import ResourceResolver

import os


ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
APP_NS = "{http://schemas.android.com/apk/res-auto}"

def _attr(el, name, default=None):
    return el.get(ANDROID_NS + name, default)

def _parse_node(el):
    node = {
        "type": el.tag.split('}')[-1],   # e.g., LinearLayout / TextView
        "attrs": {},
        "children": []
    }
    # すべてのandroid:属性とapp:属性を attrs に詰める
    for k, v in el.attrib.items():
        if k.startswith(ANDROID_NS):
            node["attrs"][k.split('}')[-1]] = v
        elif k.startswith(APP_NS):
            # app:srcCompat など app: 名前空間の属性も取得
            attr_name = k.split('}')[-1]
            # app:srcCompat を srcCompat として保存（後で src としても参照可能にする）
            node["attrs"][attr_name] = v
            # srcCompat の場合は src としても登録（後方互換性のため）
            if attr_name == "srcCompat":
                node["attrs"]["src"] = v
    # 子
    for child in el:
        if isinstance(child.tag, str):  # コメント等スキップ
            node["children"].append(_parse_node(child))
    return node

def parse_layout_xml(xml_path, values_dir=None):
    """
    xml_path: res/layout/xxx.xml
    values_dir: res/values ディレクトリ
    return: (ir: dict, resolver: ResourceResolver)
    """
    tree = etree.parse(xml_path)
    root = tree.getroot()
    ir = _parse_node(root)
    resolver = ResourceResolver(values_dir) if values_dir else None
    return ir, resolver
