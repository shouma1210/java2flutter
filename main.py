# java2flutter/main.py
import argparse
from .translator.generator import generate_dart_code


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--xml",
        required=True,
    )
    parser.add_argument(
        "--values",
        required=False,
    )
    parser.add_argument(
        "--java",
        required=False,
    )
    parser.add_argument(
        "--out-path",
        required=True,
    )
    parser.add_argument(
        "--class-name",
        required=True,
    )

    args = parser.parse_args()

    generate_dart_code(
        xml_path=args.xml,
        values_dir=args.values,
        java_root=args.java,
        output_path=args.out_path,
        class_name=args.class_name,
    )


if __name__ == "__main__":
    main()
