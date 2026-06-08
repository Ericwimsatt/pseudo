"""
project_to_pseudo.py

Convert all Python files in a project directory to interactive pseudocode HTML,
mirroring the folder structure under pseudo's mirrors/ directory.

Usage:
    python project_to_pseudo.py path/to/project_dir

Output:
    mirrors/<project_dir_name>/<relative_path>.html
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from file_to_pseudo import _parse, _convert, _to_html, MIRRORS_DIR


def _to_html_css(lines: list, source_name: str, source_path: str, css_rel_path: str) -> str:
    return _to_html(lines, source_name, source_path, css_rel_path)


def convert_project(project_path: str) -> None:
    project_dir = Path(project_path).resolve()
    if not project_dir.is_dir():
        raise ValueError(f"Not a directory: {project_dir}")

    output_base = MIRRORS_DIR / project_dir.name
    py_files = sorted(project_dir.rglob("*.py"))

    if not py_files:
        print(f"No .py files found in {project_dir}")
        return

    print(f"Converting {len(py_files)} file(s) from: {project_dir}")
    print(f"Output root: {output_base}\n")

    for src_path in py_files:
        rel_path = src_path.relative_to(project_dir)
        output_path = output_base / rel_path.with_suffix(".html")

        depth = len(output_path.relative_to(MIRRORS_DIR).parts) - 1
        css_rel_path = ("../" * depth) + "pseudo.css"

        src_bytes = src_path.read_bytes()
        tree = _parse(src_bytes)
        lines: list = []
        _convert(tree.root_node, 0, lines)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            _to_html_css(lines, src_path.name, str(project_dir / rel_path), css_rel_path),
            encoding="utf-8",
        )
        print(f"  {rel_path}  →  {output_path.relative_to(MIRRORS_DIR.parent)}")

    print(f"\nDone.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python project_to_pseudo.py <path/to/project_dir>")
        sys.exit(1)
    convert_project(sys.argv[1])
