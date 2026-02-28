import os
import re
from dataclasses import dataclass
from typing import Optional, Dict


@dataclass
class CustomViewInfo:
    view_type: str
    parent_class: str
    layout_file: Optional[str] = None


def _read_java_source(java_root: str, full_class_name: str) -> Optional[str]:

    if not java_root or not os.path.isdir(java_root) or not full_class_name:
        return None

    parts = full_class_name.split(".")
    if len(parts) < 2:
        candidate = os.path.join(java_root, f"{full_class_name}.java")
        return _safe_read(candidate)

    pkg_path = os.path.join(java_root, *parts[:-1])
    candidate = os.path.join(pkg_path, f"{parts[-1]}.java")
    return _safe_read(candidate)


def _safe_read(path: str) -> Optional[str]:

    try:
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
    except Exception:
        return None
    return None


def _guess_parent_class(src: str) -> Optional[str]:

    if not src:
        return None

    m = re.search(r"class\s+\w+\s+extends\s+([A-Za-z0-9_\.]+)", src)
    if not m:
        return None
    return m.group(1)


def _classify_view_type(parent_class: str, src: str) -> str:

    base = parent_class.lower()
    if "textview" in base or "edittext" in base:
        return "TYPE_A"
    if "imageview" in base:
        return "TYPE_A"
    if "button" in base or "floatingactionbutton" in base:
        return "TYPE_A"

    if "linearlayout" in base or "framelayout" in base or "relativelayout" in base:
        return "TYPE_B"

    if "viewpager" in base or "recyclerview" in base:
        return "TYPE_C"

    if "ondraw" in src.lower():
        return "TYPE_C"

    return "TYPE_A"


def _guess_layout_file(src: str) -> Optional[str]:

    if not src:
        return None

    m = re.search(r"R\.layout\.([a-zA-Z0-9_]+)", src)
    if not m:
        return None
    return f"{m.group(1)}.xml"


def get_custom_view_info(full_class_name: str, java_root: str) -> Optional[CustomViewInfo]:

    src = _read_java_source(java_root, full_class_name)
    if not src:
        return None

    parent = _guess_parent_class(src) or "View"
    view_type = _classify_view_type(parent, src)
    layout_file = _guess_layout_file(src)

    return CustomViewInfo(view_type=view_type, parent_class=parent, layout_file=layout_file)


def find_custom_views_in_project(java_root: str) -> Dict[str, CustomViewInfo]:

    results: Dict[str, CustomViewInfo] = {}

    if not java_root or not os.path.isdir(java_root):
        return results

    for root, _, files in os.walk(java_root):
        for fn in files:
            if not fn.endswith(".java"):
                continue
            path = os.path.join(root, fn)
            src = _safe_read(path)
            if not src:
                continue

            m = re.search(r"package\s+([a-zA-Z0-9_\.]+)\s*;", src)
            if m:
                pkg = m.group(1)
                full_name = f"{pkg}.{fn[:-5]}"
            else:
                full_name = fn[:-5]

            parent = _guess_parent_class(src)
            if not parent:
                continue

            view_type = _classify_view_type(parent, src)
            layout_file = _guess_layout_file(src)

            results[full_name] = CustomViewInfo(
                view_type=view_type,
                parent_class=parent,
                layout_file=layout_file,
            )

    return results

