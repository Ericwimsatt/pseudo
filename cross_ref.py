"""
cross_ref.py — Jedi-based cross-referencing engine.

Parses Python source files via the Jedi static analysis library and builds indexes for:
- Function definitions
- Function call sites (references)
- Variable assignments and usages

Used by server.py to answer interactive queries from the pseudocode HTML pages.
"""

import jedi
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set


class CrossReference:
    def __init__(self):
        self.project = jedi.Project(path=str(Path.cwd()))
        self.function_defs: Dict[str, List[Tuple[str, int, str]]] = {}
        self.function_calls: Dict[str, List[Tuple[str, int, str]]] = {}
        self.var_assignments: Dict[str, List[Tuple[str, int, str, Optional[str], int]]] = {}
        self.var_usages: Dict[str, List[Tuple[str, int, str, Optional[str]]]] = {}
        self.function_params: Dict[str, List[Tuple[str, int, str, str]]] = {}
        self.function_bodies: Dict[str, List[Tuple[str, int, int]]] = {}
        self.call_graph: Dict[str, List[Tuple[str, int, str]]] = {}
        self.indexed_files: Set[str] = set()
        self._basename_to_path: Dict[str, List[str]] = {}
        self._file_srcs: Dict[str, str] = {}
        # function name -> list of (file, line, column) for definition positions
        self._func_def_positions: Dict[str, List[Tuple[str, int, int]]] = {}
        self._func_calls_loaded: Set[str] = set()

    def add_source_file(self, py_path: str) -> None:
        py_path = str(Path(py_path).resolve())
        if py_path in self.indexed_files:
            return
        self.indexed_files.add(py_path)

        basename = Path(py_path).name
        self._basename_to_path.setdefault(basename, []).append(py_path)

        src = Path(py_path).read_text(encoding="utf-8", errors="replace")
        self._file_srcs[py_path] = src
        script = jedi.Script(code=src, path=py_path, project=self.project)
        self._index_script(script, py_path)

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

    def _get_source_line(self, file_path: str, line: int) -> str:
        src = self._file_srcs.get(file_path, "")
        if not src:
            try:
                src = Path(file_path).read_text(encoding="utf-8", errors="replace")
                self._file_srcs[file_path] = src
            except Exception:
                return ""
        lines = src.split("\n")
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()
        return ""

    def _get_source_range(self, file_path: str, start_line: int, end_line: int) -> str:
        src = self._file_srcs.get(file_path, "")
        if not src:
            try:
                src = Path(file_path).read_text(encoding="utf-8", errors="replace")
                self._file_srcs[file_path] = src
            except Exception:
                return ""
        lines = src.split("\n")
        return "\n".join(lines[start_line - 1 : end_line])

    def _find_enclosing_function(self, name) -> Optional[str]:
        parent = name.parent()
        while parent is not None:
            if parent.type == "function":
                return parent.name
            parent = parent.parent()
        return None

    def _index_script(self, script: jedi.Script, py_path: str) -> None:
        all_names = script.get_names(all_scopes=True, definitions=True)

        for name in all_names:
            if name.type == "function" and name.is_definition():
                end_line, _ = name.get_definition_end_position()
                func_text = self._get_source_range(py_path, name.line, end_line)
                self.function_defs.setdefault(name.name, []).append(
                    (py_path, name.line, func_text)
                )
                self.function_bodies.setdefault(name.name, []).append(
                    (py_path, name.line, end_line)
                )
                self._func_def_positions.setdefault(name.name, []).append(
                    (py_path, name.line, name.column)
                )

            elif name.type == "param" and name.is_definition():
                p = name.parent()
                func_name = p.name if p and p.type == "function" else "?"
                self.function_params.setdefault(name.name, []).append(
                    (py_path, name.line, func_name, name.description)
                )

            elif name.is_definition() and name.type not in (
                "function",
                "class",
                "module",
                "param",
            ):
                source_text = self._get_source_line(py_path, name.line)
                enclosing_func = self._find_enclosing_function(name)
                self.var_assignments.setdefault(name.name, []).append(
                    (py_path, name.line, source_text, enclosing_func, name.line)
                )

        ref_names = script.get_names(
            all_scopes=True, definitions=False, references=True
        )

        for name in ref_names:
            source_text = self._get_source_line(py_path, name.line)
            enclosing_func = self._find_enclosing_function(name)
            self.var_usages.setdefault(name.name, []).append(
                (py_path, name.line, source_text, enclosing_func)
            )

    def _load_function_calls(self, func_name: str) -> None:
        if func_name in self._func_calls_loaded:
            return
        self._func_calls_loaded.add(func_name)

        positions = self._func_def_positions.get(func_name, [])
        seen = set()

        for file_path, line, col in positions:
            src = self._file_srcs.get(file_path)
            if src is None:
                try:
                    src = Path(file_path).read_text(
                        encoding="utf-8", errors="replace"
                    )
                    self._file_srcs[file_path] = src
                except Exception:
                    continue

            try:
                s = jedi.Script(code=src, path=file_path, project=self.project)
                refs = s.get_references(line, col)
            except Exception:
                continue

            for ref in refs:
                if not ref.is_definition() and ref.module_path:
                    ref_path = str(ref.module_path)
                    key = (ref_path, ref.line)
                    if key in seen:
                        continue
                    seen.add(key)
                    ref_text = self._get_source_line(ref_path, ref.line)
                    self.function_calls.setdefault(func_name, []).append(
                        (ref_path, ref.line, ref_text)
                    )
                    self.call_graph.setdefault(func_name, []).append(
                        (ref_path, ref.line, ref_text)
                    )

    def find_function_calls(
        self, func_name: str, source_file_hint: str = ""
    ) -> List[Tuple[str, int, str]]:
        self._load_function_calls(func_name)
        calls = self.function_calls.get(func_name, [])
        if source_file_hint:
            full = self._resolve_file(source_file_hint)
            if full:
                filtered = [c for c in calls if c[0] == full]
                if filtered:
                    return filtered
        return calls

    def get_function_definition(
        self, func_name: str
    ) -> Optional[Tuple[str, int, str]]:
        defs = self.function_defs.get(func_name, [])
        if defs:
            return defs[0]
        return None

    def trace_variable(
        self, var_name: str, source_file: str, line: int
    ) -> List[dict]:
        full = self._resolve_file(source_file)
        if full:
            source_file = full
        return self._trace_var(var_name, source_file, line, set())

    def _trace_var(
        self, var_name: str, source_file: str, line: int, visited: Set[str]
    ) -> List[dict]:
        if var_name in visited:
            return [
                {
                    "variable": var_name,
                    "type": "cycle",
                    "message": "circular reference",
                }
            ]
        visited = visited | {var_name}

        assignments = self.var_assignments.get(var_name, [])
        relevant = [
            a for a in assignments if a[0] == source_file and a[4] <= line
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
                            paths.append(
                                {
                                    "variable": var_name,
                                    "type": "parameter",
                                    "of_function": func_name,
                                    "file": call_file,
                                    "line": call_line,
                                    "call_context": call_text,
                                    "chain": [
                                        {
                                            "variable": var_name,
                                            "type": "parameter",
                                            "file": p[0],
                                            "line": p[1],
                                        }
                                    ],
                                }
                            )
                    if paths:
                        return paths
                return [
                    {
                        "variable": var_name,
                        "type": "parameter",
                        "of_function": func_name,
                        "file": p[0],
                        "line": p[1],
                        "chain": [
                            {
                                "variable": var_name,
                                "type": "parameter",
                                "file": p[0],
                                "line": p[1],
                            }
                        ],
                    }
                ]
            return [
                {
                    "variable": var_name,
                    "type": "unknown",
                    "message": "no definition found",
                }
            ]

        result = []
        for assign_file, assign_line, assign_text, assign_func, _ in relevant[:3]:
            chain = [
                {
                    "variable": var_name,
                    "type": "assignment",
                    "file": assign_file,
                    "line": assign_line,
                    "context": assign_text,
                    "function": assign_func,
                }
            ]
            result.append(
                {
                    "variable": var_name,
                    "type": "assignment",
                    "file": assign_file,
                    "line": assign_line,
                    "context": assign_text,
                    "function": assign_func,
                    "chain": chain,
                }
            )
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
