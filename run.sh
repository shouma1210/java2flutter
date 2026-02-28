#!/bin/bash
# java2flutter を ~/java2flutter にいる状態で python3 -m java2flutter.main として実行するためのラッパー
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="${SCRIPT_DIR}/.."
exec python3 -m java2flutter.main "$@"
