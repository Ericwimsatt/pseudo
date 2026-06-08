"""
file_to_pseudo.py

Convert a Python source file to pseudocode and write it as an HTML file
in the mirrors/ folder (next to this script).

Usage:
    python file_to_pseudo.py path/to/file.py

Output:
    mirrors/<filename>.html

HTML contains one <p> tag per pseudocode line.  Indentation is represented
with &nbsp; entities (4 per depth level).  No CSS styling is added.

Transformations applied are documented in transformations.py.
"""

import re
import sys
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tspython.language())
MIRRORS_DIR = Path(__file__).parent / "mirrors"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _make_parser() -> Parser:
    return Parser(PY_LANGUAGE)


def _parse(src_bytes: bytes):
    return _make_parser().parse(src_bytes)


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------

def _text(node) -> str:
    """Return UTF-8 decoded text for a node."""
    return node.text.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Statement-level transformers
# Each returns a plain-text pseudocode string (no HTML).
# ---------------------------------------------------------------------------

def _for_header(node) -> str:
    """
    Transform a for_statement header.

    for _ in range(N)   →  do N times
    for x in range(N)   →  for x from 0 to N - 1
    for x in iterable   →  for each x in iterable
    """
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    left_text = _text(left) if left else "?"
    right_text = _text(right) if right else "?"

    # for _ in range(N)
    if left_text == "_" and right and right.type == "call":
        fn = right.child_by_field_name("function")
        args = right.child_by_field_name("arguments")
        if fn and _text(fn) == "range" and args:
            named = args.named_children
            if len(named) == 1:
                return f"do {{{_text(named[0])}}} times"

    # for x in range(N)
    if right and right.type == "call":
        fn = right.child_by_field_name("function")
        args = right.child_by_field_name("arguments")
        if fn and _text(fn) == "range" and args:
            named = args.named_children
            if len(named) == 1:
                n = _text(named[0])
                return f"for {{{left_text}}} from 0 to {{{n}}} - 1"

    # default
    return f"for each {{{left_text}}} in {{{right_text}}}"


def _function_header(node) -> str:
    """
    Transform a function_definition header.

    def name(params):  →  function name(params):
    """
    name = node.child_by_field_name("name")
    params = node.child_by_field_name("parameters")
    name_text = _text(name) if name else "?"

    param_parts = []
    if params:
        for child in params.named_children:
            t = child.type
            if t == "identifier":
                param_parts.append(_text(child))
            elif t in ("default_parameter", "typed_parameter", "typed_default_parameter"):
                n = child.child_by_field_name("name")
                if n:
                    param_parts.append(_text(n))
            elif t == "list_splat_pattern":
                inner = child.named_children[0] if child.named_children else None
                param_parts.append("*" + (_text(inner) if inner else "args"))
            elif t == "dictionary_splat_pattern":
                inner = child.named_children[0] if child.named_children else None
                param_parts.append("**" + (_text(inner) if inner else "kwargs"))

    return f"FUNCTION {name_text}({', '.join(param_parts)})"


def _assignment_line(node) -> str:
    """
    Transform an assignment.

    var = value  →  set var to value
    """
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    left_text = _text(left) if left else "?"
    right_text = _text(right) if right else "?"
    return f"set {left_text} to {right_text}"


def _augmented_assignment_line(node) -> str:
    """
    Transform an augmented assignment.

    var += expr   →  add expr to var
    var -= expr   →  subtract expr from var
    var *= expr   →  multiply var by expr
    var /= expr   →  divide var by expr
    var //= expr  →  floor-divide var by expr
    """
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    op_node = node.child_by_field_name("operator")

    left_text = _text(left) if left else "?"
    right_text = _text(right) if right else "?"

    # Fallback: search anonymous children for the operator token
    if op_node is None:
        for child in node.children:
            if child.type in ("+=", "-=", "*=", "/=", "//=", "%=", "**=", "&=", "|=", "^="):
                op_node = child
                break

    op = _text(op_node) if op_node else "?"

    ops = {
        "+=": f"add {right_text} to {left_text}",
        "-=": f"subtract {right_text} from {left_text}",
        "*=": f"multiply {left_text} by {right_text}",
        "/=": f"divide {left_text} by {right_text}",
        "//=": f"floor-divide {left_text} by {right_text}",
        "%=": f"{left_text} mod= {right_text}",
        "**=": f"raise {left_text} to the power of {right_text}",
    }
    return ops.get(op, f"{left_text} {op} {right_text}")


def _return_line(node) -> str:
    """
    Transform a return_statement.

    return value  →  return value
    """
    named = node.named_children
    if named:
        return f"return {_text(named[0])}"
    return "return"


# ---------------------------------------------------------------------------
# Main recursive converter
# Appends (depth: int, text: str) tuples to `out`.
# ---------------------------------------------------------------------------

def _convert(node, depth: int, out: list) -> None:
    t = node.type

    if t == "module":
        for child in node.named_children:
            _convert(child, depth, out)

    elif t == "block":
        for child in node.named_children:
            _convert(child, depth, out)

    elif t == "comment":
        # Strip leading '#' and whitespace
        text = _text(node)[1:].strip()
        out.append((depth, f"// {text}", "pseudo-comment"))

    elif t in ("import_statement", "import_from_statement"):
        pass  # skipped per transformations table

    elif t == "function_definition":
        out.append((depth, _function_header(node) + ":", "pseudo-function"))
        body = node.child_by_field_name("body")
        if body:
            _convert(body, depth + 1, out)

    elif t == "if_statement":
        cond = node.child_by_field_name("condition")
        out.append((depth, f"if {_text(cond) if cond else '?'}:", "pseudo-if"))
        consequence = node.child_by_field_name("consequence")
        if consequence:
            _convert(consequence, depth + 1, out)
        # elif and else are named_children of the if_statement
        for child in node.named_children:
            if child.type == "elif_clause":
                cond2 = child.child_by_field_name("condition")
                out.append((depth, f"else if {_text(cond2) if cond2 else '?'}:", "pseudo-if"))
                cons2 = child.child_by_field_name("consequence")
                if cons2:
                    _convert(cons2, depth + 1, out)
            elif child.type == "else_clause":
                out.append((depth, "otherwise:", "pseudo-if"))
                body = child.child_by_field_name("body")
                if body:
                    _convert(body, depth + 1, out)

    elif t == "for_statement":
        out.append((depth, _for_header(node) + ":", "pseudo-for"))
        body = node.child_by_field_name("body")
        if body:
            _convert(body, depth + 1, out)

    elif t == "while_statement":
        cond = node.child_by_field_name("condition")
        out.append((depth, f"while {_text(cond) if cond else '?'}:", "pseudo-while"))
        body = node.child_by_field_name("body")
        if body:
            _convert(body, depth + 1, out)

    elif t == "assignment":
        out.append((depth, _assignment_line(node), "pseudo-assignment"))

    elif t == "augmented_assignment":
        out.append((depth, _augmented_assignment_line(node), "pseudo-assignment"))

    elif t == "return_statement":
        out.append((depth, _return_line(node), "pseudo-return"))

    elif t == "expression_statement":
        for child in node.named_children:
            if child.type in ("string", "concatenated_string"):
                pass  # skip docstrings
            else:
                _convert(child, depth, out)

    elif t == "pass_statement":
        out.append((depth, "pass", "pseudo-control"))

    elif t == "break_statement":
        out.append((depth, "break", "pseudo-control"))

    elif t == "continue_statement":
        out.append((depth, "continue", "pseudo-control"))

    else:
        # Fallback: output raw source text for unrecognized node types
        raw = _text(node).strip()
        if raw:
            out.append((depth, raw, "pseudo-raw"))


# ---------------------------------------------------------------------------
# HTML writer
# ---------------------------------------------------------------------------

_KW_RE: dict = {
    "pseudo-if":       re.compile(r"^(if|else if|otherwise)\b"),
    "pseudo-for":      re.compile(r"^(for|do)\b"),
    "pseudo-while":    re.compile(r"^(while)\b"),
    "pseudo-function": re.compile(r"^(function)\b"),
}


def _wrap_kw(text: str, css_class: str) -> str:
    """Wrap the leading keyword in <span class="kw">...</span>."""
    pat = _KW_RE.get(css_class)
    if pat is None:
        return text
    m = pat.match(text)
    if not m:
        return text
    kw = m.group(1)
    return f'<span class="kw">{kw}</span>{text[len(kw):]}'


def _escape(text: str) -> str:
    """Escape HTML special characters in pseudocode text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _to_html(lines: list, source_name: str) -> str:
    """Convert (depth, text, css_class) tuples to a minimal HTML document."""
    parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        f"<title>Pseudocode: {source_name}</title>",
        '<link rel="stylesheet" href="pseudo.css">',
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
# Public API
# ---------------------------------------------------------------------------

def convert_file(input_path: str) -> Path:
    """
    Convert a Python file to pseudocode HTML.

    Args:
        input_path: Path to the Python source file.

    Returns:
        Path to the generated HTML file inside mirrors/.
    """
    src_path = Path(input_path).resolve()
    src_bytes = src_path.read_bytes()

    tree = _parse(src_bytes)
    lines: list = []
    _convert(tree.root_node, 0, lines)

    MIRRORS_DIR.mkdir(exist_ok=True)
    output_path = MIRRORS_DIR / (src_path.stem + ".html")
    output_path.write_text(_to_html(lines, src_path.name), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python file_to_pseudo.py <path/to/file.py>")
        sys.exit(1)
    result = convert_file(sys.argv[1])
    print(f"Written to: {result}")
