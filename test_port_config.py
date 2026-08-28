"""Тест: проверка что порт в Dockerfile и render.yaml совпадают.

Запуск: python test_port_config.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def _extract_port_from_dockerfile() -> str | None:
    path = BASE / "Dockerfile"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    m = re.search(r"PORT=(\d+)", text)
    return m.group(1) if m else None


def _extract_port_from_render_yaml() -> str | None:
    path = BASE / "render.yaml"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    # Ищем ключ PORT с последующим значением
    m = re.search(r"key:\s*PORT\s*\n\s*value:\s*\"(\d+)\"", text)
    return m.group(1) if m else None


def main() -> int:
    dockerfile_port = _extract_port_from_dockerfile()
    render_port = _extract_port_from_render_yaml()

    errors = []

    if dockerfile_port is None:
        errors.append("Dockerfile: PORT not found")
    else:
        print(f"Dockerfile PORT: {dockerfile_port}")

    if render_port is None:
        errors.append("render.yaml: PORT not found")
    else:
        print(f"render.yaml PORT: {render_port}")

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        return 1

    if dockerfile_port != render_port:
        print(f"FAIL: port mismatch — Dockerfile={dockerfile_port} render.yaml={render_port}")
        return 1

    # Render стандартный порт для Docker = 10000
    if dockerfile_port != "10000":
        print(f"WARNING: port is {dockerfile_port}, Render expects 10000 for Docker services")
        print("This may cause health check failures")
        return 1

    print(f"OK: ports match and equal to Render standard (10000)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
