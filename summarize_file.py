"""
Summarize Python files using tree-sitter.

Run directly:
    python summarize_file.py path/to/file.py

tree-sitter 0.23 API notes (things NOT used here but worth exploring):
  - parser.parse(src, old_tree)         : incremental re-parse; fast when editing
  - Query(PY_LANGUAGE, s_expr_pattern)  : S-expression pattern matching; e.g.:
        '(function_definition name: (identifier) @fn-name body: (block) @body)'
  - node.walk()                         : cursor-based traversal; faster than recursion
  - node.named_children                 : only semantic children (no commas, parens, etc.)
  - node.children_by_field_name(field)  : all children with a given grammar field name
  - node.parent / node.next_sibling     : navigate the tree
"""

import sys
from pathlib import Path
from typing import Optional

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

# Build the language object once at module level.
# Optional: Language.build_library() is the old 0.19 API — not needed in 0.23.
PY_LANGUAGE = Language(tspython.language())


def _make_parser() -> Parser:
    return Parser(PY_LANGUAGE)


# ---------------------------------------------------------------------------
# Raw AST dump — shows the unfiltered tree-sitter output
# ---------------------------------------------------------------------------

def dump_node(node, indent: int = 0, named_only: bool = False) -> str:  # type: ignore[return]
    """
    Recursively dump a tree-sitter Node as an indented string.

    Args:
        node       : tree_sitter.Node to dump
        indent     : current indentation level (spaces = indent * 2)
        named_only : if True, skip anonymous nodes (punctuation, keywords);
                     set this to True to get a cleaner, higher-level view

    The raw output shows every node type, its source span, and leaf text.
    Compare named_only=False vs named_only=True to see the difference.
    """
    if named_only and not node.is_named:
        return ""

    prefix = "  " * indent
    # node.type  : grammar node type string, e.g. 'function_definition'
    # node.start_point / node.end_point : (row, col) tuples (0-based rows)
    # node.text  : raw bytes from the source file (only populated on leaf nodes
    #              in some builds; always available via src slicing)
    span = f"[{node.start_point[0]+1}:{node.start_point[1]}-{node.end_point[0]+1}:{node.end_point[1]}]"
    line = f"{prefix}({node.type}) {span}"
    if node.child_count == 0 and node.text:
        # Leaf node — show the actual source text
        line += f"  {node.text.decode('utf-8', errors='replace')!r}"
    lines = [line]
    for child in node.children:
        sub = dump_node(child, indent + 1, named_only=named_only)
        if sub:
            lines.append(sub)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parameter extraction
# ---------------------------------------------------------------------------

def _summarize_parameters(params_node) -> list:
    """
    Walk a `parameters` node and return a list of dicts.

    Each dict has:
        name        : parameter name string
        kind        : 'positional' | 'default' | 'args' | 'kwargs' | 'keyword_only'
        default     : default value source text, or None
        annotation  : type annotation source text, or None

    Optional: inspect params_node.named_children (vs .children) to skip
    the comma/paren tokens and see only semantic children.
    """
    result = []
    keyword_only = False  # True after a bare * separator

    for child in params_node.named_children:
        t = child.type

        if t == "identifier":
            # Simple positional: def f(x)
            kind = "keyword_only" if keyword_only else "positional"
            result.append({"name": child.text.decode(), "kind": kind,
                           "default": None, "annotation": None})

        elif t == "typed_parameter":
            # Type-annotated: def f(x: int)
            name_node = child.child_by_field_name("name")
            ann_node = child.child_by_field_name("type")
            kind = "keyword_only" if keyword_only else "positional"
            result.append({
                "name": name_node.text.decode() if name_node else "?",
                "kind": kind,
                "default": None,
                "annotation": ann_node.text.decode() if ann_node else None,
            })

        elif t == "default_parameter":
            # Has default: def f(x=10)
            name_node = child.child_by_field_name("name")
            val_node = child.child_by_field_name("value")
            result.append({
                "name": name_node.text.decode() if name_node else "?",
                "kind": "default",
                "default": val_node.text.decode() if val_node else None,
                "annotation": None,
            })

        elif t == "typed_default_parameter":
            # Type + default: def f(x: int = 10)
            name_node = child.child_by_field_name("name")
            ann_node = child.child_by_field_name("type")
            val_node = child.child_by_field_name("value")
            result.append({
                "name": name_node.text.decode() if name_node else "?",
                "kind": "default",
                "default": val_node.text.decode() if val_node else None,
                "annotation": ann_node.text.decode() if ann_node else None,
            })

        elif t == "list_splat_pattern":
            # *args — named_children[0] is the identifier (if present)
            inner = child.named_children[0] if child.named_children else None
            result.append({
                "name": inner.text.decode() if inner else "*",
                "kind": "args",
                "default": None,
                "annotation": None,
            })

        elif t == "dictionary_splat_pattern":
            # **kwargs
            inner = child.named_children[0] if child.named_children else None
            result.append({
                "name": inner.text.decode() if inner else "**",
                "kind": "kwargs",
                "default": None,
                "annotation": None,
            })

        elif t == "keyword_separator":
            # Bare * — everything after is keyword-only
            keyword_only = True

    return result


# ---------------------------------------------------------------------------
# Docstring extraction
# ---------------------------------------------------------------------------

def _get_docstring(body_node) -> Optional[str]:
    """
    Return the first string expression in a function/class body, or None.
    tree-sitter does not tag docstrings specially — they are just
    expression_statement > string nodes at the top of the body.
    """
    for child in body_node.named_children:
        if child.type == "expression_statement":
            for sub in child.named_children:
                if sub.type in ("string", "concatenated_string"):
                    return sub.text.decode("utf-8", errors="replace")
            break  # only check the very first statement
    return None


# ---------------------------------------------------------------------------
# Tree walk to collect function definitions
# ---------------------------------------------------------------------------

def _collect_functions(node, class_name: Optional[str] = None) -> list:
    """
    Recursively collect all function_definition nodes in the tree.
    Tracks the enclosing class name so methods are labelled correctly.

    Optional: use a Query instead of manual recursion:
        query = Query(PY_LANGUAGE, '(function_definition) @fn')
        matches = query.captures(root_node)
    Queries are faster for large files and support more complex patterns.
    """
    results = []

    for child in node.named_children:
        if child.type == "function_definition":
            name_node = child.child_by_field_name("name")
            params_node = child.child_by_field_name("parameters")
            body_node = child.child_by_field_name("body")
            # return_type is the -> annotation; None when absent
            ret_node = child.child_by_field_name("return_type")

            # Decorators are siblings before the function in the tree.
            # child.child_by_field_name("decorator") does NOT work —
            # collect them from the parent's children instead.
            decorators = []
            parent = child.parent
            if parent:
                for sibling in parent.named_children:
                    if sibling.end_byte < child.start_byte and sibling.type == "decorator":
                        decorators.append(sibling.text.decode("utf-8", errors="replace"))

            results.append({
                "class": class_name,
                "name": name_node.text.decode() if name_node else "<anonymous>",
                "decorators": decorators,
                "parameters": _summarize_parameters(params_node) if params_node else [],
                "return_type": ret_node.text.decode() if ret_node else None,
                "docstring": _get_docstring(body_node) if body_node else None,
                "start_line": child.start_point[0] + 1,
                "end_line": child.end_point[0] + 1,
                # raw_ast_dump shows the unfiltered tree-sitter AST for this function.
                # Pass named_only=True to dump_node() for a less verbose view.
                "raw_ast_dump": dump_node(child, named_only=False),
                "raw_ast_dump_named_only": dump_node(child, named_only=True),
            })

            # Recurse into the function body to catch nested functions
            if body_node:
                results.extend(_collect_functions(body_node, class_name=class_name))

        elif child.type == "class_definition":
            cname_node = child.child_by_field_name("name")
            cname = cname_node.text.decode() if cname_node else "<class>"
            body = child.child_by_field_name("body")
            if body:
                results.extend(_collect_functions(body, class_name=cname))

        else:
            results.extend(_collect_functions(child, class_name=class_name))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def summarize_file(filepath: str) -> list:
    """
    Parse a Python file and return a list of function summary dicts.

    Each dict contains:
        class                  : enclosing class name, or None
        name                   : function name
        decorators             : list of decorator source strings
        parameters             : list of {name, kind, default, annotation}
        return_type            : -> annotation text, or None
        docstring              : first string in body, or None
        start_line             : 1-based start line
        end_line               : 1-based end line
        raw_ast_dump           : full tree-sitter AST dump (all nodes)
        raw_ast_dump_named_only: tree-sitter AST dump (named nodes only)
    """
    src = Path(filepath).read_bytes()
    parser = _make_parser()
    tree = parser.parse(src)

    # Optional: check tree.root_node.has_error to detect syntax errors
    # Optional: tree.root_node.sexp() returns the full S-expression of the tree

    return _collect_functions(tree.root_node)


def print_summary(filepath: str) -> None:
    """Print function summaries for a Python file to stdout."""
    summaries = summarize_file(filepath)

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else __file__
    print_summary(target)
