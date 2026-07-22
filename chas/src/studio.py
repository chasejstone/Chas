"""A dependency-free, local browser workbench for Chas.

The server deliberately exposes only an in-memory source buffer.  It binds to
the IPv4 loopback address, serves all assets from this file, and requires a
random launch token on every POST request.
"""

from __future__ import annotations

import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlsplit

from studio_service import (
    DEFAULT_INSTRUCTION_LIMIT,
    MAX_SOURCE_BYTES,
    StudioLimitError,
    analyze_source,
    run_source,
)


LOOPBACK_HOST = "127.0.0.1"
MAX_REQUEST_BYTES = 1024 * 1024

DEFAULT_SOURCE = """// Welcome to Chas Studio.
// Try both engines, then inspect the AST and bytecode below.

fn square(n: int) -> int {
    return n * n
}

for i in 1..6 {
    print(square(i))
}
"""

SAMPLES = (
    {
        "id": "welcome",
        "name": "Welcome",
        "description": "Functions, loops, and the compiler pipeline",
        "source": DEFAULT_SOURCE,
    },
    {
        "id": "fibonacci",
        "name": "Fibonacci",
        "description": "Recursion and typed functions",
        "source": """fn fib(n: int) -> int {
    if n < 2 {
        return n
    }
    return fib(n - 1) + fib(n - 2)
}

for i in 0..10 {
    print(fib(i))
}
""",
    },
    {
        "id": "closures",
        "name": "Closure counter",
        "description": "Capture and mutate an enclosing variable",
        "source": """fn counter() {
    let n = 0
    fn tick() {
        n = n + 1
        print(n)
    }
    tick()
    tick()
    tick()
}

counter()
""",
    },
    {
        "id": "types",
        "name": "Type lab",
        "description": "Inference, primitives, and built-ins",
        "source": """let greeting: string = \"Hello, Chas!\"
let answer = 42
let precise = 3.14

print(greeting)
print(type(answer))
print(type(precise))
print(len(greeting))
""",
    },
)


_PAGE = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Chas Studio</title>
  <style nonce="__NONCE__">
    :root {
      --ink: #09101d;
      --panel: #101a2b;
      --raised: #17243a;
      --line: #263650;
      --text: #edf2f8;
      --muted: #91a0b6;
      --amber: #f3aa5b;
      --mint: #55d3ad;
      --coral: #ff727f;
      --blue: #6eb6ff;
      --violet: #b59cff;
      --code-size: 15px;
      color-scheme: dark;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; }
    body {
      min-width: 320px;
      overflow: hidden;
      background: var(--ink);
      color: var(--text);
      font: 14px/1.45 Inter, ui-sans-serif, system-ui, -apple-system,
        BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button, select, textarea { font: inherit; }
    button, select {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--raised);
      color: var(--text);
    }
    button { cursor: pointer; }
    button:hover:not(:disabled), select:hover { border-color: #4b6080; }
    button:focus-visible, select:focus-visible, textarea:focus-visible,
    [role="tab"]:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; }
    button:disabled { cursor: wait; opacity: .55; }

    .shell { display: grid; grid-template-rows: 60px 1fr 30px; height: 100%; }
    header {
      display: flex; align-items: center; gap: 18px; padding: 0 20px;
      border-bottom: 1px solid var(--line); background: #0c1524;
    }
    .brand { display: flex; align-items: baseline; gap: 9px; white-space: nowrap; }
    .brand-mark {
      display: grid; place-items: center; width: 30px; height: 30px;
      border: 1px solid #a66b32; border-radius: 9px; color: var(--amber);
      background: #211a16; font: 700 14px ui-monospace, monospace;
    }
    .brand strong { letter-spacing: .16em; font-size: 13px; }
    .brand span { color: var(--muted); font-size: 12px; }
    .header-spacer { flex: 1; }
    .shortcut { color: var(--muted); font-size: 11px; margin-left: 5px; }
    .action { min-height: 34px; padding: 0 13px; font-weight: 650; }
    .action.primary { background: var(--amber); border-color: var(--amber); color: #17100a; }
    .action.primary:hover:not(:disabled) { background: #ffc177; border-color: #ffc177; }

    main { display: grid; grid-template-columns: 230px minmax(0, 1fr); min-height: 0; }
    aside {
      min-width: 0; overflow: auto; padding: 18px 14px;
      border-right: 1px solid var(--line); background: #0c1524;
    }
    .eyebrow {
      color: var(--muted); font-size: 10px; font-weight: 750;
      letter-spacing: .17em; text-transform: uppercase;
    }
    .sample-select { width: 100%; min-height: 38px; margin: 8px 0 7px; padding: 0 10px; }
    .sample-note { min-height: 38px; margin: 0 2px 20px; color: var(--muted); font-size: 12px; }
    .engine { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; margin: 8px 0 21px; }
    .engine input { position: absolute; opacity: 0; pointer-events: none; }
    .engine label {
      padding: 8px 6px; border: 1px solid var(--line); border-radius: 8px;
      color: var(--muted); text-align: center; cursor: pointer;
    }
    .engine input:checked + label {
      border-color: #a66b32; background: #211a16; color: var(--amber);
    }
    .pipeline { display: grid; gap: 7px; margin-top: 8px; }
    .stage {
      display: grid; grid-template-columns: 24px 1fr auto; align-items: center;
      gap: 8px; min-height: 40px; padding: 0 10px; border: 1px solid var(--line);
      border-radius: 8px; background: #0f1929;
    }
    .stage-icon { color: var(--muted); text-align: center; }
    .stage-name { font-size: 12px; font-weight: 650; }
    .stage-state { color: var(--muted); font-size: 10px; text-transform: uppercase; }
    .stage.passed .stage-icon, .stage.passed .stage-state { color: var(--mint); }
    .stage.failed .stage-icon, .stage.failed .stage-state { color: var(--coral); }
    .stage.pending .stage-icon, .stage.pending .stage-state { color: var(--amber); }
    .stage.unavailable { opacity: .65; }

    .workspace { display: grid; grid-template-rows: minmax(250px, 58%) minmax(180px, 42%); min-width: 0; min-height: 0; }
    .editor-panel { display: grid; grid-template-rows: 39px 1fr; min-height: 0; background: #0b1322; }
    .panel-head {
      display: flex; align-items: center; padding: 0 14px; border-bottom: 1px solid var(--line);
      color: var(--muted); background: var(--panel); font-size: 12px;
    }
    .dirty { width: 7px; height: 7px; margin-right: 8px; border-radius: 50%; background: var(--amber); opacity: 0; }
    .dirty.visible { opacity: 1; }
    .panel-head .right { margin-left: auto; }
    .editor-wrap { display: grid; grid-template-columns: 54px 1fr; min-height: 0; overflow: hidden; }
    #lines {
      overflow: hidden; padding: 15px 12px 40px 0; border-right: 1px solid #1a2940;
      color: #5f7089; background: #0a1220; text-align: right; user-select: none;
      white-space: pre; font: var(--code-size)/1.62 "Cascadia Code", Consolas, ui-monospace, monospace;
    }
    #editor {
      width: 100%; height: 100%; min-height: 0; resize: none; border: 0; outline: 0;
      padding: 15px 18px 50px; tab-size: 4; color: #e8eef7; background: #0b1322;
      caret-color: var(--amber); white-space: pre; overflow: auto;
      font: var(--code-size)/1.62 "Cascadia Code", Consolas, ui-monospace, monospace;
    }
    #editor::selection { background: #31527d; }

    .results { display: grid; grid-template-rows: 40px 1fr; min-height: 0; border-top: 1px solid var(--line); background: var(--panel); }
    .tabs { display: flex; align-items: end; gap: 2px; padding: 0 10px; border-bottom: 1px solid var(--line); overflow-x: auto; }
    [role="tab"] {
      height: 39px; padding: 0 12px; border: 0; border-radius: 0; background: transparent;
      color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .04em;
    }
    [role="tab"][aria-selected="true"] { color: var(--text); box-shadow: inset 0 -2px var(--amber); }
    .count {
      display: inline-grid; min-width: 17px; height: 17px; margin-left: 5px; padding: 0 4px;
      place-items: center; border-radius: 9px; background: #25354e; color: var(--muted); font-size: 9px;
    }
    .result-body { min-height: 0; overflow: auto; padding: 14px 16px 28px; }
    .tab-panel { display: none; min-height: 100%; }
    .tab-panel.active { display: block; }
    pre {
      margin: 0; color: #dfe8f5; white-space: pre-wrap; overflow-wrap: anywhere;
      font: 13px/1.58 "Cascadia Code", Consolas, ui-monospace, monospace;
    }
    .empty { display: grid; min-height: 110px; place-items: center; color: var(--muted); text-align: center; }
    .problem {
      display: grid; grid-template-columns: auto 1fr auto; gap: 10px; width: 100%;
      margin-bottom: 8px; padding: 10px 12px; border-color: #633743; background: #241721;
      text-align: left;
    }
    .problem strong { color: var(--coral); }
    .problem small { color: var(--muted); }
    table { width: 100%; border-collapse: collapse; font: 12px/1.5 ui-monospace, monospace; }
    th { position: sticky; top: -14px; background: var(--panel); color: var(--muted); text-align: left; }
    th, td { padding: 7px 10px; border-bottom: 1px solid #22314a; vertical-align: top; }
    td:first-child { color: var(--muted); white-space: nowrap; }
    td:nth-child(2) { color: var(--violet); }
    td:nth-child(3) { color: #9ed7b7; overflow-wrap: anywhere; }

    footer {
      display: flex; align-items: center; gap: 14px; padding: 0 14px;
      border-top: 1px solid var(--line); background: #0c1524; color: var(--muted); font-size: 11px;
    }
    #status[data-tone="good"] { color: var(--mint); }
    #status[data-tone="bad"] { color: var(--coral); }
    #status[data-tone="busy"] { color: var(--amber); }
    footer .right { margin-left: auto; }

    @media (max-width: 760px) {
      .shell { grid-template-rows: auto 1fr 30px; }
      header { min-height: 60px; padding: 10px 12px; gap: 8px; flex-wrap: wrap; }
      .brand { margin-right: auto; }
      .header-spacer { display: none; }
      .shortcut { display: none; }
      main { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); }
      aside { display: grid; grid-template-columns: minmax(150px, 1fr) minmax(150px, 1fr); gap: 8px 14px; padding: 10px 12px; border: 0; border-bottom: 1px solid var(--line); overflow: visible; }
      aside .sample-note, .pipeline-label, .pipeline { display: none; }
      .sample-select, .engine { margin: 0; }
      .workspace { grid-template-rows: minmax(220px, 55%) minmax(170px, 45%); }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="brand"><div class="brand-mark">C:</div><strong>CHAS</strong><span>STUDIO</span></div>
      <div class="header-spacer"></div>
      <button id="check" class="action" type="button">Check <span class="shortcut">Shift F5</span></button>
      <button id="run" class="action primary" type="button">Run <span class="shortcut">F5</span></button>
    </header>

    <main>
      <aside aria-label="Studio controls">
        <div class="eyebrow">Playground</div>
        <div>
          <select id="samples" class="sample-select" aria-label="Load sample program"></select>
          <p id="sample-note" class="sample-note"></p>
        </div>
        <div class="eyebrow">Engine</div>
        <div class="engine" role="radiogroup" aria-label="Execution engine">
          <input id="tree" name="engine" type="radio" value="tree" checked>
          <label for="tree">Tree</label>
          <input id="vm" name="engine" type="radio" value="vm">
          <label for="vm">VM</label>
        </div>
        <div class="eyebrow pipeline-label">Pipeline</div>
        <div class="pipeline" aria-label="Compiler pipeline">
          <div class="stage" data-stage="lexer"><span class="stage-icon">—</span><span class="stage-name">Lexer</span><span class="stage-state">Idle</span></div>
          <div class="stage" data-stage="parser"><span class="stage-icon">—</span><span class="stage-name">Parser</span><span class="stage-state">Idle</span></div>
          <div class="stage" data-stage="types"><span class="stage-icon">—</span><span class="stage-name">Types</span><span class="stage-state">Idle</span></div>
          <div class="stage" data-stage="bytecode"><span class="stage-icon">—</span><span class="stage-name">Bytecode</span><span class="stage-state">Idle</span></div>
          <div class="stage" data-stage="run"><span class="stage-icon">—</span><span class="stage-name">Run</span><span class="stage-state">Idle</span></div>
        </div>
      </aside>

      <section class="workspace" aria-label="Chas workbench">
        <div class="editor-panel">
          <div class="panel-head"><span id="dirty" class="dirty" aria-hidden="true"></span><span>playground.chs</span><span class="right">UTF-8 · Spaces: 4</span></div>
          <div class="editor-wrap">
            <div id="lines" aria-hidden="true">1</div>
            <textarea id="editor" aria-label="Chas source editor" autocomplete="off" autocapitalize="off" spellcheck="false"></textarea>
          </div>
        </div>

        <section class="results" aria-label="Compiler results">
          <div class="tabs" role="tablist" aria-label="Result views">
            <button role="tab" data-tab="output" aria-selected="true">OUTPUT</button>
            <button role="tab" data-tab="problems" aria-selected="false">PROBLEMS <span id="problem-count" class="count">0</span></button>
            <button role="tab" data-tab="tokens" aria-selected="false">TOKENS</button>
            <button role="tab" data-tab="ast" aria-selected="false">AST</button>
            <button role="tab" data-tab="bytecode" aria-selected="false">BYTECODE</button>
          </div>
          <div class="result-body">
            <div id="panel-output" class="tab-panel active" role="tabpanel"><div class="empty">Run the program to see its output.</div></div>
            <div id="panel-problems" class="tab-panel" role="tabpanel"><div class="empty">No problems yet.</div></div>
            <div id="panel-tokens" class="tab-panel" role="tabpanel"><div class="empty">Check the program to inspect tokens.</div></div>
            <div id="panel-ast" class="tab-panel" role="tabpanel"><div class="empty">Check the program to inspect its AST.</div></div>
            <div id="panel-bytecode" class="tab-panel" role="tabpanel"><div class="empty">Check the program to inspect bytecode.</div></div>
          </div>
        </section>
      </section>
    </main>

    <footer><span id="status">Ready</span><span id="timing"></span><span class="right" id="cursor">Ln 1, Col 1</span><span>Chas 0.2</span></footer>
  </div>

  <script id="bootstrap" type="application/json" nonce="__NONCE__">__BOOTSTRAP__</script>
  <script nonce="__NONCE__">
    (() => {
      "use strict";
      const boot = JSON.parse(document.getElementById("bootstrap").textContent);
      const editor = document.getElementById("editor");
      const lines = document.getElementById("lines");
      const dirty = document.getElementById("dirty");
      const status = document.getElementById("status");
      const timing = document.getElementById("timing");
      const cursor = document.getElementById("cursor");
      const sampleSelect = document.getElementById("samples");
      const sampleNote = document.getElementById("sample-note");
      const checkButton = document.getElementById("check");
      const runButton = document.getElementById("run");
      let cleanSource = boot.initialSource;
      let busy = false;

      function setStatus(text, tone = "") {
        status.textContent = text;
        status.dataset.tone = tone;
      }

      function updateEditorChrome() {
        const count = editor.value.split("\n").length;
        lines.textContent = Array.from({length: count}, (_, i) => i + 1).join("\n");
        dirty.classList.toggle("visible", editor.value !== cleanSource);
        const before = editor.value.slice(0, editor.selectionStart);
        const line = before.split("\n").length;
        const column = before.length - before.lastIndexOf("\n");
        cursor.textContent = `Ln ${line}, Col ${column}`;
      }

      function selectTab(name) {
        document.querySelectorAll('[role="tab"]').forEach(tab => {
          const selected = tab.dataset.tab === name;
          tab.setAttribute("aria-selected", String(selected));
          tab.tabIndex = selected ? 0 : -1;
        });
        document.querySelectorAll(".tab-panel").forEach(panel => {
          panel.classList.toggle("active", panel.id === `panel-${name}`);
        });
      }

      function textPanel(name, text, emptyMessage) {
        const panel = document.getElementById(`panel-${name}`);
        panel.replaceChildren();
        if (!text) {
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = emptyMessage;
          panel.append(empty);
          return;
        }
        const pre = document.createElement("pre");
        pre.textContent = text;
        panel.append(pre);
      }

      function renderProblems(items) {
        const panel = document.getElementById("panel-problems");
        const count = document.getElementById("problem-count");
        panel.replaceChildren();
        count.textContent = String(items.length);
        if (!items.length) {
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = "No problems found.";
          panel.append(empty);
          return;
        }
        items.forEach(item => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "problem";
          const category = document.createElement("strong");
          category.textContent = item.category;
          const message = document.createElement("span");
          message.textContent = item.message;
          const location = document.createElement("small");
          location.textContent = `${item.line}:${item.column}`;
          button.append(category, message, location);
          button.addEventListener("click", () => jumpTo(item.line, item.column));
          panel.append(button);
        });
      }

      function renderTokens(tokens, truncated) {
        const panel = document.getElementById("panel-tokens");
        panel.replaceChildren();
        if (!tokens.length) {
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = "No tokens produced.";
          panel.append(empty);
          return;
        }
        const table = document.createElement("table");
        const head = document.createElement("thead");
        const hr = document.createElement("tr");
        ["POSITION", "KIND", "LEXEME"].forEach(label => {
          const th = document.createElement("th"); th.textContent = label; hr.append(th);
        });
        head.append(hr); table.append(head);
        const body = document.createElement("tbody");
        tokens.forEach(token => {
          const row = document.createElement("tr");
          [`${token.line}:${token.column}`, token.kind, token.lexeme].forEach(value => {
            const td = document.createElement("td"); td.textContent = value; row.append(td);
          });
          body.append(row);
        });
        table.append(body); panel.append(table);
        if (truncated) {
          const note = document.createElement("p");
          note.className = "sample-note";
          note.textContent = "Token view truncated at the Studio limit.";
          panel.append(note);
        }
      }

      function renderStages(stages) {
        const icons = {passed: "✓", failed: "!", pending: "…", blocked: "—", idle: "—", unavailable: "○"};
        document.querySelectorAll(".stage").forEach(row => {
          const value = stages[row.dataset.stage] || "idle";
          row.className = `stage ${value}`;
          row.querySelector(".stage-icon").textContent = icons[value] || "—";
          row.querySelector(".stage-state").textContent = value;
        });
      }

      function applyResult(result, wasRun) {
        renderStages(result.stages || {});
        renderProblems(result.diagnostics || []);
        renderTokens(result.tokens || [], result.tokens_truncated);
        textPanel("ast", result.ast, "No AST available.");
        textPanel("bytecode", result.bytecode,
          result.bytecode_available ? "No bytecode available." : "The bytecode engine is unavailable.");
        if (wasRun) textPanel("output", result.output, "Program completed without output.");
        timing.textContent = `${result.duration_ms || 0} ms`;
        if (result.ok) {
          setStatus(wasRun ? `Finished with ${result.engine}` : "Checks passed", "good");
          selectTab(wasRun ? "output" : "problems");
        } else {
          setStatus("Needs attention", "bad");
          selectTab("problems");
        }
      }

      async function request(path, payload) {
        const response = await fetch(path, {
          method: "POST",
          headers: {"Content-Type": "application/json", "X-Chas-Token": boot.token},
          body: JSON.stringify(payload),
          credentials: "same-origin",
          cache: "no-store"
        });
        let body;
        try { body = await response.json(); }
        catch (_) { throw new Error(`Studio returned HTTP ${response.status}`); }
        if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
        return body;
      }

      async function perform(kind) {
        if (busy) return;
        busy = true;
        checkButton.disabled = runButton.disabled = true;
        setStatus(kind === "run" ? "Running…" : "Checking…", "busy");
        try {
          const payload = {source: editor.value};
          if (kind === "run") {
            payload.engine = document.querySelector('input[name="engine"]:checked').value;
          }
          const result = await request(`/api/${kind === "run" ? "run" : "analyze"}`, payload);
          applyResult(result, kind === "run");
        } catch (error) {
          setStatus("Studio request failed", "bad");
          renderProblems([{category: "StudioError", message: error.message, line: 1, column: 1}]);
          selectTab("problems");
        } finally {
          busy = false;
          checkButton.disabled = runButton.disabled = false;
        }
      }

      function jumpTo(line, column) {
        const rows = editor.value.split("\n");
        let offset = 0;
        for (let i = 0; i < Math.max(0, line - 1) && i < rows.length; i++) offset += rows[i].length + 1;
        offset += Math.max(0, column - 1);
        editor.focus();
        editor.setSelectionRange(Math.min(offset, editor.value.length), Math.min(offset + 1, editor.value.length));
        updateEditorChrome();
      }

      const initialSample = boot.samples.find(sample => sample.source === boot.initialSource);
      if (!initialSample) {
        const loaded = document.createElement("option");
        loaded.value = "";
        loaded.textContent = "Loaded source";
        loaded.disabled = true;
        sampleSelect.append(loaded);
      }
      boot.samples.forEach(sample => {
        const option = document.createElement("option");
        option.value = sample.id; option.textContent = sample.name; sampleSelect.append(option);
      });
      sampleSelect.addEventListener("change", () => {
        const sample = boot.samples.find(item => item.id === sampleSelect.value);
        if (!sample) return;
        if (editor.value !== cleanSource && !window.confirm("Replace your edited program with this sample?")) return;
        editor.value = sample.source;
        cleanSource = sample.source;
        sampleNote.textContent = sample.description;
        updateEditorChrome();
        setStatus("Sample loaded");
      });

      document.querySelectorAll('[role="tab"]').forEach(tab => {
        tab.addEventListener("click", () => selectTab(tab.dataset.tab));
      });
      editor.addEventListener("input", () => { updateEditorChrome(); setStatus("Edited — check pending"); });
      editor.addEventListener("scroll", () => { lines.scrollTop = editor.scrollTop; });
      editor.addEventListener("keyup", updateEditorChrome);
      editor.addEventListener("click", updateEditorChrome);
      editor.addEventListener("keydown", event => {
        if (event.key === "Tab") {
          event.preventDefault();
          const start = editor.selectionStart, end = editor.selectionEnd;
          editor.setRangeText("    ", start, end, "end");
          editor.dispatchEvent(new Event("input"));
        }
      });
      checkButton.addEventListener("click", () => perform("check"));
      runButton.addEventListener("click", () => perform("run"));
      document.addEventListener("keydown", event => {
        if (event.key === "F5") {
          event.preventDefault(); perform(event.shiftKey ? "check" : "run");
        } else if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
          event.preventDefault(); perform(event.shiftKey ? "check" : "run");
        } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
          event.preventDefault(); perform("check");
        }
      });

      editor.value = boot.initialSource;
      sampleSelect.value = initialSample ? initialSample.id : "";
      sampleNote.textContent = initialSample
        ? initialSample.description
        : "Source loaded from the command line";
      updateEditorChrome();
      window.setTimeout(() => perform("check"), 120);
    })();
  </script>
</body>
</html>
'''


def _json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _html_json(payload: object) -> str:
    text = _json_bytes(payload).decode("utf-8")
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _render_page(token: str, nonce: str, initial_source: str) -> bytes:
    bootstrap = {
        "token": token,
        "initialSource": initial_source,
        "samples": SAMPLES,
        "sourceLimit": MAX_SOURCE_BYTES,
    }
    page = _PAGE.replace("__NONCE__", nonce).replace(
        "__BOOTSTRAP__", _html_json(bootstrap)
    )
    return page.encode("utf-8")


def _make_handler(token: str, nonce: str, initial_source: str):
    page = _render_page(token, nonce, initial_source)

    class StudioHandler(BaseHTTPRequestHandler):
        server_version = "ChasStudio/0.2"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args) -> None:
            return

        def _host_is_valid(self) -> bool:
            host = self.headers.get("Host", "")
            expected = f"{LOOPBACK_HOST}:{self.server.server_port}"
            return host in (expected, LOOPBACK_HOST)

        def _security_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; "
                f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "connect-src 'self'; img-src data:; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'none'",
            )

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _send_json(self, status: int, payload: object) -> None:
            try:
                body = _json_bytes(payload)
            except (TypeError, ValueError):
                status = 500
                body = _json_bytes({"error": "Studio produced an invalid response"})
            self._send_bytes(status, body, "application/json; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._send_json(status, {"error": message})

        def do_GET(self) -> None:
            if not self._host_is_valid():
                self._error(421, "invalid Host header")
                return
            path = urlsplit(self.path).path
            if path == "/":
                self._send_bytes(200, page, "text/html; charset=utf-8")
            elif path == "/api/health":
                self._send_json(200, {"ok": True, "service": "chas-studio"})
            else:
                self._error(404, "not found")

        def do_POST(self) -> None:
            if not self._host_is_valid():
                self._error(421, "invalid Host header")
                return

            supplied = self.headers.get("X-Chas-Token", "")
            if not supplied or not secrets.compare_digest(supplied, token):
                self._error(403, "missing or invalid Studio token")
                return

            expected_origin = f"http://{LOOPBACK_HOST}:{self.server.server_port}"
            origin = self.headers.get("Origin")
            if origin is not None and origin != expected_origin:
                self._error(403, "cross-origin requests are not allowed")
                return

            media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                self._error(415, "Content-Type must be application/json")
                return

            length_text = self.headers.get("Content-Length")
            if length_text is None:
                self._error(411, "Content-Length is required")
                return
            try:
                length = int(length_text, 10)
            except ValueError:
                self._error(400, "invalid Content-Length")
                return
            if length < 0:
                self._error(400, "invalid Content-Length")
                return
            if length > MAX_REQUEST_BYTES:
                self.close_connection = True
                self._error(413, "request body is too large")
                return

            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(400, "request body must be valid UTF-8 JSON")
                return
            if not isinstance(payload, dict):
                self._error(400, "request body must be a JSON object")
                return
            source = payload.get("source")
            if not isinstance(source, str):
                self._error(400, "source must be a string")
                return

            path = urlsplit(self.path).path
            try:
                if path == "/api/analyze":
                    result = analyze_source(source)
                elif path == "/api/run":
                    engine = payload.get("engine", "tree")
                    instruction_limit = payload.get(
                        "instructionLimit", DEFAULT_INSTRUCTION_LIMIT
                    )
                    result = run_source(
                        source,
                        engine=engine,
                        instruction_limit=instruction_limit,
                    )
                else:
                    self._error(404, "not found")
                    return
            except StudioLimitError as error:
                self._error(413, str(error))
                return
            except (TypeError, ValueError) as error:
                self._error(400, str(error))
                return
            except Exception:
                self._error(500, "internal Studio error")
                return
            self._send_json(200, result)

    return StudioHandler


class _StudioServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:
        """Silence routine browser disconnects, but preserve real tracebacks."""

        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def _create_server(
    *,
    host: str = LOOPBACK_HOST,
    port: int = 0,
    initial_source: Optional[str] = None,
    token: Optional[str] = None,
) -> _StudioServer:
    """Create a configured server without starting its request loop."""

    if host != LOOPBACK_HOST:
        raise ValueError("Chas Studio only binds to 127.0.0.1")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535")
    source = DEFAULT_SOURCE if initial_source is None else initial_source
    if not isinstance(source, str):
        raise TypeError("initial_source must be a string or None")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise StudioLimitError("initial_source exceeds the Studio source limit")
    launch_token = token or secrets.token_urlsafe(32)
    if not isinstance(launch_token, str) or not launch_token:
        raise ValueError("token must be a non-empty string")
    nonce = secrets.token_urlsafe(18)
    handler = _make_handler(launch_token, nonce, source)
    server = _StudioServer((host, port), handler)
    server.studio_token = launch_token
    return server


def serve(
    host: str = LOOPBACK_HOST,
    port: int = 0,
    open_browser: bool = True,
    initial_source: Optional[str] = None,
) -> None:
    """Run Chas Studio until interrupted, opening the browser by default."""

    server = _create_server(host=host, port=port, initial_source=initial_source)
    url = f"http://{LOOPBACK_HOST}:{server.server_port}/"
    print(f"Chas Studio is running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        opener = threading.Timer(0.12, webbrowser.open, args=(url,), kwargs={"new": 2})
        opener.daemon = True
        opener.start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = ["serve"]
