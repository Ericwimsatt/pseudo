"""
server.py — Flask server for the pseudo interactive code browser.

Endpoints:
  /                    — index listing all available HTML pages
  /api/function-refs   — find all call sites for a function
  /api/var-trace       — trace a variable back to its definition

Serves static HTML from mirrors/ and computes cross-references on demand
using Tree-sitter parsing of the original Python sources.
"""

import sys
import os
from pathlib import Path
from flask import Flask, request, send_from_directory, render_template_string

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from cross_ref import CrossReference

app = Flask(__name__, static_folder=None)

MIRRORS_DIR = SCRIPT_DIR / "mirrors"

# ---------------------------------------------------------------------------
# Build the cross-reference index
# ---------------------------------------------------------------------------
xref = CrossReference()

# Index the pseudo project's own Python files
for py_file in SCRIPT_DIR.glob("*.py"):
    if py_file.name != "server.py":
        xref.add_source_file(str(py_file))

# Index sibling project if available
sibling = SCRIPT_DIR.parent / "gamble" / "uthCollusion" / "strategyBruteForce"
if sibling.exists():
    xref.add_source_dir(str(sibling))

# Try parent of sibling
for guess in [
    SCRIPT_DIR / "strategyBruteForce",
    SCRIPT_DIR.parent / "strategyBruteForce",
]:
    if guess.exists() and guess.is_dir():
        xref.add_source_dir(str(guess))

print(f"Indexed {len(xref.indexed_files)} Python source file(s)")
for f in sorted(xref.indexed_files):
    print(f"  {f}")

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    html_files = sorted(MIRRORS_DIR.rglob("*.html"))
    entries = []
    for hf in html_files:
        rel = hf.relative_to(MIRRORS_DIR)
        entries.append(rel.as_posix())
    return render_template_string("""<!DOCTYPE html>
<html><head><title>pseudo — browse</title>
<style>
body{font-family:"Courier New",monospace;background:#011809;color:#cdd6f4;padding:2em 3em;}
a{color:#89b4fa;text-decoration:none;display:block;margin:4px 0;}
a:hover{color:#b4d0fb;}
h1{color:#d4c2ea;border-bottom:1px solid #2a3a30;padding-bottom:.3em;}
</style></head><body>
<h1>pseudo — generated pseudocode pages</h1>
{% for e in entries %}
<a href="/mirrors/{{ e }}">{{ e }}</a>
{% endfor %}
</body></html>""", entries=entries)


@app.route("/mirrors/<path:filename>")
def serve_mirrors(filename):
    return send_from_directory(str(MIRRORS_DIR), filename)


@app.route("/api/function-refs")
def function_refs():
    fn = request.args.get("fn", "")
    file = request.args.get("file", "")

    calls = xref.find_function_calls(fn, file)
    fn_def = xref.get_function_definition(fn)

    lines = []
    lines.append(f'<span class="modal-word">FUNCTION {fn}</span>')

    if fn_def:
        f_file, f_line, f_text = fn_def
        lines.append(
            f'<div class="ref-entry">'
            f'<span class="ref-line">defined at {Path(f_file).name}:{f_line}</span>'
            f'</div>'
        )

    if not calls:
        lines.append('<div class="no-results">no call sites found</div>')
    else:
        for call_file, call_line, call_text in calls:
            masked = call_text[:120] + ("..." if len(call_text) > 120 else "")
            lines.append(
                f'<div class="ref-entry">'
                f'<span class="ref-line">{Path(call_file).name}:{call_line}</span>'
                f'<span class="ref-context">{masked}</span>'
                f'</div>'
            )

    return "\n".join(lines)


@app.route("/api/var-trace")
def var_trace():
    var = request.args.get("var", "")
    file = request.args.get("file", "")
    try:
        line = int(request.args.get("line", "1"))
    except ValueError:
        line = 1

    traces = xref.trace_variable(var, file, line)

    lines = []
    lines.append(f'<span class="modal-word">{var}</span>')

    if not traces:
        lines.append('<div class="no-results">no definition found</div>')
    else:
        for t in traces:
            ttype = t.get("type", "unknown")
            if ttype == "cycle":
                lines.append(f'<div class="trace-entry">cycle: {t.get("message", "")}</div>')
                continue
            if ttype == "unknown":
                lines.append(f'<div class="trace-entry">{t.get("message", "not found")}</div>')
                continue

            lines.append('<div class="trace-entry">')
            lines.append(f'<div class="trace-step"><span class="step-var">{var}</span>')
            if ttype == "parameter":
                func_name = t.get("of_function", "?")
                ctxt = t.get("call_context", "")
                call_line = t.get("line", "?")
                lines.append(
                    f'<div class="trace-step">'
                    f'<span class="step-var">{var}</span> is a parameter of '
                    f'<span class="step-context">{func_name}()</span>'
                    f'<div class="step-loc">called at {Path(t.get("file","?")).name}:{call_line}</div>'
                    f'</div>'
                )
                if ctxt:
                    lines.append(
                        f'<div class="trace-step"><span class="step-context">call: {ctxt[:100]}</span></div>'
                    )
            elif ttype == "assignment":
                tfile = t.get("file", "?")
                tline = t.get("line", "?")
                tcontext = t.get("context", "")
                tfunc = t.get("function")

                chain = t.get("chain", [])
                for step in chain:
                    sf = step.get("file", "?")
                    sl = step.get("line", "?")
                    sc = step.get("context", "")
                    sv = step.get("variable", var)
                    lines.append(
                        f'<div class="trace-step">'
                        f'<span class="step-var">{sv}</span>'
                        f'<span class="step-context"> = {sc}</span>'
                        f'<div class="step-loc">{Path(sf).name}:{sl}</div>'
                        f'</div>'
                    )
            lines.append("</div>")

    return "\n".join(lines)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting server at http://127.0.0.1:{port}")
    print(f"Indexed files: {len(xref.indexed_files)}")
    app.run(host="127.0.0.1", port=port, debug=True)
