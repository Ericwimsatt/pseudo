"""
file_to_pseudo.py

Convert a Python source file to pseudocode and write it as an interactive
HTML file in the mirrors/ folder.

Generated HTML includes:
  - HTMX for dynamic modal content loading
  - data-source-file / data-source-line attributes on each <p> line
  - Function names wrapped in .fn-name spans
  - Custom JS to detect mousedown, extract the word under cursor, and
    fire HTMX requests to the Flask server for cross-referencing.

Usage:
    python file_to_pseudo.py path/to/file.py

Output:
    mirrors/<filename>.html
"""

import re
import sys
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tspython.language())
MIRRORS_DIR = Path(__file__).parent / "mirrors"


def _make_parser() -> Parser:
    return Parser(PY_LANGUAGE)


def _parse(src_bytes: bytes):
    return _make_parser().parse(src_bytes)


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace")


def _node_line(node) -> int:
    return node.start_point[0] + 1


# ---------------------------------------------------------------------------
# Line-level transformers — each returns a list of (text_or_html, is_html)
# ---------------------------------------------------------------------------

def _for_header(node) -> list:
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    left_text = _text(left) if left else "?"
    right_text = _text(right) if right else "?"

    if left_text == "_" and right and right.type == "call":
        fn = right.child_by_field_name("function")
        args = right.child_by_field_name("arguments")
        if fn and _text(fn) == "range" and args:
            named = args.named_children
            if len(named) == 1:
                return [("do", False), (f" {_text(named[0])} times", False)]

    if right and right.type == "call":
        fn = right.child_by_field_name("function")
        args = right.child_by_field_name("arguments")
        if fn and _text(fn) == "range" and args:
            named = args.named_children
            if len(named) == 1:
                n = _text(named[0])
                return [("for", False), (f" {left_text} from 0 to {n} - 1", False)]

    return [("for each", False), (f" {left_text} in {right_text}", False)]


def _function_header(node) -> list:
    name = node.child_by_field_name("name")
    params = node.child_by_field_name("parameters")
    name_text = _text(name) if name else "?"
    name_line = _node_line(name) if name else 0

    param_parts = []
    param_names = []
    if params:
        for child in params.named_children:
            t = child.type
            if t == "identifier":
                pn = _text(child)
                param_parts.append(pn)
                param_names.append(pn)
            elif t in ("default_parameter", "typed_parameter", "typed_default_parameter"):
                n = child.child_by_field_name("name")
                if n:
                    pn = _text(n)
                    param_parts.append(pn)
                    param_names.append(pn)
            elif t == "list_splat_pattern":
                inner = child.named_children[0] if child.named_children else None
                param_parts.append("*" + (_text(inner) if inner else "args"))
            elif t == "dictionary_splat_pattern":
                inner = child.named_children[0] if child.named_children else None
                param_parts.append("**" + (_text(inner) if inner else "kwargs"))

    parts = [('<span class="kw">FUNCTION</span>', True)]
    attrs = f' class="fn-name" data-fn="{name_text}" data-line="{name_line}"'
    parts.append((f'<span{attrs}>{name_text}</span>', True))
    parts.append((f'({", ".join(param_parts)})', False))
    return parts


def _assignment_line(node) -> list:
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    line = _node_line(node)

    left_text = _text(left) if left else "?"
    right_text = _text(right) if right else "?"

    parts = [("set ", False)]
    if left and left.type == "identifier":
        vname = _text(left)
        line2 = _node_line(left)
        attrs = f' class="var-name" data-var="{vname}" data-line="{line2}"'
        parts.append((f'<span{attrs}>{left_text}</span>', True))
    else:
        parts.append((left_text, False))
    parts.append((f" to {right_text}", False))
    return parts


def _var_span(left_node, vname: str) -> tuple:
    line2 = _node_line(left_node)
    attrs = f' class="var-name" data-var="{vname}" data-line="{line2}"'
    return (f'<span{attrs}>{vname}</span>', True)


def _augmented_assignment_line(node) -> list:
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    op_node = node.child_by_field_name("operator")

    left_text = _text(left) if left else "?"
    right_text = _text(right) if right else "?"

    if op_node is None:
        for child in node.children:
            if child.type in ("+=", "-=", "*=", "/=", "//=", "%=", "**=", "&=", "|=", "^="):
                op_node = child
                break

    op = _text(op_node) if op_node else "?"

    left_seg = _var_span(left, left_text) if (left and left.type == "identifier") else (left_text, False)
    right_seg = _var_span(right, right_text) if (right and right.type == "identifier") else (right_text, False)

    ops = {
        "+=": lambda: [("add ", False), right_seg, (" to ", False), left_seg],
        "-=": lambda: [("subtract ", False), right_seg, (" from ", False), left_seg],
        "*=": lambda: [("multiply ", False), left_seg, (" by ", False), right_seg],
        "/=": lambda: [("divide ", False), left_seg, (" by ", False), right_seg],
        "//=": lambda: [("floor-divide ", False), left_seg, (" by ", False), right_seg],
        "%=": lambda: [left_seg, (" mod= ", False), right_seg],
        "**=": lambda: [("raise ", False), left_seg, (" to the power of ", False), right_seg],
    }

    if op in ops:
        return ops[op]()
    return [(f"{left_text} {op} {right_text}", False)]


def _return_line(node) -> list:
    named = node.named_children
    if named:
        return [(f"return {_text(named[0])}", False)]
    return [("return", False)]


# ---------------------------------------------------------------------------
# Main recursive converter — appends (depth, parts, css_class) tuples
# parts is a list of (text_or_html, is_html_bool)
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
        text = _text(node)[1:].strip()
        out.append((depth, [(f"// {text}", False)], "pseudo-comment"))

    elif t in ("import_statement", "import_from_statement"):
        pass

    elif t == "function_definition":
        parts = _function_header(node)
        parts.append((":", False))
        out.append((depth, parts, "pseudo-function"))
        body = node.child_by_field_name("body")
        if body:
            _convert(body, depth + 1, out)

    elif t == "if_statement":
        cond = node.child_by_field_name("condition")
        out.append((depth, [(f"if {_text(cond) if cond else '?'}:", False)], "pseudo-if"))
        consequence = node.child_by_field_name("consequence")
        if consequence:
            _convert(consequence, depth + 1, out)
        for child in node.named_children:
            if child.type == "elif_clause":
                cond2 = child.child_by_field_name("condition")
                out.append((depth, [(f"else if {_text(cond2) if cond2 else '?'}:", False)], "pseudo-if"))
                cons2 = child.child_by_field_name("consequence")
                if cons2:
                    _convert(cons2, depth + 1, out)
            elif child.type == "else_clause":
                out.append((depth, [("otherwise:", False)], "pseudo-if"))
                body = child.child_by_field_name("body")
                if body:
                    _convert(body, depth + 1, out)

    elif t == "for_statement":
        parts = _for_header(node)
        parts.append((":", False))
        out.append((depth, parts, "pseudo-for"))
        body = node.child_by_field_name("body")
        if body:
            _convert(body, depth + 1, out)

    elif t == "while_statement":
        cond = node.child_by_field_name("condition")
        out.append((depth, [(f"while {_text(cond) if cond else '?'}:", False)], "pseudo-while"))
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
                pass
            else:
                _convert(child, depth, out)

    elif t == "pass_statement":
        out.append((depth, [("pass", False)], "pseudo-control"))

    elif t == "break_statement":
        out.append((depth, [("break", False)], "pseudo-control"))

    elif t == "continue_statement":
        out.append((depth, [("continue", False)], "pseudo-control"))

    else:
        raw = _text(node).strip()
        if raw:
            out.append((depth, [(raw, False)], "pseudo-raw"))


# ---------------------------------------------------------------------------
# HTML writer
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_HTMX_JS = """<script src="https://unpkg.com/htmx.org@2.0.4"></script>"""

_MODAL_HTML = """
<div id="modal-overlay" class="modal-overlay" style="display:none" onclick="closeModal(event)">
  <div id="modal-box" class="modal-box" onclick="event.stopPropagation()">
    <div id="modal-content"></div>
    <button class="modal-close" onclick="closeModal()">close</button>
  </div>
</div>"""

_INTERACTIVE_JS = """
<script>
var modalOverlay = document.getElementById('modal-overlay');
var modalContent = document.getElementById('modal-content');

function getWordAtPosition(range) {
  if (!range) return null;
  var node = range.startContainer;
  if (node.nodeType !== 3) return null;
  var text = node.textContent;
  var pos = range.startOffset;
  if (pos === undefined || text.length === 0) return null;
  var start = pos;
  while (start > 0 && /\\w/.test(text[start - 1])) start--;
  var end = pos;
  while (end < text.length && /\\w/.test(text[end])) end++;
  if (start === end) return null;
  var word = text.slice(start, end);
  return {word: word, node: node, offset: pos};
}

function showModal(x, y) {
  var box = document.getElementById('modal-box');
  modalOverlay.style.display = 'flex';
  var vw = window.innerWidth;
  var vh = window.innerHeight;
  var bx = Math.min(x, vw - 420);
  var by = Math.min(y, vh - 300);
  box.style.left = Math.max(10, bx) + 'px';
  box.style.top = Math.max(10, by) + 'px';
}

window.closeModal = function(e) {
  if (e && e.target !== modalOverlay && e.target.className !== 'modal-close') return;
  modalOverlay.style.display = 'none';
};

document.addEventListener('htmx:beforeRequest', function(evt) {
  modalContent.innerHTML = '<div class="modal-loading">searching...</div>';
});

document.addEventListener('htmx:afterRequest', function(evt) {
  var wordEl = document.getElementById('modal-word-label');
  if (wordEl) {
    document.getElementById('modal-box').querySelector('.modal-word').textContent = wordEl.textContent;
  }
});

document.addEventListener('mousedown', function(e) {
  var p = e.target.closest('p');
  if (!p) return;
  if (e.target.closest('.modal-overlay, .modal-box, .modal-close')) return;

  var isFnDef = e.target.classList.contains('fn-name');
  var isVarName = e.target.classList.contains('var-name');
  var word = null;

  if (isFnDef) {
    word = e.target.dataset.fn;
  } else if (isVarName) {
    word = e.target.dataset.var;
  } else {
    var range;
    if (document.caretRangeFromPoint) {
      range = document.caretRangeFromPoint(e.clientX, e.clientY);
    }
    var info = getWordAtPosition(range);
    if (!info) return;
    word = info.word;
    if (/^(if|else|otherwise|for|do|while|return|function|set|to|add|subtract|multiply|divide|pass|break|continue|from|in|each|times|not|and|or|True|False|None|mod)$/i.test(word)) return;
    if (/^\\d+(\\.\\d+)?$/.test(word)) return;
    if (/^['"]/.test(word)) return;
  }
  if (!word) return;

  var sourceFile = p.dataset.sourceFile || '';
  var sourceLine = p.dataset.sourceLine || '1';

  if (isFnDef || p.classList.contains('pseudo-function')) {
    var fnName = isFnDef ? word : (p.querySelector('.fn-name') || {}).dataset?.fn;
    if (!fnName) return;
    htmx.ajax('GET', '/api/function-refs?fn=' + encodeURIComponent(fnName) + '&file=' + encodeURIComponent(sourceFile), {
      target: '#modal-content',
      swap: 'innerHTML'
    });
    showModal(e.clientX, e.clientY);
  } else {
    htmx.ajax('GET', '/api/var-trace?var=' + encodeURIComponent(word) + '&file=' + encodeURIComponent(sourceFile) + '&line=' + sourceLine, {
      target: '#modal-content',
      swap: 'innerHTML'
    });
    showModal(e.clientX, e.clientY);
  }
});
</script>"""


def _to_html(lines: list, source_name: str, file_path: str, css_rel_path: str = "pseudo.css") -> str:
    parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        f"<title>Pseudocode: {source_name}</title>",
        f'<link rel="stylesheet" href="{_escape(css_rel_path)}">',
        _HTMX_JS,
        "</head>",
        "<body>",
    ]
    for depth, segs, css_class in lines:
        indent = "&nbsp;&nbsp;&nbsp;&nbsp;" * depth
        inner = ""
        for text_or_html, is_html in segs:
            if is_html:
                inner += text_or_html
            else:
                inner += _escape(text_or_html)
        attrs = f' class="{css_class}" data-source-file="{_escape(file_path)}"'
        parts.append(f"<p{attrs}>{indent}{inner}</p>")
    parts.append(_MODAL_HTML)
    parts.append(_INTERACTIVE_JS)
    parts += ["</body>", "</html>"]
    return "\n".join(parts)


def convert_file(input_path: str, output_path: str = None) -> Path:
    src_path = Path(input_path).resolve()
    src_bytes = src_path.read_bytes()

    tree = _parse(src_bytes)
    lines: list = []
    _convert(tree.root_node, 0, lines)

    MIRRORS_DIR.mkdir(exist_ok=True)
    if output_path is None:
        output_path = MIRRORS_DIR / (src_path.stem + ".html")
    else:
        output_path = Path(output_path)

    output_path.write_text(
        _to_html(lines, src_path.name, str(src_path)),
        encoding="utf-8",
    )
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python file_to_pseudo.py <path/to/file.py>")
        sys.exit(1)
    result = convert_file(sys.argv[1])
    print(f"Written to: {result}")
