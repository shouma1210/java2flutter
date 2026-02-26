
from lxml import etree
from .resource_resolver import ResourceResolver

import os


ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
APP_NS = "{http://schemas.android.com/apk/res-auto}"

def _attr(el, name, default=None):
    return el.get(ANDROID_NS + name, default)

def _parse_node(el):
    node = {
        "type": el.tag.split('}')[-1],  
        "attrs": {},
        "children": []
    }
   
    for k, v in el.attrib.items():
        if k.startswith(ANDROID_NS):
            node["attrs"][k.split('}')[-1]] = v
        elif k.startswith(APP_NS):
           
            attr_name = k.split('}')[-1]
           
            node["attrs"][attr_name] = v
           
            if attr_name == "srcCompat":
                node["attrs"]["src"] = v
 
    for child in el:
        if isinstance(child.tag, str):  
            node["children"].append(_parse_node(child))
    return node

def parse_layout_xml(xml_path, values_dir=None):

    tree = etree.parse(xml_path)
    root = tree.getroot()
    ir = _parse_node(root)
    resolver = ResourceResolver(values_dir) if values_dir else None
    return ir, resolver
