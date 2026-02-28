# java2flutter/parser/xml_parser.py
from __future__ import annotations
from lxml import etree
from .resource_resolver import ResourceResolver

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
APP_NS = "{http://schemas.android.com/apk/res-auto}"


def _convert_node(el):
    if isinstance(el, etree._Comment):
        return {
            "type": "comment",
            "text": str(el),
            "attrs": {},
            "children": [],
        }

    if isinstance(el, etree._ProcessingInstruction):
        return {
            "type": "pi",
            "target": el.target,
            "text": el.text,
            "attrs": {},
            "children": [],
        }

    if isinstance(el, etree._Element):
        node = {
            "type": el.tag.split('}')[-1],  
            "raw_tag": el.tag,              
            "nsmap": dict(el.nsmap) if hasattr(el, "nsmap") else None,
            "attrs": {},
            "text": el.text,
            "tail": el.tail,
            "children": []
        }

        for k, v in el.attrib.items():
            if "}" in k:
                ns, local = k.split("}")
                ns += "}"
                if ns == ANDROID_NS:
                    node["attrs"][local] = v
                elif ns == APP_NS:
                    node["attrs"][local] = v
                    if local == "srcCompat":
                        node["attrs"]["src"] = v  
                else:
                    node["attrs"][f"{ns}{local}"] = v
            else:
                node["attrs"][k] = v

        for child in el.iterchildren():
            node["children"].append(_convert_node(child))

        return node

    raise TypeError(f"Unsupported node type: {type(el)}")


def parse_layout_xml(xml_path, values_dir=None):

    parser = etree.XMLParser(
        remove_blank_text=False,
        recover=False,
        huge_tree=True
    )

    tree = etree.parse(xml_path, parser)
    root = tree.getroot()

    children = []

    before = root.getprevious()
    before_list = []
    while before is not None:
        before_list.append(_convert_node(before))
        before = before.getprevious()
    children.extend(reversed(before_list))

    children.append(_convert_node(root))

    after = root.getnext()
    while after is not None:
        children.append(_convert_node(after))
        after = after.getnext()

    document_ir = {
        "type": "document",
        "children": children
    }

    resolver = ResourceResolver(values_dir) if values_dir else None

    return document_ir, resolver