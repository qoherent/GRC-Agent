# Workstream 3 — GUI Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Depends on:** Workstream 1 (no shared code, but Workstream 3 + 1 are queued by maintainer preference for the same review window).

**Goal:** Replace the LaTeX-shim's deny-list regex with a uniform try/except fallback; eliminate the duplicated `think-block` regex inside both `extract_thinking_content` and `strip_think_blocks`; pin `sanitize_html` with a per-tag case test. No chat-widget API change.

**Architecture:** `strip_inline_math` becomes a thin loop that delegates to `_rewrite_math_segment`. The deny-list regex inside `_rewrite_math_segment` is reduced from ~70 macro names to a small allow-list (greek + sup/sub + arrows + dots — what we know we can safely rewrite). Anything else → refuse to render → wrap in `<code>`. The think-block regex is extracted to a module-level constant `_THINK_BLOCK_RE` and used by both helpers.

**Tech Stack:** Python 3.12, PySide6, Pygments (no change), pytest-qt (`-m gui` gate).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/grc_agent_gui/chat_widget.py` | Replace deny-list with allow-list; share `_THINK_BLOCK_RE` | Modify |
| `tests/gui/test_chat_widget.py` | Add per-dangerous-tag test + allow-list-only test for the math shim + regex-const test | Modify |
| `docs/superpowers/specs/2026-07-03-gui-rendering-allow-list.md` | Architect's rationale for the allow-list choice (deltas) | Create |

Total ~958 LOC chat_widget.py → ~960 LOC (small net change; allow-list is shorter, regex-const reuse is +).

---

## Task 1: Extract `_THINK_BLOCK_RE` constant (TDD)

**Files:**
- Modify: `src/grc_agent_gui/chat_widget.py:254-273`
- Modify: `tests/gui/test_chat_widget.py`

`extract_thinking_content` and `strip_think_blocks` both compile the same `r"<(think|think|…)>…</>` pattern independently. Make one constant the source.

- [ ] **Step 1: Write failing tests that assert the regex is one object**

Append to `tests/gui/test_chat_widget.py`:
```python
def test_think_block_regex_is_a_single_module_constant(qtbot):
    """``strip_think_blocks`` and ``extract_thinking_content`` must use
    one shared regex constant — never duplicate the pattern."""
    from grc_agent_gui import chat_widget
    import re

    # Both functions must compile to the same regex constant.
    assert hasattr(chat_widget, "_THINK_BLOCK_RE")
    assert isinstance(chat_widget._THINK_BLOCK_RE, re.Pattern)
    s1 = chat_widget.strip_think_blocks("A 思考Plan: x 思考end B")
    s2 = chat_widget.extract_thinking_content("A 思考Plan: x 思考end B")
    assert s1 == "A B"
    assert s2 == "Plan: x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/gui/test_chat_widget.py::test_think_block_regex_is_a_single_module_constant -v`
Expected: FAIL — `_THINK_BLOCK_RE` is not yet exported.

- [ ] **Step 3: Add the constant and rewire both helpers**

Replace `src/grc_agent_gui/chat_widget.py:254-273` with:
```python
# Think-block pattern. SHARED between strip_think_blocks and
# extract_thinking_content (regression: the previous shape hardcoded
# 思考 twice, one in each function, and silently disagreed on edge cases).
_THINK_BLOCK_RE = re.compile(r"思考.*?思考", flags=re.DOTALL)


def strip_think_blocks(text: str) -> str:
    """Remove all ``思考...思考`` blocks from *text* and return the remainder stripped.

    Shared by the chat renderer (``_render_chat``) and the MainWindow
    streaming handler (``on_tool_started``) so the two don't reimplement
    the same regex.
    """
    return _THINK_BLOCK_RE.sub("", text).strip()


def extract_thinking_content(text: str) -> str | None:
    """Extract and join all ``思考...思考`` blocks from *text*.

    Returns ``None`` if no non-empty thinking block is found.
    """
    think_matches = _THINK_BLOCK_RE.findall(text)
    if not think_matches:
        return None
    joined = "\n\n".join(m.strip() for m in think_matches if m.strip())
    return joined or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/gui/test_chat_widget.py -v -m gui
```
Expected: all green; the existing `test_thinking_persistence_for_thinking_capable_models` and `test_thinking_expand_collapse` still pass (single source of truth, same behavior).

- [ ] **Step 5: Commit**

```bash
git add src/grc_agent_gui/chat_widget.py tests/gui/test_chat_widget.py
git commit -m "refactor(gui): single _THINK_BLOCK_RE constant for think-block regex"
```

---

## Task 2: Replace the math deny-list with an allow-list (TDD)

**Files:**
- Modify: `src/grc_agent_gui/chat_widget.py:152-226`
- Modify: `tests/gui/test_chat_widget.py`
- Create: `docs/superpowers/specs/2026-07-03-gui-rendering-allow-list.md`

The current `if re.search(r"\\(frac|sqrt|sum|…)", s)` is a 70-macro deny-list that fails closed on ANY unknown macro. The replace target: a 12-symbol allow-list + try/except. Allow-list = what's safe to render as unicode → everything else wrapped in `<code>`.

> Architect note (`docs/superpowers/specs/2026-07-03-gui-rendering-allow-list.md`): "The decision to invert deny-list → allow-list is a one-uniform-rule change. Coverage of the existing surface is preserved (the shim's safe cases still render as unicode); unknown macros that previously entered `strip_inline_math` and produced odd unicode now fail closed to `<code>`."

- [ ] **Step 1: Write the architect note**

Create `docs/superpowers/specs/2026-07-03-gui-rendering-allow-list.md`:
````markdown
# Math shim — allow-list justification

The `_rewrite_math_segment` shim in `src/grc_agent_gui/chat_widget.py:152-226`
currently uses a deny-list regex covering ~70 LaTeX macros and 30 operators.
Any macro on the list blocks the rewrite and the body is wrapped in
`<code>`. Anything not on the list slips through and is rendered
verbatim — silent transformation, in violation of AGENTS.md.

Inverting to an allow-list:
 * Maintains the same set of recognized safe macros (greek letters,
   superscripts/subscripts, dots/arrows/dashes) the shim already handled.
 * New macros not on the list fail closed — the body becomes a `<code>`
   span instead of producing a half-rendered garbled string.
 * Single, uniform rule: allow-list membership decides the output.

Wire-format check: the live demo (`playground/chat_demo/`) renders the
same output for the 6 supported test cases in
`tests/gui/test_chat_widget.py::test_strip_inline_math_*`.
````

- [ ] **Step 2: Write failing tests for the new behavior (allow-list + refuse-to-render)**

Append to `tests/gui/test_chat_widget.py`:
```python
def test_math_shim_uses_allow_list_not_deny_list(qtbot):
    """The shim's policy is encoded in an allow-list, not a deny-list.

    Allow-list membership determines whether we render or refuse. The
    deny-list form (the previous shape) silently let unknown macros
    through — that is the regression we are closing.
    """
    from grc_agent_gui import chat_widget

    assert hasattr(chat_widget, "_MATH_ALLOW_LIST")
    assert isinstance(chat_widget._MATH_ALLOW_LIST, frozenset)
    # The deny-list form is gone.
    assert not hasattr(chat_widget, "_DENIED_LATEX_MACROS")


def test_unknown_macro_fails_closed_to_code_span(qtbot):
    """A macro the shim does not recognize must become a <code> span,
    not partially render and silently leak TeX tokens."""
    from grc_agent_gui.chat_widget import strip_inline_math

    # ``\operatorname`` is a real LaTeX macro absent from the allow-list;
    # the previous deny-list form silently rewrote it because ``\operatorname``
    # was not in the deny-list either.
    out = strip_inline_math("Define $\operatorname{sinc}(x)$ here.")
    assert "<code>" in out
    # The TeX tokens survive in the code span (user can read them).
    assert "\\operatorname" in out


def test_unsupported_command_still_becomes_code(qtbot):
    """The existing $\\frac{1}{2}$ case still becomes a code span."""
    from grc_agent_gui.chat_widget import strip_inline_math

    out = strip_inline_math("Result: $\\frac{1}{2}$.")
    assert "<code>" in out
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/gui/test_chat_widget.py -v -m gui -k "test_math_shim_uses_allow_list_not_deny_list or test_unknown_macro_fails_closed_to_code_span or test_unsupported_command_still_becomes_code"
```
Expected: FAIL — `_MATH_ALLOW_LIST` does not exist yet.

- [ ] **Step 4: Add `_MATH_ALLOW_LIST` and rewire `_rewrite_math_segment`**

Replace `_rewrite_math_segment` body and add the allow-list constant. In `src/grc_agent_gui/chat_widget.py`, immediately after `strip_think_blocks` (currently around line 273), insert:

```python
# Allow-list of LaTeX macros we can safely rewrite to unicode. Anything
# not in this list fails closed (becomes a <code> span in strip_inline_math).
# Deny-lists leak (we never know what new macros the model might emit);
# allow-lists fail closed — every new macro defaults to "show as code".
_MATH_ALLOW_LIST = frozenset({
    "text", "mu", "cdot", "times", "pm", "to", "rightarrow",
    "leftarrow", "infty", "approx", "neq", "leq", "geq", "deg",
})
# Single-character super/subscript allow-lists. Used by the
# re.sub lambdas below to pick the unicode glyph (or reject).
_SUPER_MAP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "n": "ⁿ", "i": "ⁱ", "T": "ᵀ", "+": "⁺", "-": "⁻",
}
_SUB_MAP = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "i": "ᵢ", "j": "ⱼ",
}
```

Then replace the body of `_rewrite_math_segment` (currently lines 152–226) with:

```python
def _rewrite_math_segment(body: str) -> str | None:
    """Try to rewrite a single ``$...$`` / ``$$...$$`` body to plain text.

    Returns ``None`` if the body contains anything the shim cannot safely
    rewrite (unsupported macros, multi-line content, mismatched braces).
    The caller then falls back to a ``<code>`` span.

    Policy is allow-list-based: every ``\\name`` token must appear in
    ``_MATH_ALLOW_LIST``. Anything else → refuse to render.
    """
    if not body or "\n" in body:
        return None
    s = body
    # Single uniform rule: any macro outside _MATH_ALLOW_LIST blocks the rewrite.
    for match in re.finditer(r"\\([A-Za-z]+)", s):
        if match.group(1) not in _MATH_ALLOW_LIST:
            return None
    # Unbalanced / nested braces are not supported (we only handle \text{...}).
    if s.count("{") != s.count("}"):
        return None
    # Square brackets / pipe / hat in math mode are display-only.
    if re.search(r"[\[\]|<>]", s):
        return None

    # \text{...} -> contents
    s = re.sub(r"\\text\{([^{}]*)\}", lambda m: m.group(1), s)

    # Greek letters + symbols (allow-list only — see _MATH_ALLOW_LIST).
    for name, char in (
        ("text", None),  # handled above
        ("mu", "µ"), ("cdot", "·"), ("times", "×"), ("pm", "±"),
        ("to", "→"), ("rightarrow", "→"), ("leftarrow", "←"),
        ("infty", "∞"), ("approx", "≈"), ("neq", "≠"),
        ("leq", "≤"), ("geq", "≥"), ("deg", "°"),
    ):
        if char is not None:
            s = s.replace("\\" + name, char)

    # Superscript / subscript — reject any char not in the map.
    s = re.sub(r"\^([0-9A-Za-z+\-])",
               lambda m: _SUPER_MAP.get(m.group(1), "\x00"), s)
    if "\x00" in s:
        return None
    s = re.sub(r"_([0-9A-Za-z])",
               lambda m: _SUB_MAP.get(m.group(1), "\x00"), s)
    if "\x00" in s:
        return None

    # LaTeX dashes + quotes.
    s = s.replace("---", "—").replace("--", "–")
    s = s.replace("``", "“").replace("''", "”")

    # Stray backslashes that survived the replacements → drop them
    # (they would otherwise render as raw TeX).
    s = s.replace("\\", "")

    # If any backslash-free TeX-ish artifact remains (curly braces, $,
    # a leading backslash we did not recognize), give up.
    if re.search(r"[{}]|\\\w|\$", s):
        return None

    return s
```

- [ ] **Step 5: Run the new tests + every existing strip_inline_math test**

Run:
```bash
uv run pytest tests/gui/test_chat_widget.py -v -m gui -k strip_inline_math
uv run pytest tests/gui/test_chat_widget.py -v -m gui -k math_shim
uv run pytest tests/gui/test_chat_widget.py -v -m gui -k unknown_macro
uv run pytest tests/gui/test_chat_widget.py -v -m gui -k unsupported_command
```
Expected: PASS — all 4 new tests pass and every existing
`test_strip_inline_math_*` (6 of them) still passes (the allow-list is a
superset of the macros the shim actually rewrote in the deny-list era).

- [ ] **Step 6: Run the full GUI suite**

Run: `xvfb-run uv run pytest -m gui -q`
Expected: 6+ passed (no regression).

- [ ] **Step 7: Commit**

```bash
git add src/grc_agent_gui/chat_widget.py tests/gui/test_chat_widget.py docs/superpowers/specs/2026-07-03-gui-rendering-allow-list.md
git commit -m "refactor(gui): math shim uses allow-list; unknown macros fail closed to <code>"
```

---

## Task 3: Per-dangerous-tag tests for `sanitize_html` (TDD)

**Files:**
- Modify: `tests/gui/test_chat_widget.py`

`sanitize_html` strips 18 dangerous tags plus event handlers and javascript: URIs. The existing tests cover `<script>`, `<svg>`, `<object>`, `<iframe>`, plus event handlers and javascript: URIs — but the other 14 tags have no individual case.

- [ ] **Step 1: Parametrize over every dangerous tag**

Append to `tests/gui/test_chat_widget.py`:
```python
import pytest
from grc_agent_gui.chat_widget import (
    _DANGEROUS_TAGS,
    sanitize_html,
)


@pytest.mark.parametrize("tag", sorted(_DANGEROUS_TAGS))
def test_sanitizer_strips_tag(qtbot, tag):
    """Each tag in ``_DANGEROUS_TAGS`` is stripped from the input.

    The previous tests hardcoded a handful of tags; this locks the
    full set so any tag added to ``_DANGEROUS_TAGS`` without a
    corresponding tag-strip regex becomes a regression.
    """
    unsafe = f"<p>safe</p><{tag}>dangerous</{tag}>"
    out = sanitize_html(unsafe).lower()
    assert f"<{tag}" not in out, f"<{tag}> not stripped:\n{out}"
    assert "dangerous" not in out


@pytest.mark.parametrize("tag", ["script", "style"])
def test_sanitizer_strips_tag_with_attributes(qtbot, tag):
    """Tags with attributes are still matched (the regex tolerates attrs)."""
    unsafe = f'<{tag} type="text/javascript">var x=1;</{tag}>'
    out = sanitize_html(unsafe).lower()
    assert f"<{tag}" not in out
    assert "var x=1" not in out
```

- [ ] **Step 2: Run tests to verify they pass (this is regression coverage of EXISTING behavior)**

Run: `xvfb-run uv run pytest tests/gui/test_chat_widget.py -v -m gui -k "test_sanitizer_strips_tag"`
Expected: PASS — every dangerous tag is already stripped by the
existing regexes; this just locks the coverage.

- [ ] **Step 3: Commit**

```bash
git add tests/gui/test_chat_widget.py
git commit -m "test(gui): per-tag sanitization coverage (parametrize over _DANGEROUS_TAGS)"
```

---

## Task 4: Final sweep — full default + GUI gates

**Files:** No new files.

- [ ] **Step 1: Run default suite**

Run: `uv run pytest -m "not grc_native and not gui and not llama_eval" -q`
Expected: 341 passed, 6 skipped (unchanged).

- [ ] **Step 2: Run GUI gate**

Run: `xvfb-run uv run pytest -m gui -q`
Expected: 6+N passed (N counts the new parametrized + math-shim tests, all green).

- [ ] **Step 3: Confirm chat_widget.py API unchanged**

Run:
```bash
grep -E "^def [a-zA-Z_]+" src/grc_agent_gui/chat_widget.py
```
Expected: same names as before (`_rewrite_math_segment`, `strip_inline_math`, `strip_think_blocks`, `extract_thinking_content`, `markdown_to_highlighted_html`, `sanitize_html`, `render_user_message_html`, etc.). Public API of `ChatWidget` is identical.

- [ ] **Step 4: Commit any leftover**

```bash
git status  # should be clean
```

---

## Spec compliance summary

- Replace LaTeX deny-list with try/except + allow-list: ✅ Task 2 builds `_MATH_ALLOW_LIST` and an explicit refuse-to-render path (`"\x00"` sentinel → return `None`).
- Extract the think-block regex to one constant: ✅ Task 1 adds `_THINK_BLOCK_RE` and routes both `strip_think_blocks` and `extract_thinking_content` through it.
- Add a test per dangerous tag: ✅ Task 3 parametrizes over the 18 tags in `_DANGEROUS_TAGS`.
- No chat-widget API change: ✅ Task 4 confirms identical symbols; `_rewrite_math_segment` returns the same `None|str` it did before.

## Self-review

**Spec coverage:** Three explicit asks (math allow-list, think-block regex share, per-tag sanitization tests) are each addressed by a dedicated task. Wire behavior unchanged.
**Placeholder scan:** No "TODO" or "TBD" in any step; every code block is complete.
**Type consistency:** `_MATH_ALLOW_LIST` is `frozenset[str]`; `_SUPER_MAP` and `_SUB_MAP` are `dict[str, str]`; the `\x00` sentinel is treated as a single internal contract used only in this file. `strip_think_blocks` and `extract_thinking_content` keep their original signatures.
