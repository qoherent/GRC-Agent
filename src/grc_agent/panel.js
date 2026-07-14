// Qoherent GRC Agent — dashboard logic. Extracted from panel.html (still
// vanilla, no build step). All functions stay top-level/global because every
// handler is bound via addEventListener in the wiring block at the bottom of
// this file (see there for why). The previously scattered `let` globals are
// consolidated into one `state` object: declared once at the very top, so
// every read/write goes through it and there is no temporal-dead-zone
// ordering hazard (an earlier bare `let isGrcLoaded` declared too low once
// threw a ReferenceError that silently killed the whole inline script).

const ICON_DIR = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>';
const ICON_FILE = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>';

// Two distinct canvas-placeholder messages: genuinely nothing loaded (the
// static HTML default) vs. a flowgraph that IS loaded (chat/inspect/undo
// all work) but whose visual canvas failed/timed out. Reusing one message
// for both — the bug this pair fixes — told a user with a fully working
// chat session that "no flowgraph" was loaded.
const CANVAS_PLACEHOLDER_EMPTY = "No flowgraph loaded. Click Browse to choose one.";
const CANVAS_PLACEHOLDER_CANVAS_FAILED = "Flowgraph loaded, but the canvas failed to connect. Browse to load a different file, or open this one again to retry.";

// Single source of truth for all mutable page-level state. Replacing a field
// here is the only way these flags ever change — nothing else holds a copy.
const state = {
  isGrcLoaded: false,
  lastGrcVersion: -1,
  lastPath: "/",
  brokenIframePolls: 0,
  browseDir: null,
  browseFocusReturnEl: null,
  browseTrapHandler: null,
  lastSentCanvasSize: null,
  openrouterKeySet: false,
  ollamaCloudKeySet: false,
  ollamaModel: "qwen3.6:35b-a3b-q4_K_M",
  openrouterModel: "deepseek/deepseek-v4-flash",
  ollamaCloudModel: "deepseek-v4-flash:cloud",
  // What the running chat Agent's model ACTUALLY is right now (from
  // /grc/settings' active_provider/active_model) — distinct from
  // provider/model above (the saved-to-disk preference, reflected in the
  // provider select + model name label) since a saved change never takes
  // effect without an app restart. Used only to decide whether the
  // restart-required badge should show; see updateRestartBadge().
  activeProvider: null,
  activeModel: null,
  // When the saved config failed to build at startup (e.g. OpenRouter with
  // no API key), this carries the error string so the dashboard can show a
  // specific message instead of a misleading "restart to apply" badge.
  activeProviderError: null,
  // Set right before resetChatFrame(true) (a chat-widget-only repair, e.g.
  // the auto-heal or "chat looks stuck" button) so pollConversationState's
  // own transition-to-"/" handler knows this particular reset must not
  // unload the flowgraph — the chat glitching is unrelated to whether a
  // file is loaded. Consumed (reset to false) the first time that handler
  // sees it, so it never suppresses a later, genuine abandon.
  preserveGrcOnReset: false,
  // True for the duration of the auto-reopen branch's /grc/open call
  // (pollConversationState), which can take up to the ~20s canvas-ready
  // deadline. state.lastPath is only written once that call settles, so
  // without this guard every 750ms tick in between re-reads the same
  // currentPath !== state.lastPath and fires ANOTHER /grc/open for the
  // same path — and each call unconditionally kills whatever canvas is
  // currently starting up (web.py's own "last-writer-wins" contract), so
  // overlapping calls can repeatedly kill a freshly spawned canvas before
  // it ever reaches /ready, a self-inflicted livelock that manufactures
  // its own "Timed out waiting for canvas to become ready".
  autoOpenInFlight: false,
  // Native chat widget: the active conversation id (null = fresh, no
  // messages yet). Replaces the iframe's pathname-as-conversation-id model.
  chatConvId: null,
  chatBusy: false,
  // Mirrors GRC's native Block Library panel visibility. Reset to false on
  // every fresh /grc/open (a new canvas_app.py subprocess always starts
  // with it hidden — see hide_panels_by_default), not just on page load.
  blocksPanelVisible: false,
};

// ===================== Native chat widget =====================
// Replaces the @pydantic/ai-chat-ui iframe. Talks directly to pydantic-ai's
// to_web() backend at POST /api/chat, which speaks the Vercel AI SDK UI
// Message Stream protocol (text/event-stream, `data: {json}\n\n` frames).
// The server is stateless — the client owns the full message history and
// sends it on every request. These `let`s live here (above restoreSession()'s
// load-time call to clearChatWidget) so there is no temporal-dead-zone risk.
let chatMessages = [];      // [{id, role, parts: [{type:'text', text}]}]
let chatAbort = null;       // AbortController for the in-flight request

function _chatId() {
  return crypto.randomUUID ? crypto.randomUUID()
    : "m-" + Date.now() + "-" + Math.random().toString(36).slice(2);
}

function _scrollChatToBottom() {
  const box = document.getElementById("chat-messages");
  if (box) box.scrollTop = box.scrollHeight;
}

function _appendChatMsg(role) {
  const box = document.getElementById("chat-messages");
  const empty = document.getElementById("chat-empty");
  if (empty) empty.remove();
  const msg = document.createElement("div");
  msg.className = `chat-msg ${role}`;
  const roleEl = document.createElement("div");
  roleEl.className = "chat-msg-role";
  roleEl.textContent = role;
  const bodyEl = document.createElement("div");
  bodyEl.className = "chat-msg-body";
  msg.append(roleEl, bodyEl);
  box.appendChild(msg);
  _scrollChatToBottom();
  return bodyEl;
}

function clearChatWidget() {
  if (chatAbort) { try { chatAbort.abort(); } catch (e) {} chatAbort = null; }
  chatMessages = [];
  state.chatConvId = null;
  state.chatBusy = false;
  const box = document.getElementById("chat-messages");
  if (box) {
    box.innerHTML = "";
    const empty = document.createElement("div");
    empty.id = "chat-empty";
    empty.textContent = state.isGrcLoaded
      ? "Start a conversation about your flowgraph."
      : "Load a flowgraph (Browse) to start chatting.";
    box.appendChild(empty);
  }
  updateChatInputState();
}

// Minimal SSE reader: splits a text/event-stream body into `data: ...`
// frames and parses each as JSON. Calls onData(parsedObj) per frame; a
// false return stops reading. The literal `[DONE]` marker is treated as
// {type:"done"}. The reader is always released.
async function _consumeSSEStream(body, onData) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    let keepGoing = true;
    while (keepGoing) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buf.indexOf("\n\n")) !== -1) {
        const rawFrame = buf.slice(0, sep);
        buf = buf.slice(sep + 2);
        const dataLines = rawFrame.split("\n")
          .filter(l => l.startsWith("data:"))
          .map(l => l.slice(5).trimStart());
        if (!dataLines.length) continue;
        const payload = dataLines.join("\n");
        if (payload === "[DONE]") { keepGoing = false; break; }
        try {
          const obj = JSON.parse(payload);
          if (onData(obj) === false) { keepGoing = false; break; }
        } catch (e) { /* ignore non-JSON keep-alive frames */ }
      }
    }
  } finally {
    try { reader.cancel(); } catch (e) {}
  }
}

// Minimal, dependency-free Markdown renderer for the subset LLM replies
// commonly use: fenced code blocks, inline code, bold, italic, headings, and
// line breaks. HTML is escaped BEFORE any markup is applied, and fenced code
// blocks are extracted first so their content is never touched by the inline
// rules — so agent output can never inject raw HTML/script into the page.
function _escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function renderMarkdown(text) {
  const blocks = [];
  // 1. Pull out fenced code blocks (```lang\n...\n```).
  let work = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_m, _lang, code) => {
    blocks.push(`<pre><code>${_escapeHtml(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000B${blocks.length - 1}\u0000`;
  });
  // 2. Escape everything else.
  work = _escapeHtml(work);
  // 3. Headings (longest marker first so ## isn't eaten by #).
  work = work.replace(/^######\s+(.*)$/gm, "<h6>$1</h6>")
            .replace(/^#####\s+(.*)$/gm, "<h5>$1</h5>")
            .replace(/^####\s+(.*)$/gm, "<h4>$1</h4>")
            .replace(/^###\s+(.*)$/gm, "<h3>$1</h3>")
            .replace(/^##\s+(.*)$/gm, "<h2>$1</h2>")
            .replace(/^#\s+(.*)$/gm, "<h1>$1</h1>");
  // 4. Bold then italic (bold first so ** wins over *).
  work = work.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  work = work.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  // 5. Inline code.
  work = work.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  // 6. Line breaks (code-block placeholders have no \n, so they're untouched).
  work = work.replace(/\n/g, "<br>");
  // 7. Restore code blocks (<pre> preserves their internal newlines).
  work = work.replace(/\u0000B(\d+)\u0000/g, (_m, i) => blocks[+i]);
  return work;
}

// pydantic-ai's default structured-output tool name (DEFAULT_OUTPUT_TOOL_NAME
// in pydantic_ai/_output.py) — used whenever an Agent's output_type includes
// exactly one non-str structured type with no custom ToolOutput(name=...).
// Both pydantic-ai's own Vercel AI SDK adapter and its reference chat UI
// (@pydantic/ai-chat-ui) treat this identically to any other tool call on
// the wire — there is no distinct frame type for it — so recognizing it here
// is the intended way for a consumer to render it as the actual reply
// instead of a raw Args/Result tool dump.
const FINAL_RESULT_TOOL_NAME = "final_result";

// The full chatMessages history is resent on every turn (the /api/chat
// backend is stateless), so anything ever stored in it — including
// conversations saved by an older version of this file — passes back through
// pydantic-ai's UI-message-part schema (CamelBaseModel, extra='forbid') on
// every subsequent send. A part shaped wrong in ANY single stored message
// fails validation for that whole part (and since the 9-member part union
// then matches nothing, the WHOLE request 500s) — permanently breaking every
// later turn of that conversation until fixed. Runs on every send so an
// already-corrupted saved conversation self-heals the moment it's used again,
// rather than requiring the user to notice and start a new conversation.
function _toOutgoingMessage(msg) {
  const parts = [];
  const toolPartByCallId = new Map();
  for (const part of msg.parts) {
    if (part.type === "reasoning") {
      // ReasoningUIPart is exactly {type, text, state} — an old saved
      // "reasoning" key (kept briefly for backward-compatible rendering)
      // must never be resent: its mere presence fails the part, not just
      // that field.
      const { reasoning, ...rest } = part;
      parts.push({ ...rest, text: part.text ?? reasoning ?? "" });
    } else if (part.type === "tool-call") {
      // Legacy shape (separate tool-call/tool-result parts) — merge into
      // the one-object-per-toolCallId shape the schema actually expects.
      const merged = {
        type: "tool-" + part.toolName,
        toolCallId: part.toolCallId,
        state: "input-available",
        input: part.args,
      };
      toolPartByCallId.set(part.toolCallId, merged);
      parts.push(merged);
    } else if (part.type === "tool-result") {
      const tp = toolPartByCallId.get(part.toolCallId);
      if (tp) {
        if (part.error !== undefined) {
          tp.state = "output-error";
          tp.errorText = part.error;
        } else {
          tp.state = "output-available";
          tp.output = part.result;
        }
      }
      // Never pushed on its own — folded into its matching tool-call above.
    } else {
      parts.push(part);
    }
  }
  return { ...msg, parts };
}

function _sanitizeOutgoingMessages(messages) {
  return messages.map(_toOutgoingMessage);
}

async function sendChatMessage(text) {
  if (state.chatBusy || !text.trim()) return;

  let isFirstMessage = false;
  if (!state.chatConvId) {
    state.chatConvId = _chatId();
    isFirstMessage = true;
  }

  if (isFirstMessage) {
    setUrlConvId(state.chatConvId);
    localStorage.setItem("grc_active_conv_id", state.chatConvId);
    const pathEl = document.querySelector("#current-path .value");
    const activePath = (pathEl && pathEl.textContent !== "-") ? pathEl.textContent : "";
    if (activePath) {
      addSessionHistory(state.chatConvId, activePath);
    }
  }

  // Captured now (not read fresh later) so a reset/new-conversation/clear-history
  // click mid-stream — which rebinds the module-level chatMessages to a new array
  // and nulls state.chatConvId — can't make this request's own success/error
  // handler splice a stale reply into whatever conversation is active by the time
  // this async call finally settles.
  const myConvId = state.chatConvId;
  const myMessages = chatMessages;

  myMessages.push({ id: _chatId(), role: "user", parts: [{ type: "text", text }] });
  saveConversationMessages(myConvId, myMessages);
  _appendChatMsg("user").textContent = text;

  const box = document.getElementById("chat-messages");
  const empty = document.getElementById("chat-empty");
  if (empty) empty.remove();
  const asstMsg = document.createElement("div");
  asstMsg.className = "chat-msg assistant";
  const roleEl = document.createElement("div");
  roleEl.className = "chat-msg-role";
  roleEl.textContent = "assistant";
  asstMsg.appendChild(roleEl);

  // Dynamic visual typing indicator
  const typingIndicator = document.createElement("div");
  typingIndicator.id = "chat-typing-indicator";
  typingIndicator.className = "typing-indicator";
  typingIndicator.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
  asstMsg.appendChild(typingIndicator);

  box.appendChild(asstMsg);
  _scrollChatToBottom();

  // Ordered parts: each text/reasoning/tool-call run is its own DOM element,
  // appended to asstMsg in strict arrival order — matching the Vercel AI
  // SDK's UIMessage.parts array (what @pydantic/ai-chat-ui itself renders
  // from). A single shared accumulator per event type — one reasoning box,
  // one tool box, one text buffer, as this used to be — collapses every
  // "thinking" segment into one stale block positioned ahead of all tool
  // calls, and every text segment into one block after them, regardless of
  // when each actually streamed in relative to the tool calls between them.
  let currentText = null;      // { el, acc } | null
  let currentReasoning = null; // { el, acc } | null
  let currentToolGroup = null; // the open .chat-msg-tools container | null
  const toolCalls = {};        // toolCallId -> { el, outEl, argsAcc }
  const finalResultCallIds = new Set();
  const textSegments = [];     // every closed text run's content, in order

  function endText() {
    if (currentText) { textSegments.push(currentText.acc); currentText = null; }
  }
  function endReasoning() {
    if (currentReasoning) {
      const summary = currentReasoning.el.querySelector("summary");
      if (summary) summary.textContent = "Thinking";
      currentReasoning = null;
    }
  }
  function endToolGroup() {
    currentToolGroup = null;
  }
  function updateToolElement(tc, status) {
    if (!tc) return;
    if (status) tc.status = status;
    const isOpen = tc.outEl.classList.contains("open");
    const arrow = isOpen ? "▾" : "▸";
    let suffix = "";
    if (tc.status === "done") {
      suffix = " ✓";
    } else if (tc.status === "error") {
      suffix = " ✗";
    }
    tc.el.textContent = `${arrow} called ${tc.el.dataset.name}${suffix}`;
  }
  function startText() {
    endReasoning();
    endToolGroup();
    const el = document.createElement("div");
    el.className = "chat-msg-body";
    asstMsg.appendChild(el);
    currentText = { el, acc: "" };
  }
  function appendText(delta) {
    if (!delta) return;
    if (!currentText) startText();
    currentText.acc += delta;
    currentText.el.innerHTML = renderMarkdown(currentText.acc);
    _scrollChatToBottom();
  }
  function startReasoning() {
    endText();
    endToolGroup();
    const el = document.createElement("details");
    el.className = "chat-msg-reasoning";
    el.innerHTML = "<summary>Thinking…</summary><div class='reasoning-body'></div>";
    asstMsg.appendChild(el);
    currentReasoning = { el, acc: "" };
  }
  function openToolGroup() {
    endText();
    endReasoning();
    if (!currentToolGroup) {
      currentToolGroup = document.createElement("div");
      currentToolGroup.className = "chat-msg-tools";
      asstMsg.appendChild(currentToolGroup);
    }
    return currentToolGroup;
  }
  function renderFinalResult(input) {
    if (!input || typeof input !== "object") return;
    // Always its own fresh bubble — never merged into whatever incidental
    // narration text preceded it, since this is a semantically distinct
    // "final answer", not a continuation of it.
    endText();
    endReasoning();
    endToolGroup();
    if (typeof input.explanation === "string" && input.explanation) {
      appendText(input.explanation);
    }
    if (Array.isArray(input.actions_taken) && input.actions_taken.length) {
      endText();
      const ul = document.createElement("ul");
      ul.className = "chat-msg-actions";
      for (const action of input.actions_taken) {
        const li = document.createElement("li");
        li.textContent = String(action);
        ul.appendChild(li);
      }
      asstMsg.appendChild(ul);
    }
  }

  state.chatBusy = true;
  updateChatInputState();
  chatAbort = new AbortController();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: chatAbort.signal,
      body: JSON.stringify({
        trigger: "submit-message",
        id: myConvId,
        messages: _sanitizeOutgoingMessages(myMessages),
      }),
    });
    if (!res.ok || !res.body) {
      const errText = await res.text().catch(() => res.statusText);
      throw new Error(`Chat request failed (${res.status}): ${errText}`);
    }
    let assistantParts = [];
    let hasRemovedTyping = false;
    await _consumeSSEStream(res.body, (data) => {
      if (!hasRemovedTyping) {
        hasRemovedTyping = true;
        const typingEl = asstMsg.querySelector("#chat-typing-indicator");
        if (typingEl) typingEl.remove();
      }
      switch (data.type) {
        case "text-delta":
          if (typeof data.delta === "string") {
            appendText(data.delta);
            let lastPart = assistantParts[assistantParts.length - 1];
            if (!lastPart || lastPart.type !== "text") {
              lastPart = { type: "text", text: "" };
              assistantParts.push(lastPart);
            }
            lastPart.text += data.delta;
          }
          break;
        case "reasoning-start":
          startReasoning();
          // pydantic-ai's ReasoningUIPart (the schema /api/chat validates
          // against) is exactly {type, text, state} with extra='forbid' — no
          // separate `reasoning` field. A stray extra key here doesn't just
          // get ignored on the next send: it fails validation for the WHOLE
          // part, and since no other member of the 9-part union matches
          // type:"reasoning" either, the ENTIRE request 500s — breaking every
          // later turn of the conversation, not just this one.
          assistantParts.push({ type: "reasoning", text: "", state: "streaming" });
          break;
        case "reasoning-delta":
          if (currentReasoning && typeof data.delta === "string") {
            currentReasoning.acc += data.delta;
            const body = currentReasoning.el.querySelector(".reasoning-body");
            if (body) body.textContent = currentReasoning.acc;
            _scrollChatToBottom();

            let lastPart = assistantParts[assistantParts.length - 1];
            if (lastPart && lastPart.type === "reasoning") {
              lastPart.text = (lastPart.text || "") + data.delta;
            }
          }
          break;
        case "reasoning-end":
          endReasoning();
          {
            let lastPart = assistantParts[assistantParts.length - 1];
            if (lastPart && lastPart.type === "reasoning") {
              lastPart.state = "done";
            }
          }
          break;
        case "tool-input-start":
          if (data.toolName === FINAL_RESULT_TOOL_NAME) {
            finalResultCallIds.add(data.toolCallId);
          }
          if (data.toolName) {
            // pydantic-ai's tool part schema is ONE evolving object per
            // toolCallId — `type` matches ^tool- (i.e. "tool-<toolName>"),
            // with `state` progressing input-streaming -> input-available ->
            // output-available/output-error/output-denied. There's no
            // toolName/args field (not part of the schema, and rejected as
            // extra — same failure mode as the reasoning part above); the
            // tool's name is recovered from `type` when re-rendering saved
            // history (see renderChatMessagesFromHistory).
            assistantParts.push({
              type: "tool-" + data.toolName,
              toolCallId: data.toolCallId,
              state: "input-streaming",
              input: null,
            });
          }
          if (data.toolName === FINAL_RESULT_TOOL_NAME) {
            break;
          }
          if (data.toolName) {
            const group = openToolGroup();
            const el = document.createElement("div");
            el.className = "chat-tool pending";
            el.dataset.name = data.toolName;
            const outEl = document.createElement("div");
            outEl.className = "chat-tool-output";
            const tc = { el, outEl, argsAcc: "", status: "pending" };
            updateToolElement(tc);
            el.addEventListener("click", () => {
              outEl.classList.toggle("open");
              updateToolElement(tc);
            });
            group.appendChild(el);
            group.appendChild(outEl);
            toolCalls[data.toolCallId] = tc;
            _scrollChatToBottom();
          }
          break;
        case "tool-input-delta":
          // Partial input text has no home in the outgoing schema (no
          // "input so far" string field) — it stays a DOM-only preview via
          // tc.argsAcc below, never written into assistantParts.
          if (finalResultCallIds.has(data.toolCallId)) break;
          { const tc = toolCalls[data.toolCallId];
            if (tc && typeof data.inputTextDelta === "string") {
              tc.argsAcc += data.inputTextDelta;
              if (tc.outEl.classList.contains("open")) {
                tc.outEl.textContent = "Args:\n" + tc.argsAcc;
              }
            } }
          break;
        case "tool-input-available":
          {
            const part = assistantParts.find(p => p.toolCallId === data.toolCallId);
            if (part) {
              part.input = data.input;
              part.state = "input-available";
            }
          }
          if (finalResultCallIds.has(data.toolCallId)) {
            renderFinalResult(data.input);
            break;
          }
          { const tc = toolCalls[data.toolCallId];
            if (tc && data.input !== undefined) {
              tc.argsAcc = typeof data.input === "string" ? data.input : JSON.stringify(data.input, null, 2);
              if (tc.outEl.classList.contains("open")) {
                tc.outEl.textContent = "Args:\n" + tc.argsAcc;
              }
            } }
          break;
        case "tool-output-available":
          {
            const part = assistantParts.find(p => p.toolCallId === data.toolCallId);
            if (part) {
              part.output = data.output;
              part.state = "output-available";
            }
          }
          if (finalResultCallIds.has(data.toolCallId)) break;
          { const tc = toolCalls[data.toolCallId];
            if (tc) {
              tc.el.className = "chat-tool done";
              const out = data.output;
              tc.outEl.textContent = (tc.argsAcc ? "Args:\n" + tc.argsAcc + "\n\nResult:\n" : "Result:\n")
                + (typeof out === "string" ? out : JSON.stringify(out, null, 2) || "(empty)");
              updateToolElement(tc, "done");
            } }
          break;
        case "tool-output-error":
        case "tool-output-denied":
          {
            const part = assistantParts.find(p => p.toolCallId === data.toolCallId);
            const errTxt = data.errorText || data.error?.message || (data.type === "tool-output-denied" ? "denied" : "error");
            if (part) {
              part.state = data.type === "tool-output-denied" ? "output-denied" : "output-error";
              part.errorText = errTxt;
            }
          }
          if (finalResultCallIds.has(data.toolCallId)) break;
          { const tc = toolCalls[data.toolCallId];
            if (tc) {
              tc.el.className = "chat-tool error";
              tc.outEl.textContent = data.errorText || data.error?.message || "error";
              updateToolElement(tc, "error");
            } }
          break;
        case "error":
          throw new Error(data.error?.message || data.errorText || "stream error");
        case "done":
          return false;
      }
      return true;
    });
    endText();
    myMessages.push({
      id: _chatId(),
      role: "assistant",
      parts: assistantParts,
    });
    saveConversationMessages(myConvId, myMessages);
  } catch (e) {
    endText();
    let errText = "";
    if (e.name === "AbortError") {
      errText = "[aborted]";
      const el = document.createElement("div");
      el.className = "chat-msg-body";
      el.textContent = textSegments.length ? textSegments.join("\n\n") + "\n\n[aborted]" : "[aborted]";
      asstMsg.appendChild(el);
    } else {
      errText = "Error: " + (e.message || String(e));
      const el = document.createElement("div");
      el.className = "chat-msg-body error";
      el.textContent = errText;
      asstMsg.appendChild(el);
      console.error("Chat stream failed:", e);
    }
    assistantParts.push({ type: "text", text: textSegments.length ? textSegments.join("\n\n") + "\n\n" + errText : errText });
    myMessages.push({
      id: _chatId(),
      role: "assistant",
      parts: assistantParts,
    });
    saveConversationMessages(myConvId, myMessages);
  } finally {
    const typingEl = asstMsg.querySelector("#chat-typing-indicator");
    if (typingEl) typingEl.remove();
    // Superseded by a reset/new-conversation/clear-history while in flight —
    // chatAbort/chatBusy now belong to whatever request (if any) is active for
    // the CURRENT conversation, not this one; touching them here would corrupt
    // that unrelated request's own state.
    if (state.chatConvId !== myConvId) return;
    chatAbort = null;
    state.chatBusy = false;
    updateChatInputState();
    // Disabling a focused input forces a native blur with no automatic
    // refocus — restore it once re-enabled, but only if the user hasn't
    // since focused something else (e.g. opened Browse) while busy.
    if (document.activeElement === document.body) {
      document.getElementById("chat-input")?.focus();
    }
  }
}

function initChatWidget() {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  if (!form || !input) return;
  clearChatWidget();
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || state.chatBusy) return;
    input.value = "";
    input.style.height = "auto";
    sendChatMessage(text);
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
}

// Reset the chat widget to a fresh empty conversation. Each conversation is
// locked to exactly one flowgraph (mirrors the old desktop GUI's open_file(),
// which cleared the chat and reset the agent session on every load) — a chat
// history should never span two different underlying .grc files.
function resetChatFrame(preserveGraph = false) {
  localStorage.removeItem("grc_active_conv_id");
  setUrlConvId(null);
  if (preserveGraph) state.preserveGrcOnReset = true;
  state.chatConvId = null;
  clearChatWidget();
}

// For callers that are ABANDONING whatever's currently loaded to start
// fresh (the "+" button, Clear History, a stuck-chat reset) — NOT for
// openGraph, which calls resetChatFrame() directly instead: openGraph just
// successfully loaded a file, and by the time its own await resolves the
// always-running 750ms pollConversationState has often already noticed the
// version bump and flipped state.isGrcLoaded true, so routing through this
// unload guard here would immediately close the file openGraph just opened
// (live-reproduced: /grc/close fired ~2.6s after /grc/open on every run).
function startNewConversation() {
  resetChatFrame();
  // If a file was loaded but the user never sent a message yet, the
  // iframe's pathname is already "/" and never changes here — so
  // pollConversationState's own path-transition check (which is what
  // normally unloads the flowgraph and brings the Browse button back)
  // never fires. Without this, loading a file then wanting to load a
  // different one before chatting had no way back: Browse lives inside
  // the now-hidden empty-state placeholder, and there'd be nothing left
  // to click.
  if (state.isGrcLoaded) {
    unloadGrc();
  }
}

// A conversation id that no longer resolves to anything (its IndexedDB
// entry was cleared some other way, or the URL was hand-edited) leaves
// the vendor widget rendering nothing — no textarea, no sidebar, no send
// button — which makes every OTHER recovery control hide itself too,
// since they all work by finding something inside that now-empty iframe.
// This one deliberately doesn't depend on iframe content at all, so it's
// always clickable no matter how broken the chat pane looks, and a
// reload persists into the exact same broken state via
// grc_active_conv_id — so clear that too, not just start a new src.
//
// Deliberately calls resetChatFrame(true) directly, NOT startNewConversation()
// — the chat widget looking broken has nothing to do with whether a
// flowgraph is loaded. This used to go through startNewConversation(),
// which unconditionally closes any loaded file and kills its live canvas
// process — meaning the auto-heal below (and the "chat looks stuck?"
// button) silently destroyed unrelated, perfectly-working work every time
// the chat widget merely glitched (live-reproduced: a corrupted
// localStorage entry, or simply this button, closed the loaded file with
// zero relation between the two and no confirmation, unlike Clear History).
function resetConversation() {
  state.brokenIframePolls = 0;
  resetChatFrame(true);
}

// Native widget: the active-conversation "path" is derived from the widget's
// own conversation id ("/" = fresh, "/{id}" = active) — preserves the
// pathname contract the rest of the state machine keys off of, without an
// iframe contentWindow to read.
function getChatFramePath() {
  return state.chatConvId ? "/" + state.chatConvId : "/";
}

// The dashboard's own URL (as opposed to the chat iframe's internal one,
// invisible to the browser's address bar) previously never changed at all —
// always exactly /grc/panel regardless of which conversation was active, so
// a reload or a shared link could only ever fall back to whatever
// localStorage happened to hold. Mirrors that value into a `conv` query
// param via replaceState (not pushState — this tracks "what's currently
// shown," it isn't meant to grow browser history per conversation) so the
// visible URL actually reflects the active session, and restoreSession() can
// honor it on load.
function setUrlConvId(convId) {
  const url = new URL(window.location);
  if (convId && convId !== "/") {
    url.searchParams.set("conv", convId.replace(/^\//, ""));
  } else {
    url.searchParams.delete("conv");
  }
  history.replaceState(null, "", url);
}

function restoreSession() {
  const urlParams = new URLSearchParams(window.location.search);
  const convId = urlParams.get("conv");
  if (convId) {
    const entries = loadSessionHistory();
    const entry = entries.find(e => e.convId === convId);
    if (entry) {
      openGraph(entry.path, convId);
      return;
    }
  }


  clearChatWidget();
}
restoreSession();

function clearHistory() {
  if (!confirm("Delete all conversations? This cannot be undone.")) return;

  try {
    localStorage.removeItem("grc_session_history");
    localStorage.removeItem("grc_active_conv_id");
    const keysToRemove = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith("grc_messages_")) {
        keysToRemove.push(key);
      }
    }
    for (const key of keysToRemove) {
      localStorage.removeItem(key);
    }
  } catch (e) {}

  startNewConversation();
  unloadGrc();
}

// The vendor widget uses pathname "/" as its own "fresh conversation"
// sentinel (confirmed via direct inspection) — it flips away from "/" as
// soon as a real conversation exists. Same-origin iframe, so the parent
// can read it directly: poll it to gate Browse, since a conversation in
// progress must not have its flowgraph swapped out from under it — only
// starting a new conversation (this page's own load, or the widget's own
// "New conversation" button) should re-enable loading a different file.

async function pollConversationState() {
  // A previous tick's auto-reopen /grc/open is still awaiting its (up to
  // ~20s) canvas-ready outcome — see autoOpenInFlight's own comment. Skip
  // this whole tick rather than re-detecting the same still-pending
  // transition and firing a duplicate open.
  if (state.autoOpenInFlight) return;
  const btn = document.getElementById("browse-btn");
  let currentPath = getChatFramePath();
  if (currentPath === undefined) return;
  const fresh = currentPath === "/";

  btn.disabled = !fresh;
  btn.title = fresh ? "" : "Start a new conversation to load a different file";

  // Check version changes on backend to auto-refresh if agent changed params!
  try {
    const statusRes = await fetch("/grc/status").then(r => r.json());
    if (statusRes.version !== undefined && statusRes.version !== state.lastGrcVersion) {
      state.lastGrcVersion = statusRes.version;
      await refresh();
    }
  } catch (e) {}

  // Version polling above handles agent-driven auto-refresh. The iframe-era
  // pathname→file auto-open/session-mapping is gone (the file is loaded
  // explicitly via Browse/openGraph; new-conversation unload is driven
  // directly by startNewConversation()->unloadGrc). Full widget history
  // persistence is Phase 2.
  state.lastPath = currentPath;
  updateChatInputState();
}

async function unloadGrc() {
  try {
    await fetch("/grc/close", { method: "POST" });
    await refresh();
  } catch (e) {
    console.error("Failed to unload GRC:", e);
  }
}

// ---- Undo/redo: a shared, disk-based snapshot stack (adapter.py) covering
// both agent (change_graph) and manual-canvas edits — see AGENTS.md. Button
// enabled/disabled state is synced from /grc/status's can_undo/can_redo in
// refresh() itself, not tracked separately here.
async function doUndo() {
  try {
    const res = await fetch("/grc/undo", { method: "POST" }).then(r => r.json());
    if (!res.ok) { setMsg(res.message || "Nothing to undo.", "error"); return; }
    await refresh();
  } catch (e) {
    setMsg(String(e), "error");
  }
}

async function doRedo() {
  try {
    const res = await fetch("/grc/redo", { method: "POST" }).then(r => r.json());
    if (!res.ok) { setMsg(res.message || "Nothing to redo.", "error"); return; }
    await refresh();
  } catch (e) {
    setMsg(String(e), "error");
  }
}

// /grc/inspect's validation field is otherwise only re-checked when
// pollConversationState notices /grc/status's version counter change —
// this button forces that same check (and the same render()-driven
// pill/message update) on demand.
async function doValidate() {
  await refresh();
}

// Toggles GRC's native Block Library panel (a real GTK widget in the
// canvas_app.py subprocess, not DOM content — see grc_canvas_blocks_panel).
// Self-disables for the duration of its own request: stricter than
// doUndo/doRedo above, needed here because this button also maintains its
// own optimistic "is it open" mirror (state.blocksPanelVisible), which two
// overlapping requests could otherwise resolve out of order.
async function toggleBlocksPanel() {
  const btn = document.getElementById("blocks-panel-toggle-btn");
  if (!btn || btn.disabled) return;
  const desired = !state.blocksPanelVisible;
  btn.disabled = true;
  try {
    const res = await fetch("/grc/canvas/blocks-panel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visible: desired }),
    }).then(r => r.json());
    if (res.ok) {
      state.blocksPanelVisible = desired;
      // Showing/hiding the Block Library adds/removes a whole pane in the
      // real GTK window, which can grow the window's total size request
      // past the iframe's actual bounds (GRC's block-tree ScrolledWindow
      // hardcodes its own minimum width) — forcibly re-send the unchanged
      // canvas-container size so canvas_app.py re-clamps the GTK window
      // back to exactly what's visible, the same fix syncCanvasSize
      // already applies on a real pane resize. force=true bypasses its
      // dedup-by-size-key guard, since the container's own DOM size here
      // is unchanged (only the GTK-side content behind it changed).
      syncCanvasSize(true);
    } else {
      setMsg(res.message || "Failed to toggle Block Library panel.", "error");
    }
  } catch (e) {
    setMsg(String(e), "error");
  } finally {
    updateBlocksPanelButton();
  }
}

function updateBlocksPanelButton() {
  const btn = document.getElementById("blocks-panel-toggle-btn");
  if (!btn) return;
  btn.disabled = !state.isGrcLoaded;
  btn.classList.toggle("active", state.blocksPanelVisible);
  btn.setAttribute("aria-pressed", String(state.blocksPanelVisible));
  btn.title = state.blocksPanelVisible ? "Hide Block Library" : "Show Block Library";
}

setInterval(pollConversationState, 750);

function updateChatInputState() {
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send-btn");
  const formLoading = document.getElementById("chat-form-loading");
  if (!input || !sendBtn) return;
  const enable = state.isGrcLoaded && !state.chatBusy;
  input.disabled = !enable;
  if (state.chatBusy) {
    sendBtn.style.display = "none";
    if (formLoading) formLoading.style.display = "flex";
  } else {
    sendBtn.style.display = "";
    sendBtn.disabled = !enable;
    if (formLoading) formLoading.style.display = "none";
  }
  input.placeholder = state.isGrcLoaded
    ? "Ask about your flowgraph…"
    : "Load a flowgraph (Browse) to start chatting.";
}

function setMsg(text, cls) {
  const el = document.getElementById("msg");
  el.textContent = text || "";
  el.className = text ? `visible ${cls || ""}` : "";
}

async function openGraph(path, convId = null) {
  if (!path) return;
  setMsg("Loading...");
  try {
    const pixelRatio = window.devicePixelRatio || 1;
    const res = await fetch("/grc/open", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path, pixel_ratio: pixelRatio})
    });
    const data = await res.json();
    if (!data.ok) { setMsg(data.message || "Failed to load file.", "error"); return; }

    // A fresh canvas_app.py subprocess always starts with the Block Library
    // hidden (hide_panels_by_default) — reset the frontend's mirror to match,
    // not just on first page load.
    state.blocksPanelVisible = false;

    if (convId) {
      state.chatConvId = convId;
      localStorage.setItem("grc_active_conv_id", convId);
      setUrlConvId(convId);
      chatMessages = loadConversationMessages(convId);
      renderChatMessagesFromHistory();
    } else {
      // Record a session-history entry right away — a real conversation id
      // doesn't exist yet (the vendor chat widget only mints one once the
      // first message is sent), so this is a "pending" entry (no convId).
      // It's upgraded to the real conversation once the user actually
      // chats (see pollConversationState) — without this, a file load that
      // never turns into a chat message would never show up anywhere.
      addSessionHistory(null, data.path);
      // resetChatFrame(), NOT startNewConversation() — the latter's
      // unload-if-loaded guard would immediately close the file this call
      // just opened (see startNewConversation's own comment).
      resetChatFrame();
    }
    if (data.canvas_ready === false) {
      // The flowgraph itself DID load (chat/inspect work) — only the
      // canvas subprocess failed/timed out. refresh() itself now sets the
      // error message (in the right order relative to its own render(),
      // which would otherwise clear a stale one) and doesn't point the
      // iframe at a display with no live GTK client (broadway.js's own
      // reconnect-free alert("disconnected") otherwise).
      await refresh(false, false, data.canvas_error);
    } else {
      setMsg(`Loaded ${data.path}`, "ok");
      // forceCanvasReload: a brand-new canvas_app.py just connected to
      // Broadway; force the iframe to re-init broadway.js so it doesn't
      // sit on a stale/disconnected WebSocket from the prior canvas.
      await refresh(true);
      setTimeout(() => setMsg(""), 2500);
    }
  } catch (e) {
    setMsg(String(e), "error");
  }
}

// ---- Server-side directory browser (the web analog of the old GUI's
// native QFileDialog: a browser can never hand back a real filesystem
// path from its own picker, but this server runs on the same machine as
// the files, so browsing server-side and loading by the real path it
// already knows reproduces the same "click a .grc file, it loads" flow.
async function openBrowse() {
  state.browseFocusReturnEl = document.activeElement;
  document.getElementById("browse-overlay").classList.add("open");
  await browseTo();

  // The dialog is visually modal (click-outside and Escape both close it,
  // see the listeners at the bottom of this script) but nothing stopped
  // Tab from cycling through the page behind it — trap it within the
  // dialog's own focusable elements while open.
  const dialog = document.getElementById("browse-dialog");
  state.browseTrapHandler = (e) => {
    if (e.key !== "Tab") return;
    const focusable = Array.from(dialog.querySelectorAll("button, [tabindex]"))
      .filter(el => !el.disabled);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  dialog.addEventListener("keydown", state.browseTrapHandler);
}

function closeBrowse() {
  document.getElementById("browse-overlay").classList.remove("open");
  const dialog = document.getElementById("browse-dialog");
  if (state.browseTrapHandler) {
    dialog.removeEventListener("keydown", state.browseTrapHandler);
    state.browseTrapHandler = null;
  }
  if (state.browseFocusReturnEl && typeof state.browseFocusReturnEl.focus === "function") {
    state.browseFocusReturnEl.focus();
  }
  state.browseFocusReturnEl = null;
}

async function browseUp() {
  const res = await fetch(`/grc/browse?dir=${encodeURIComponent(state.browseDir)}`).then(r => r.json());
  if (res.ok && res.parent) await browseTo(res.parent);
}

// Both the Browse dialog's file/folder rows and the Recent Sessions rows
// are built as plain divs (needed for the icon + text layout `<button>`
// makes awkward to style consistently with the rest of the dashboard) —
// without this, they were mouse-only: no tabindex, no role, no keydown
// handling, so a keyboard-only user could open the dialog but never
// select anything inside it, or ever resume a past session.
function makeRowFocusable(row, activate) {
  row.tabIndex = 0;
  row.setAttribute("role", "button");
  row.onclick = activate;
  row.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      activate();
    }
  });
}

async function browseTo(dir) {
  const url = dir ? `/grc/browse?dir=${encodeURIComponent(dir)}` : "/grc/browse";
  const res = await fetch(url).then(r => r.json());
  if (!res.ok) { setMsg(res.message || "Browse failed.", "error"); return; }
  state.browseDir = res.dir;
  document.getElementById("browse-dir").textContent = res.dir;
  document.getElementById("browse-up-btn").disabled = !res.parent;
  const list = document.getElementById("browse-list");
  list.innerHTML = "";
  for (const entry of res.entries) {
    const row = document.createElement("div");
    row.className = "browse-entry" + (entry.is_dir ? " dir" : "");
    row.innerHTML = `${entry.is_dir ? ICON_DIR : ICON_FILE}<span>${entry.name}</span>`;
    makeRowFocusable(row, entry.is_dir
      ? () => browseTo(entry.path)
      : async () => { closeBrowse(); await openGraph(entry.path); });
    list.appendChild(row);
  }
  // First row is the natural place for focus to land after navigating
  // into a directory (mirrors what a native file dialog does).
  const firstRow = list.firstElementChild;
  if (firstRow) {
    firstRow.focus();
  } else {
    const empty = document.createElement("div");
    empty.className = "browse-empty";
    empty.textContent = "No .grc files here.";
    list.appendChild(empty);
  }
}

function render(graph) {
  const validation = document.getElementById("validation");
  const valid = graph.validation.status === "valid";
  validation.innerHTML = `<span class="pill ${valid ? "valid" : "invalid"}"><span class="dot"></span>${graph.validation.status}</span>`;
  if (!valid && graph.validation.errors.length) {
    setMsg(graph.validation.errors.join(" | "), "error");
  } else {
    const msgEl = document.getElementById("msg");
    if (msgEl && msgEl.classList.contains("error")) {
      setMsg("", "ok");
    }
  }
}

function renderEmptyState() {
  document.getElementById("validation").innerHTML = "";
  // A canvas_error from a previous, unrelated open attempt (e.g. "Timed out
  // waiting for canvas to become ready") otherwise survives indefinitely
  // through the transition back to "nothing loaded" — nothing else ever
  // clears #msg on this path (render() only clears it on a valid, loaded
  // graph), so a stale error can sit on screen next to "No flowgraph
  // loaded" long after the attempt it described is over.
  setMsg("", "");
  const canvasIframe = document.getElementById("canvas-iframe");
  const canvasPlaceholder = document.getElementById("canvas-placeholder");
  const placeholderText = document.getElementById("canvas-placeholder-text");
  if (placeholderText) placeholderText.textContent = CANVAS_PLACEHOLDER_EMPTY;
  if (canvasIframe && canvasPlaceholder) {
    canvasIframe.style.display = "none";
    canvasIframe.src = "about:blank";
    canvasPlaceholder.style.display = "flex";
  }
}

async function refresh(forceCanvasReload = false, canvasReady, canvasError) {
  try {
    const [statusRes, inspectRes] = await Promise.all([
      fetch("/grc/status").then(r => r.json()),
      fetch("/grc/inspect").then(r => r.json())
    ]);
    document.querySelector("#current-path .value").textContent = statusRes.path || "-";
    if (statusRes.version !== undefined) {
      state.lastGrcVersion = statusRes.version;
    }
    // Show a "Building knowledge database..." banner while the vector DB is
    // being built on first query_knowledge (or after a provider switch that
    // changes the embedding model). The banner clears itself once the build
    // finishes (status flips to "ready" or "failed").
    if (statusRes.rag_building?.status === "building") {
      setMsg("Building knowledge database (first use may take a few minutes)...", "info");
    } else if (statusRes.rag_building?.status === "failed") {
      setMsg("Knowledge database build failed. Try again later.", "error");
    }
    // Ahead of the not_loaded/inspect-failure branches below so both
    // buttons are correctly disabled in either case too (no file loaded ->
    // nothing to undo/redo).
    document.getElementById("undo-btn").disabled = !statusRes.can_undo;
    document.getElementById("redo-btn").disabled = !statusRes.can_redo;
    if (inspectRes.not_loaded) {
      state.isGrcLoaded = false;
      updateBlocksPanelButton();
      renderEmptyState();
      return;
    }
    if (!inspectRes.ok) {
      state.isGrcLoaded = false;
      updateBlocksPanelButton();
      setMsg("Inspect failed.", "error");
      return;
    }
    state.isGrcLoaded = true;
    updateBlocksPanelButton();
    render(inspectRes.graph);
    // Prefer an explicitly-passed, just-obtained value — openGraph and the
    // auto-reopen path already have the freshest possible signal from their
    // own /grc/open call, no need to wait on this same function's own
    // /grc/status round-trip. Otherwise fall back to /grc/status's
    // canvas_ready (persisted server-side, see web.py's canvas_ready_state),
    // not a hardcoded true — without this, a bare refresh() call made well
    // after a real canvas failure (doUndo, doRedo, doValidate, the
    // version-bump poll in pollConversationState) would blindly try to
    // re-point the iframe at a canvas already known to be dead.
    const effectiveCanvasReady =
      canvasReady !== undefined ? canvasReady : statusRes.canvas_ready !== false;
    const canvasIframe = document.getElementById("canvas-iframe");
    const canvasPlaceholder = document.getElementById("canvas-placeholder");
    if (!effectiveCanvasReady) {
      // The canvas subprocess is known dead for this open (its readiness
      // wait already timed out) — pointing the iframe at it would just
      // trigger broadway.js's own reconnect-free alert("disconnected").
      // The flowgraph IS loaded (chat/inspect above still work); only the
      // visual canvas is unavailable, so leave the placeholder showing
      // instead of a doomed connection attempt.
      const placeholderText = document.getElementById("canvas-placeholder-text");
      if (placeholderText) placeholderText.textContent = CANVAS_PLACEHOLDER_CANVAS_FAILED;
      if (canvasIframe) canvasIframe.style.display = "none";
      if (canvasPlaceholder) canvasPlaceholder.style.display = "flex";
      // Set here (after render() above, which is the only thing that would
      // otherwise clear a stale error-class #msg) rather than leaving each
      // caller responsible for it — otherwise a canvas failure discovered
      // through a bare refresh() call (doUndo, doRedo, doValidate, the
      // version-bump poll, or simply a fresh page load re-fetching
      // /grc/status) never shows the banner at all, only the placeholder
      // text above, even though the same real condition holds.
      const effectiveCanvasError =
        canvasError !== undefined ? canvasError : statusRes.canvas_error;
      setMsg(
        effectiveCanvasError || "Flowgraph loaded, but the canvas failed to connect.",
        "error"
      );
      return;
    }
    // Broadway URL comes from the server (env-overridable port) so this
    // isn't hardcoded to 8085 — falls back only if the server didn't send it.
    const broadwayUrl = statusRes.broadway_url || "http://localhost:8085/";
    if (canvasIframe && canvasPlaceholder) {
      canvasPlaceholder.style.display = "none";
      canvasIframe.style.display = "block";
      // On a fresh open (forceCanvasReload), force a full reload with a
      // cache-busting query so broadway.js re-initializes its WebSocket
      // against the now-connected GTK client. Without this, a second open
      // reuses the stale iframe and never recovers from the prior session's
      // unrecoverable alert("disconnected"). On the version-change polling
      // path, keep the equality guard so agent edits don't flicker the
      // canvas. (Query strings are harmless: broadway.js strips to /socket.)
      if (forceCanvasReload) {
        canvasIframe.src = broadwayUrl + "?g=" + Date.now();
      } else if (canvasIframe.getAttribute("src") !== broadwayUrl) {
        // Compare the literal attribute, not the `.src` IDL property: an
        // empty src="" attribute resolves through the property getter to
        // the *parent page's own URL* (per the URL spec, empty-string
        // resolution against the document base yields the base itself) —
        // never empty and never "about:blank" — so that check silently
        // never fires on a page load where a flowgraph is already active
        // server-side (e.g. refreshing the dashboard, or opening it in a
        // second tab), leaving the canvas permanently blank despite a
        // valid loaded graph.
        canvasIframe.src = broadwayUrl;
      }
      // Force a size sync (bypassing the "did it change" dedup below) —
      // a freshly spawned canvas_app.py process needs to be told the
      // pane's real size at least once, and its own resize listener
      // takes a moment to come up, so retry a couple of times to survive
      // that startup race rather than leaving the window at its default
      // guessed size.
      syncCanvasSize(true);
      setTimeout(() => syncCanvasSize(true), 800);
      setTimeout(() => syncCanvasSize(true), 2000);
    }
  } catch (e) {
    // A transient fetch failure (network blip, /grc/inspect mid-swap) must
    // NOT set isGrcLoaded = false — that would disable the chat input until
    // the user manually clicks Validate (updateChatInputState gates on
    // isGrcLoaded). Leave the prior value as the best guess; the next
    // successful refresh() call will correct any stale state.
    setMsg(String(e), "error");
  } finally {
    updateChatInputState();
  }
}

// Keep the GTK canvas window matched to the actual pane size — a mismatch
// clips the flowgraph AND pushes GRC's own scrollbars outside the visible
// iframe viewport, making it both cropped and unpannable (see
// canvas_app.py's start_resize_server for the receiving end).
let resizeTimeout = null;
function debouncedSyncCanvasSize() {
  if (resizeTimeout) clearTimeout(resizeTimeout);
  resizeTimeout = setTimeout(() => {
    syncCanvasSize(false);
  }, 150);
}

function syncCanvasSize(force) {
  const container = document.getElementById("canvas-container");
  if (!container) return;
  const rect = container.getBoundingClientRect();
  const width = Math.round(rect.width);
  const height = Math.round(rect.height);
  if (width <= 0 || height <= 0) return;
  const key = `${width}x${height}`;
  if (!force && key === state.lastSentCanvasSize) return;
  state.lastSentCanvasSize = key;
  fetch("/grc/canvas/resize", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({width, height})
  }).catch(() => {});
}

// The session-history panel needs a periodic refresh to reflect localStorage
// changes and hide itself once a conversation is active. (The old
// integrateSettings 300ms poll drove this; that iframe-sidebar-sniffing
// heuristic is gone with the iframe.)
function startLayoutDependentWork() {
  new ResizeObserver(() => debouncedSyncCanvasSize()).observe(document.getElementById("canvas-container"));
  setInterval(renderSessionHistory, 300);
}
if (document.readyState === "complete") startLayoutDependentWork();
else window.addEventListener("load", startLayoutDependentWork, { once: true });

refresh();

// ---- Model/provider preference (see grc_agent.settings), picked from a
// dropdown pinned directly over the chat widget's own (inert) model
// selector — that one just reflects the static `models=[...]` list to_web()
// was constructed with, it isn't wired to actually switch models, hence
// replacing it rather than leaving both visible side by side.
//
// The model NAME is click-to-edit (see enterModelNameEditMode/
// confirmModelNameEdit/cancelModelNameEdit below): a plain, non-editable
// label by default so an accidental keystroke or stray blur can't silently
// change something this consequential — it only becomes an editable field
// (with explicit confirm/cancel) once the user deliberately clicks it. The
// provider dropdown stays auto-save-on-change: picking an option is already
// one deliberate action, unlike typing free text.
function renderModelSuggestions() {
  const provider = document.getElementById("model-provider-select").value;
  const list = document.getElementById("model-name-suggestions");
  const suggestion = provider === "ollama" ? state.ollamaModel
    : provider === "ollama_cloud" ? state.ollamaCloudModel
    : state.openrouterModel;
  list.innerHTML = `<option value="${suggestion}"></option>`;
  document.getElementById("model-name-label").textContent = suggestion;
  const overlay = document.getElementById("chat-toolbar");
  overlay.title = provider === "ollama"
    ? "Pull a model first: ollama pull <name>."
    : provider === "ollama_cloud"
      ? "Enter any model name available on Ollama Cloud."
      : "Browse models at openrouter.ai/models." +
        (state.openrouterKeySet ? "" : " OPENROUTER_API_KEY is not set in .env.");
  updateRestartBadge();
}

// Derived fresh from server state every time it's checked (not a one-shot
// flag set right after a save), so it's correct even after a page reload or
// in a second tab — a saved change never takes effect without an app
// restart (AGENTS.md), so "saved" and "actually running" can diverge for an
// arbitrarily long time and the badge needs to reflect that the whole time,
// not just in the few seconds right after clicking confirm.
function updateRestartBadge() {
  const badge = document.getElementById("model-restart-badge");
  const provider = document.getElementById("model-provider-select").value;
  const model = document.getElementById("model-name-label").textContent;
  // When the saved config failed to build at startup (e.g. OpenRouter with
  // no API key), the restart badge would mislead: it would say "restart to
  // apply" for a config that can never succeed on restart. Suppress the
  // badge and show the error in the overlay title instead.
  if (state.activeProviderError) {
    badge.classList.remove("visible");
    document.getElementById("chat-toolbar").title =
      `Saved config failed to build: ${state.activeProviderError}`;
    return;
  }
  const stale = state.activeProvider !== null &&
    (provider !== state.activeProvider || model !== state.activeModel);
  badge.classList.toggle("visible", stale);
  if (stale) {
    badge.title = `Currently running ${state.activeProvider}/${state.activeModel} — ` +
      `restart the app to switch to ${provider}/${model}.`;
  }
}

function onModelProviderChange() {
  cancelModelNameEdit();
  const provider = document.getElementById("model-provider-select").value;
  document.getElementById("model-name-input").value =
    provider === "ollama" ? state.ollamaModel
    : provider === "ollama_cloud" ? state.ollamaCloudModel
    : state.openrouterModel;
  renderModelSuggestions();
  // Cloud providers need an API key — if none is saved, show the key dialog
  // instead of saving a config that will fail on restart (the app would fall
  // back to Ollama silently, and the restart badge would mislead the user
  // into a restart loop). The user can set the key and the provider will be
  // saved together.
  if ((provider === "ollama_cloud" && !state.ollamaCloudKeySet) ||
      (provider === "openrouter" && !state.openrouterKeySet)) {
    openApiKeyDialog();
    return;
  }
  saveModelSettings();
  checkProviderHealth();
}

async function loadModelSettings() {
  try {
    const res = await fetch("/grc/settings").then(r => r.json());
    if (!res.ok) return;
    state.openrouterKeySet = !!res.openrouter_api_key_set;
    state.ollamaCloudKeySet = !!res.ollama_cloud_api_key_set;
    if (res.ollama_model) state.ollamaModel = res.ollama_model;
    if (res.openrouter_model) state.openrouterModel = res.openrouter_model;
    if (res.ollama_cloud_model) state.ollamaCloudModel = res.ollama_cloud_model;
    state.activeProvider = res.active_provider ?? null;
    state.activeModel = res.active_model ?? null;
    state.activeProviderError = res.active_provider_error ?? null;
    document.getElementById("model-provider-select").value = res.provider;
    document.getElementById("model-name-input").value = res.model;
    renderModelSuggestions();
    // Trigger health check after loading settings
    checkProviderHealth();
  } catch (e) { /* leave defaults */ }
}

async function saveModelSettings() {
  const provider = document.getElementById("model-provider-select").value;
  const model = document.getElementById("model-name-input").value;
  const overlay = document.getElementById("chat-toolbar");
  overlay.classList.remove("saved", "error");
  try {
    const res = await fetch("/grc/settings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({provider, model})
    });
    const data = await res.json();
    overlay.classList.add(data.ok ? "saved" : "error");
    if (data.ok) {
      if (provider === "ollama") state.ollamaModel = model;
      else if (provider === "ollama_cloud") state.ollamaCloudModel = model;
      else state.openrouterModel = model;
      state.activeProvider = provider;
      state.activeModel = model;
      renderModelSuggestions();
      updateRestartBadge();
    }
  } catch (e) {
    overlay.classList.add("error");
  }
  setTimeout(() => overlay.classList.remove("saved", "error"), 2500);
}

// ---- Click-to-edit model name — see #model-name-label's own styling
// comment in panel.html for why this isn't just an always-editable input.
function enterModelNameEditMode() {
  const label = document.getElementById("model-name-label");
  const input = document.getElementById("model-name-input");
  input.value = label.textContent;
  label.style.display = "none";
  input.style.display = "";
  document.getElementById("model-name-confirm-btn").style.display = "";
  document.getElementById("model-name-cancel-btn").style.display = "";
  input.focus();
  input.select();
}

function cancelModelNameEdit() {
  const input = document.getElementById("model-name-input");
  if (input.style.display === "none") return; // not editing — no-op
  input.style.display = "none";
  document.getElementById("model-name-confirm-btn").style.display = "none";
  document.getElementById("model-name-cancel-btn").style.display = "none";
  document.getElementById("model-name-label").style.display = "";
}

async function confirmModelNameEdit() {
  const model = document.getElementById("model-name-input").value.trim();
  if (!model) { cancelModelNameEdit(); return; } // empty — treat as cancel, not a save of ""
  cancelModelNameEdit();
  await saveModelSettings();
}

// ---- Session history: previous conversations, each locked to the .grc
// file it was opened with (see saveSessionGrcPath). Shown above the chat
// input only while no conversation is active, so picking one — or simply
// starting a new one by sending a message — hides it.
function loadSessionHistory() {
  try {
    return JSON.parse(localStorage.getItem("grc_session_history") || "[]");
  } catch (e) {
    return [];
  }
}

function addSessionHistory(convId, path) {
  try {
    // Drop any exact duplicate of this conversation, and — when upgrading
    // a pending entry (convId === null) to a real one now that the user
    // has chatted — drop the stale pending copy for the same file so it
    // doesn't show up twice.
    const list = loadSessionHistory().filter(e =>
      e.convId !== convId && !(e.path === path && !e.convId)
    );
    list.unshift({convId: convId || null, path, ts: Date.now()});
    const kept = list.slice(0, 20);
    // Entries pushed past the cap drop out of the index silently — without
    // this, their grc_messages_<convId> payloads (which can hold full
    // tool-call args/results) never get cleaned up and accumulate in
    // localStorage without bound until a quota error silently breaks saving.
    for (const dropped of list.slice(20)) {
      if (dropped.convId) localStorage.removeItem("grc_messages_" + dropped.convId);
    }
    localStorage.setItem("grc_session_history", JSON.stringify(kept));
  } catch (e) { /* ignore */ }
}

function timeAgo(ts) {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function saveConversationMessages(convId, messages) {
  if (!convId) return;
  try {
    localStorage.setItem("grc_messages_" + convId, JSON.stringify(messages));
  } catch (e) {}
}

function loadConversationMessages(convId) {
  if (!convId) return [];
  try {
    return JSON.parse(localStorage.getItem("grc_messages_" + convId) || "[]");
  } catch (e) {
    return [];
  }
}

function renderChatMessagesFromHistory() {
  const box = document.getElementById("chat-messages");
  if (!box) return;
  box.innerHTML = "";
  if (!chatMessages.length) {
    const empty = document.createElement("div");
    empty.id = "chat-empty";
    empty.textContent = state.isGrcLoaded
      ? "Start a conversation about your flowgraph."
      : "Load a flowgraph (Browse) to start chatting.";
    box.appendChild(empty);
    return;
  }

  for (const msg of chatMessages) {
    const msgEl = document.createElement("div");
    msgEl.className = `chat-msg ${msg.role}`;
    const roleEl = document.createElement("div");
    roleEl.className = "chat-msg-role";
    roleEl.textContent = msg.role;
    msgEl.appendChild(roleEl);

    if (msg.role === "user") {
      const bodyEl = document.createElement("div");
      bodyEl.className = "chat-msg-body";
      const textPart = msg.parts.find(p => p.type === "text");
      bodyEl.textContent = textPart ? textPart.text : "";
      msgEl.appendChild(bodyEl);
    } else {
      let currentToolGroup = null;
      for (const part of msg.parts) {
        if (part.type === "text") {
          currentToolGroup = null;
          const bodyEl = document.createElement("div");
          bodyEl.className = "chat-msg-body";
          bodyEl.innerHTML = renderMarkdown(part.text);
          msgEl.appendChild(bodyEl);
        } else if (part.type === "reasoning") {
          currentToolGroup = null;
          const el = document.createElement("details");
          el.className = "chat-msg-reasoning";
          el.innerHTML = `<summary>Thinking</summary><div class='reasoning-body'></div>`;
          el.querySelector(".reasoning-body").textContent = part.text || part.reasoning || "";
          msgEl.appendChild(el);
        } else if (part.type === "tool-call") {
          // Legacy shape: saved by an older version of this file as two
          // separate tool-call/tool-result parts. No new conversation is
          // ever stored this way anymore (see sendChatMessage) — kept only
          // so a conversation saved before that fix still renders.
          if (part.toolName === FINAL_RESULT_TOOL_NAME) {
            currentToolGroup = null;
            let input = part.args;
            if (typeof input === "string") {
              try { input = JSON.parse(input); } catch (e) {}
            }
            if (input && typeof input === "object") {
              if (typeof input.explanation === "string" && input.explanation) {
                const bodyEl = document.createElement("div");
                bodyEl.className = "chat-msg-body";
                bodyEl.innerHTML = renderMarkdown(input.explanation);
                msgEl.appendChild(bodyEl);
              }
              if (Array.isArray(input.actions_taken) && input.actions_taken.length) {
                const ul = document.createElement("ul");
                ul.className = "chat-msg-actions";
                for (const action of input.actions_taken) {
                  const li = document.createElement("li");
                  li.textContent = String(action);
                  ul.appendChild(li);
                }
                msgEl.appendChild(ul);
              }
            }
            continue;
          }
          if (!currentToolGroup) {
            currentToolGroup = document.createElement("div");
            currentToolGroup.className = "chat-msg-tools";
            msgEl.appendChild(currentToolGroup);
          }
          const el = document.createElement("div");
          el.className = "chat-tool";
          el.dataset.name = part.toolName;

          const outEl = document.createElement("div");
          outEl.className = "chat-tool-output";

          const tc = {
            el,
            outEl,
            argsAcc: typeof part.args === "string" ? part.args : JSON.stringify(part.args, null, 2) || "",
            status: "pending"
          };

          const resultPart = msg.parts.find(p => p.type === "tool-result" && p.toolCallId === part.toolCallId);
          if (resultPart) {
            if (resultPart.error) {
              el.className = "chat-tool error";
              tc.status = "error";
              outEl.textContent = resultPart.error;
            } else {
              el.className = "chat-tool done";
              tc.status = "done";
              const out = resultPart.result;
              outEl.textContent = (tc.argsAcc ? "Args:\n" + tc.argsAcc + "\n\nResult:\n" : "Result:\n")
                + (typeof out === "string" ? out : JSON.stringify(out, null, 2) || "(empty)");
            }
          }

          updateToolElement(tc);
          el.addEventListener("click", () => {
            outEl.classList.toggle("open");
            if (outEl.classList.contains("open") && tc.status === "pending") {
              outEl.textContent = "Args:\n" + tc.argsAcc;
            }
            updateToolElement(tc);
          });
          currentToolGroup.appendChild(el);
          currentToolGroup.appendChild(outEl);
        } else if (typeof part.type === "string" && part.type.startsWith("tool-")) {
          // Current schema-correct shape: one merged part per toolCallId,
          // `type` = "tool-<toolName>" — recover the display name from it.
          const toolName = part.type.slice(5);
          if (toolName === FINAL_RESULT_TOOL_NAME) {
            currentToolGroup = null;
            let input = part.input;
            if (typeof input === "string") {
              try { input = JSON.parse(input); } catch (e) {}
            }
            if (input && typeof input === "object") {
              if (typeof input.explanation === "string" && input.explanation) {
                const bodyEl = document.createElement("div");
                bodyEl.className = "chat-msg-body";
                bodyEl.innerHTML = renderMarkdown(input.explanation);
                msgEl.appendChild(bodyEl);
              }
              if (Array.isArray(input.actions_taken) && input.actions_taken.length) {
                const ul = document.createElement("ul");
                ul.className = "chat-msg-actions";
                for (const action of input.actions_taken) {
                  const li = document.createElement("li");
                  li.textContent = String(action);
                  ul.appendChild(li);
                }
                msgEl.appendChild(ul);
              }
            }
            continue;
          }
          if (!currentToolGroup) {
            currentToolGroup = document.createElement("div");
            currentToolGroup.className = "chat-msg-tools";
            msgEl.appendChild(currentToolGroup);
          }
          const el = document.createElement("div");
          el.className = "chat-tool";
          el.dataset.name = toolName;

          const outEl = document.createElement("div");
          outEl.className = "chat-tool-output";

          const argsAcc = typeof part.input === "string" ? part.input : JSON.stringify(part.input, null, 2) || "";
          const tc = { el, outEl, argsAcc, status: "pending" };

          if (part.state === "output-available") {
            el.className = "chat-tool done";
            tc.status = "done";
            const out = part.output;
            outEl.textContent = (argsAcc ? "Args:\n" + argsAcc + "\n\nResult:\n" : "Result:\n")
              + (typeof out === "string" ? out : JSON.stringify(out, null, 2) || "(empty)");
          } else if (part.state === "output-error" || part.state === "output-denied") {
            el.className = "chat-tool error";
            tc.status = "error";
            outEl.textContent = part.errorText || (part.state === "output-denied" ? "denied" : "error");
          }

          updateToolElement(tc);
          el.addEventListener("click", () => {
            outEl.classList.toggle("open");
            if (outEl.classList.contains("open") && tc.status === "pending") {
              outEl.textContent = "Args:\n" + tc.argsAcc;
            }
            updateToolElement(tc);
          });
          currentToolGroup.appendChild(el);
          currentToolGroup.appendChild(outEl);
        }
      }
    }
    box.appendChild(msgEl);
  }
  _scrollChatToBottom();
}

function renderSessionHistory() {
  const panel = document.getElementById("session-history-panel");
  if (!panel) return;
  const entries = loadSessionHistory();
  // Hide panel if conversation has started, history is empty, or a GRC file is loaded
  if (!entries.length || state.isGrcLoaded || getChatFramePath() !== "/") {
    panel.style.display = "none";
    return;
  }

  const form = document.getElementById("chat-form");
  let bottomOffset = 96;
  if (form) {
    bottomOffset = form.getBoundingClientRect().height;
  }
  panel.style.bottom = `${bottomOffset}px`;
  panel.style.display = "block";

  const key = JSON.stringify(entries);
  if (panel.dataset.key === key) return;
  panel.dataset.key = key;

  const list = document.getElementById("session-history-list");
  list.innerHTML = "";
  for (const entry of entries) {
    const row = document.createElement("div");
    row.className = "session-history-entry";
    const name = (entry.path || "").split("/").pop() || entry.path || "untitled";
    row.innerHTML = `<span class="she-name">${name}</span><span class="she-time">${timeAgo(entry.ts)}</span>`;
    // Reopen both the file and the saved chat history
    makeRowFocusable(row, () => {
      openGraph(entry.path, entry.convId);
    });
    list.appendChild(row);
  }
}

// ---- Provider health check — async, never blocks the UI. Called on page
// load and whenever the provider dropdown changes. Updates a small badge
// next to the model selector with live connectivity status.
async function checkProviderHealth() {
  const badge = document.getElementById("provider-health");
  const apikeyBtn = document.getElementById("apikey-btn");
  if (!badge) return;
  badge.className = "checking";
  badge.textContent = "";
  try {
    const res = await fetch("/grc/health").then(r => r.json());
    if (res.ok) {
      badge.className = "healthy";
      badge.textContent = "";
      badge.title = res.message;
      apikeyBtn.style.display = "none";
    } else {
      badge.className = "unhealthy";
      badge.textContent = "";
      badge.title = res.message;
      // Show the API key button for cloud providers that need a key
      if (res.provider === "ollama_cloud" || res.provider === "openrouter") {
        apikeyBtn.style.display = "";
        apikeyBtn.title = res.message;
      } else {
        apikeyBtn.style.display = "none";
      }
    }
  } catch (e) {
    badge.className = "unhealthy";
    badge.textContent = "error";
    badge.title = String(e);
    apikeyBtn.style.display = "none";
  }
}

// ---- API key dialog — lets the user set their API key for Ollama Cloud or
// OpenRouter directly from the GUI. Writes to .env on the server.
function openApiKeyDialog() {
  const provider = document.getElementById("model-provider-select").value;
  if (provider !== "ollama_cloud" && provider !== "openrouter") return;
  const nameEl = document.getElementById("apikey-provider-name");
  nameEl.textContent = provider === "ollama_cloud" ? "Ollama Cloud" : "OpenRouter";
  document.getElementById("apikey-input").value = "";
  document.getElementById("apikey-dialog-overlay").classList.add("open");
  setTimeout(() => document.getElementById("apikey-input").focus(), 100);
}

function closeApiKeyDialog() {
  document.getElementById("apikey-dialog-overlay").classList.remove("open");
}

async function saveApiKey() {
  const provider = document.getElementById("model-provider-select").value;
  const apiKey = document.getElementById("apikey-input").value.trim();
  if (!apiKey) { setMsg("API key cannot be empty.", "error"); return; }
  try {
    const res = await fetch("/grc/apikey", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({provider, api_key: apiKey})
    });
    const data = await res.json();
    if (data.ok) {
      setMsg(data.message, "ok");
      closeApiKeyDialog();
      // onModelProviderChange() deliberately skipped saveModelSettings() when
      // this dialog opened (to avoid persisting an unconfigured provider) —
      // finish that provider switch now that the key exists, BEFORE checking
      // health: /grc/health reads the SAVED provider, not the dropdown's
      // current value, so checking health first would report the OLD
      // provider's connectivity instead of the one the user just configured.
      await saveModelSettings();
      checkProviderHealth();
    } else {
      setMsg(data.message || "Failed to save API key.", "error");
    }
  } catch (e) {
    setMsg(String(e), "error");
  }
}

function initResize() {
  const handle = document.getElementById("resize-handle");
  const chatPane = document.getElementById("chat-pane");
  if (!handle || !chatPane) return;

  let dragging = false;

  const applyWidth = (clientX) => {
    const containerWidth = window.innerWidth;
    const newWidth = containerWidth - clientX;
    // Floor 360px for the chat pane, leave >=300px for the GRC pane.
    const w = Math.max(360, Math.min(newWidth, containerWidth - 300));
    // Inline min-width:0 lets the drag go below the CSS 580px desktop floor.
    // These inline values are cleared on pointerup / window-resize below
    // 900px (see respectResponsive) so the @media stacking rule stays in
    // charge on narrow viewports — inline beats stylesheet, so without that
    // clear the responsive fallback would silently die after the first drag.
    chatPane.style.minWidth = "0px";
    chatPane.style.flex = `0 0 ${w}px`;
    chatPane.style.width = `${w}px`;
  };

  const respectResponsive = () => {
    if (window.innerWidth <= 900) {
      chatPane.style.flex = "";
      chatPane.style.width = "";
      chatPane.style.minWidth = "";
    }
  };
  window.addEventListener("resize", respectResponsive);

  handle.addEventListener("pointerdown", (e) => {
    dragging = true;
    // setPointerCapture routes all subsequent pointer events to the handle
    // regardless of where the pointer is — including a pointerup outside the
    // window — so the drag always ends cleanly. No "stuck resizing" state, and
    // no need for the old body.resizing iframe pointer-events hack.
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("active");
    e.preventDefault();
  });
  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    // The viewport can cross the 900px stacking breakpoint mid-drag (a window
    // resize/snap, or an orientation change, without the pointer ever being
    // released) — applyWidth() would otherwise keep re-applying inline
    // flex/width/minWidth, which beats the @media stacking rule and leaves the
    // chat pane at a wrong, non-full-width size once stacked.
    if (window.innerWidth <= 900) { endDrag(e); return; }
    applyWidth(e.clientX);
  });
  const endDrag = (e) => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove("active");
    try { handle.releasePointerCapture(e.pointerId); } catch (err) {}
    respectResponsive();
  };
  handle.addEventListener("pointerup", endDrag);
  handle.addEventListener("pointercancel", endDrag);
}
// All handlers are bound HERE (after every function above is defined) rather
// than via inline onclick=/onload= attributes in the HTML. An inline
// onload="integrateSettings()" on the chat iframe used to fire BEFORE this
// script finished loading -> "integrateSettings is not defined", and inline
// onclick handlers can similarly throw "not defined" if clicked during a
// cold first load. Binding here guarantees the function exists when the
// handler fires.
document.getElementById("clear-history-btn").addEventListener("click", clearHistory);
document.getElementById("browse-btn").addEventListener("click", openBrowse);
document.getElementById("undo-btn").addEventListener("click", doUndo);
document.getElementById("redo-btn").addEventListener("click", doRedo);
document.getElementById("validate-btn").addEventListener("click", doValidate);
document.getElementById("blocks-panel-toggle-btn").addEventListener("click", toggleBlocksPanel);
document.getElementById("reset-conversation-btn").addEventListener("click", resetConversation);
document.getElementById("new-conversation-btn").addEventListener("click", startNewConversation);
document.getElementById("browse-up-btn").addEventListener("click", browseUp);
document.getElementById("browse-cancel-btn").addEventListener("click", closeBrowse);
initChatWidget();
document.getElementById("model-provider-select").addEventListener("change", onModelProviderChange);
document.getElementById("model-name-label").addEventListener("click", enterModelNameEditMode);
document.getElementById("model-name-label").addEventListener("keydown", function(e) {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); enterModelNameEditMode(); }
});
document.getElementById("model-name-confirm-btn").addEventListener("click", confirmModelNameEdit);
document.getElementById("model-name-cancel-btn").addEventListener("click", cancelModelNameEdit);
document.getElementById("model-name-input").addEventListener("keydown", function(e) {
  if (e.key === "Enter") { e.preventDefault(); confirmModelNameEdit(); }
  else if (e.key === "Escape") { e.preventDefault(); cancelModelNameEdit(); }
});
loadModelSettings();
initResize();

// ---- API key dialog wiring ----
document.getElementById("apikey-btn").addEventListener("click", openApiKeyDialog);
document.getElementById("apikey-cancel-btn").addEventListener("click", closeApiKeyDialog);
document.getElementById("apikey-save-btn").addEventListener("click", saveApiKey);
document.getElementById("apikey-input").addEventListener("keydown", function(e) {
  if (e.key === "Enter") { e.preventDefault(); saveApiKey(); }
  else if (e.key === "Escape") { e.preventDefault(); closeApiKeyDialog(); }
});
document.getElementById("apikey-dialog-overlay").addEventListener("click", function(e) {
  if (e.target === this) closeApiKeyDialog();
});

// Close browse overlay when clicking outside the dialog content
document.getElementById("browse-overlay").addEventListener("click", function(e) {
  if (e.target === this) {
    closeBrowse();
  }
});

// Close browse overlay or API key dialog when pressing the Escape key
window.addEventListener("keydown", function(e) {
  if (e.key === "Escape") {
    if (document.getElementById("apikey-dialog-overlay").classList.contains("open")) {
      closeApiKeyDialog();
    } else {
      closeBrowse();
    }
  }
});
