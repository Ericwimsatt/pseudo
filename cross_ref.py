"""
cross_ref.py — Tree-sitter based cross-referencing engine.

Parses Python source files and builds indexes for:
- Function definitions → call sites
- Variable assignments → usages

Used by server.py to answer interactive queries from the pseudocode HTML pages.
"""

import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set

PY_LANGUAGE = Language(tspython.language())


class CrossReference:
    def __init__(self):
        self.parser = Parser(PY_LANGUAGE)
        self.function_defs: Dict[str, List[Tuple[str, int, str]]] = {}
        self.function_calls: Dict[str, List[Tuple[str, int, str]]] = {}
        self.var_assignments: Dict[str, List[Tuple[str, int, str, Optional[str], int]]] = {}
        self.var_usages: Dict[str, List[Tuple[str, int, str, Optional[str]]]] = {}
        self.function_params: Dict[str, List[Tuple[str, int, str, str]]] = {}
        self.function_bodies: Dict[str, List[Tuple[str, int, int]]] = {}
        self.call_graph: Dict[str, List[Tuple[str, int, str]]] = {}
        self.indexed_files: Set[str] = set()
        self._basename_to_path: Dict[str, List[str]] = {}

    def add_source_file(self, py_path: str) -> None:
        py_path = str(Path(py_path).resolve())
        if py_path in self.indexed_files:
            return
        self.indexed_files.add(py_path)

        basename = Path(py_path).name
        self._basename_to_path.setdefault(basename, []).append(py_path)

        src = Path(py_path).read_bytes()
        tree = self.parser.parse(src)
        self._walk(tree.root_node, py_path, None)

    def _resolve_file(self, name_or_path: str) -> Optional[str]:
        p = Path(name_or_path)
        if p.is_absolute() and str(p) in self.indexed_files:
            return str(p)
        if name_or_path in self._basename_to_path:
            matches = self._basename_to_path[name_or_path]
            return matches[0]
        for full_path in self.indexed_files:
            if full_path.endswith(name_or_path):
                return full_path
        return None

    def add_source_dir(self, dir_path: str) -> None:
        for py_file in sorted(Path(dir_path).rglob("*.py")):
            self.add_source_file(str(py_file))

    def _walk(self, node, file_path: str, current_func: Optional[str]) -> None:
        t = node.type

        if t == "function_definition":
            name_node = node.child_by_field_name("name")
            params_node = node.child_by_field_name("parameters")
            name = _text(name_node) if name_node else "?"
            line = node.start_point[0] + 1

            self.function_defs.setdefault(name, []).append(
                (file_path, line, _text(node))
            )
            self.function_bodies.setdefault(name, []).append(
                (file_path, node.start_point[0] + 1, node.end_point[0] + 1)
            )

            if params_node:
                for child in params_node.named_children:
                    if child.type == "identifier":
                        pname = _text(child)
                        self.function_params.setdefault(pname, []).append(
                            (file_path, child.start_point[0] + 1, name, _text(child))
                        )

            body = node.child_by_field_name("body")
            if body:
                for child in body.named_children:
                    self._walk(child, file_path, name)
            return

        if t == "block":
            for child in node.named_children:
                self._walk(child, file_path, current_func)

        elif t == "module":
            for child in node.named_children:
                self._walk(child, file_path, current_func)

        elif t == "assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            line = node.start_point[0] + 1

            if left and left.type == "identifier":
                vname = _text(left)
                self.var_assignments.setdefault(vname, []).append(
                    (file_path, line, _text(node), current_func, node.start_point[0] + 1)
                )

            if right:
                self._index_identifiers(right, file_path, current_func)

        elif t == "augmented_assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            line = node.start_point[0] + 1

            if left and left.type == "identifier":
                vname = _text(left)
                self.var_assignments.setdefault(vname, []).append(
                    (file_path, line, _text(node), current_func, node.start_point[0] + 1)
                )

            if right:
                self._index_identifiers(right, file_path, current_func)

        elif t == "call":
            fn_node = node.child_by_field_name("function")
            if fn_node:
                fn_name = _text(fn_node)
                line = node.start_point[0] + 1
                self.function_calls.setdefault(fn_name, []).append(
                    (file_path, line, _text(node))
                )
                self.call_graph.setdefault(fn_name, []).append(
                    (file_path, line, _text(node))
                )
            args = node.child_by_field_name("arguments")
            if args:
                self._walk(args, file_path, current_func)

        elif t == "identifier":
            name = _text(node)
            line = node.start_point[0] + 1
            self.var_usages.setdefault(name, []).append(
                (file_path, line, _text(node), current_func)
            )

        else:
            for child in node.named_children:
                self._walk(child, file_path, current_func)

    def _index_identifiers(self, node, file_path: str, current_func: Optional[str]) -> None:
        t = node.type
        if t == "identifier":
            name = _text(node)
            line = node.start_point[0] + 1
            self.var_usages.setdefault(name, []).append(
                (file_path, line, _text(node), current_func)
            )
        elif t == "call":
            fn_node = node.child_by_field_name("function")
            if fn_node:
                fn_name = _text(fn_node)
                line = node.start_point[0] + 1
                self.function_calls.setdefault(fn_name, []).append(
                    (file_path, line, _text(node))
                )
                self.call_graph.setdefault(fn_name, []).append(
                    (file_path, line, _text(node))
                )
            for child in node.named_children:
                self._index_identifiers(child, file_path, current_func)
        else:
            for child in node.named_children:
                self._index_identifiers(child, file_path, current_func)

    def find_function_calls(self, func_name: str, source_file_hint: str = "") -> List[Tuple[str, int, str]]:
        calls = self.function_calls.get(func_name, [])
        if source_file_hint:
            full = self._resolve_file(source_file_hint)
            if full:
                filtered = [c for c in calls if c[0] == full]
                if filtered:
                    return filtered
        return calls

    def get_function_definition(self, func_name: str) -> Optional[Tuple[str, int, str]]:
        defs = self.function_defs.get(func_name, [])
        if defs:
            return defs[0]
        return None

    def trace_variable(self, var_name: str, source_file: str, line: int) -> List[dict]:
        full = self._resolve_file(source_file)
        if full:
            source_file = full
        return self._trace_var(var_name, source_file, line, set())

    def _trace_var(self, var_name: str, source_file: str, line: int, visited: Set[str]) -> List[dict]:
        if var_name in visited:
            return [{"variable": var_name, "type": "cycle", "message": "circular reference"}]
        visited = visited | {var_name}

        assignments = self.var_assignments.get(var_name, [])
        relevant = [
            a for a in assignments
            if a[0] == source_file and a[4] <= line
        ]
        relevant.sort(key=lambda x: x[4], reverse=True)

        if not relevant:
            params = self.function_params.get(var_name, [])
            param_in_file = [p for p in params if p[0] == source_file]
            if param_in_file:
                p = param_in_file[0]
                func_name = p[2]
                calls_to_func = self.find_function_calls(func_name)
                if calls_to_func:
                    paths = []
                    for call_file, call_line, call_text in calls_to_func:
                        if call_file == source_file:
                            paths.append({
                                "variable": var_name,
                                "type": "parameter",
                                "of_function": func_name,
                                "file": call_file,
                                "line": call_line,
                                "call_context": call_text,
                                "chain": [{"variable": var_name, "type": "parameter", "file": p[0], "line": p[1]}]
                            })
                    return paths if paths else [{
                        "variable": var_name, "type": "parameter", "of_function": func_name,
                        "file": p[0], "line": p[1],
                        "chain": [{"variable": var_name, "type": "parameter", "file": p[0], "line": p[1]}]
                    }]
                return [{
                    "variable": var_name, "type": "parameter", "of_function": func_name,
                    "file": p[0], "line": p[1],
                    "chain": [{"variable": var_name, "type": "parameter", "file": p[0], "line": p[1]}]
                }]
            return [{"variable": var_name, "type": "unknown", "message": "no definition found"}]

        result = []
        for assign_file, assign_line, assign_text, assign_func, _ in relevant[:3]:
            chain = [{
                "variable": var_name,
                "type": "assignment",
                "file": assign_file,
                "line": assign_line,
                "context": assign_text,
                "function": assign_func
            }]
            result.append({
                "variable": var_name,
                "type": "assignment",
                "file": assign_file,
                "line": assign_line,
                "context": assign_text,
                "function": assign_func,
                "chain": chain
            })
        return result

    def get_word_info(self, word: str, source_file: str, line: int) -> dict:
        is_function_def = False
        is_function_call = False
        is_variable = False

        if word in self.function_defs:
            for f_file, f_line, f_text in self.function_defs[word]:
                if f_file == source_file and abs(f_line - line) < 3:
                    is_function_def = True
                    break

        if word in self.function_calls:
            for c_file, c_line, c_text in self.function_calls[word]:
                if c_file == source_file and abs(c_line - line) < 3:
                    is_function_call = True
                    break

        if word in self.var_assignments or word in self.var_usages:
            is_variable = True

        return {
            "word": word,
            "is_function_def": is_function_def,
            "is_function_call": is_function_call,
            "is_variable": is_variable,
        }


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace")
