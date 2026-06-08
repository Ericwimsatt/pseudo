"""
project_to_pseudo.py

Convert all Python files in a project directory to pseudocode HTML,
mirroring the folder structure under pseudo's mirrors/ directory.

Usage:
    python project_to_pseudo.py path/to/project_dir

Output:
    mirrors/<project_dir_name>/<relative_path>.html

The CSS path in each HTML file is adjusted to point back to
mirrors/pseudo.css relative to the output file's depth.
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from file_to_pseudo import _parse, _convert, _escape, _wrap_kw, MIRRORS_DIR


# ---------------------------------------------------------------------------
# HTML writer (CSS-path aware)
# ---------------------------------------------------------------------------

def _to_html(lines: list, source_name: str, css_rel_path: str) -> str:
    """Convert (depth, text, css_class) tuples to HTML with a custom CSS path."""
    parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        f"<title>Pseudocode: {source_name}</title>",
        f'<link rel="stylesheet" href="{css_rel_path}">',
        "</head>",
        "<body>",
    ]
    for depth, text, css_class in lines:
        indent = "&nbsp;&nbsp;&nbsp;&nbsp;" * depth
        escaped = _wrap_kw(_escape(text), css_class)
        parts.append(f'<p class="{css_class}">{indent}{escaped}</p>')
    parts += ["</body>", "</html>"]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Project converter
# ---------------------------------------------------------------------------

def convert_project(project_path: str) -> None:
    """
    Convert all .py files in project_path to pseudocode HTML.

    The output mirrors the source folder structure under:
        mirrors/<project_dir_name>/

    Args:
        project_path: Path to the root of the project directory.
    """
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

        # Compute relative path from the output file back to mirrors/ for CSS.
        # e.g. mirrors/proj/sub/file.html  →  depth=2  →  ../../pseudo.css
        depth = len(output_path.relative_to(MIRRORS_DIR).parts) - 1
        css_rel_path = ("../" * depth) + "pseudo.css"

        src_bytes = src_path.read_bytes()
        tree = _parse(src_bytes)
        lines: list = []
        _convert(tree.root_node, 0, lines)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            _to_html(lines, src_path.name, css_rel_path),
            encoding="utf-8",
        )
        print(f"  {rel_path}  →  {output_path.relative_to(MIRRORS_DIR.parent)}")

    print(f"\nDone.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python project_to_pseudo.py <path/to/project_dir>")
        sys.exit(1)
    convert_project(sys.argv[1])
