# java2flutter/main.py
from __future__ import annotations

import argparse
from .translator.generator import generate_dart_code


def main() -> None:
    parser = argparse.ArgumentParser(description="Java/XML → Flutter UI converter")
    parser.add_argument(
        "--xml",
        required=True,
        help="Path to main layout XML (e.g., app/src/main/res/layout/activity_main.xml)",
    )
    parser.add_argument(
        "--values",
        required=False,
        help="Path to res/values directory (e.g., app/src/main/res/values)",
    )
    parser.add_argument(
        "--java",
        required=False,
        help="Path to Java source root (e.g., app/src/main/java)",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output Dart file path (e.g., Converted/converted_main.dart)",
    )
    parser.add_argument(
        "--class",
        dest="class_name",
        required=True,
        help="Dart class name to generate (e.g., ConvertedMain)",
    )

    args = parser.parse_args()

    generate_dart_code(
        xml_path=args.xml,
        values_dir=args.values,
        java_root=args.java,
        output_path=args.out,
        class_name=args.class_name,
    )


if __name__ == "__main__":
    main()
