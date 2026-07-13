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
  // Last-known canvas outcome, mirrored from whichever of /grc/open's or
  // /grc/status's canvas_ready field was freshest at the time — see
  // refresh()'s own effectiveCanvasReady note. Only read/written by
  // refresh() itself today; kept on state (rather than a local variable)
  // so it survives across calls the same way isGrcLoaded does.
  canvasReady: true,
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
};

const MAX_SESSION_MAPPINGS = 50;

// Each conversation is locked to exactly one flowgraph (mirrors the old
// desktop GUI's open_file(), which cleared the chat and reset the agent
// session on every load) — a chat history should never span two different
// underlying .grc files. The vendor widget hardcodes pathname === "/" as
// its own "fresh conversation" sentinel (confirmed via direct inspection —
// any other path silently renders nothing), so the iframe must point at
// the literal root, not a subpath; the query param just forces a reload
// instead of a no-op same-src assignment.
function resetChatFrame(preserveGraph = false) {
  localStorage.removeItem("grc_active_conv_id");
  setUrlConvId(null);
  if (preserveGraph) state.preserveGrcOnReset = true;
  document.getElementById("chat-frame").src = `/?r=${crypto.randomUUID()}`;
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

// Reads the chat iframe's OWN current pathname directly and fresh — never
// through state.lastPath, which is only written by pollConversationState's
// 750ms tick and can lag the iframe's real location by up to that long (plus
// whatever async work runs before it writes). Used by any decision that must
// be correct within a tick or two of a real navigation (e.g. hiding Clear
// History the instant a message is sent), not just eventually-consistent.
function getChatFramePath() {
  try {
    const win = document.getElementById("chat-frame")?.contentWindow;
    const loc = win?.location;
    if (!loc || loc.href === "about:blank") return undefined;
    return loc.pathname;
  } catch (e) {
    return undefined; // cross-origin/transitional — same as pollConversationState's own guard
  }
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
  const url = new URL(window.location);
  const urlConv = url.searchParams.get("conv");
  const activeConvId = urlConv ? "/" + urlConv : localStorage.getItem("grc_active_conv_id");
  if (activeConvId && activeConvId !== "/") {
    document.getElementById("chat-frame").src = activeConvId;
  } else {
    startNewConversation();
  }
}
restoreSession();

// The chat widget has no server-side conversation store (confirmed by
// reading pydantic_ai's own to_web() source — its /api/chat route is
// stateless); it persists every conversation client-side in a same-origin
// IndexedDB database named "chat-storage" (confirmed by inspecting the
// vendor bundle directly). Since this page and the iframe share that origin,
// deleting it here clears history for good — then start a fresh conversation
// so the UI doesn't keep pointing at now-gone data.
function clearHistory() {
  if (!confirm("Delete all conversations? This cannot be undone.")) return;

  // 1. Clear parent storage
  try {
    localStorage.clear();
    sessionStorage.clear();
  } catch (e) {}

  // 2. Clear iframe storage directly to bypass iframe session memory
  const iframe = document.getElementById("chat-frame");
  if (iframe) {
    try {
      const iframeWin = iframe.contentWindow;
      if (iframeWin) {
        iframeWin.localStorage.clear();
        iframeWin.sessionStorage.clear();
      }
    } catch (e) {}
  }

  // 3. Delete parent IndexedDB chat database only — clearing every DB on
  // the origin is overly destructive and could wipe unrelated same-origin
  // application data. The targeted delete is enough to reset the vendor widget.
  try {
    indexedDB.deleteDatabase("chat-storage");
  } catch (e) {}

  // 4. Force reload/navigate the iframe
  startNewConversation();
  unloadGrc();

  // 5. Clear again after 150ms to wipe out any state written by React during the unload event
  setTimeout(() => {
    try {
      localStorage.clear();
      sessionStorage.clear();
      if (iframe && iframe.contentWindow) {
        iframe.contentWindow.localStorage.clear();
        iframe.contentWindow.sessionStorage.clear();
      }
    } catch (e) {}
  }, 150);
}

// The vendor widget uses pathname "/" as its own "fresh conversation"
// sentinel (confirmed via direct inspection) — it flips away from "/" as
// soon as a real conversation exists. Same-origin iframe, so the parent
// can read it directly: poll it to gate Browse, since a conversation in
// progress must not have its flowgraph swapped out from under it — only
// starting a new conversation (this page's own load, or the widget's own
// "New conversation" button) should re-enable loading a different file.

function getSessionGrcPath(convId) {
  try {
    const mapping = JSON.parse(localStorage.getItem("grc_session_mapping") || "{}");
    return mapping[convId] || null;
  } catch (e) {
    return null;
  }
}

// Unlike grc_session_history (capped at 20, see addSessionHistory), this
// map grew forever — every conversation ever created left a permanent
// entry with no pruning at all.
function saveSessionGrcPath(convId, grcPath) {
  try {
    const mapping = JSON.parse(localStorage.getItem("grc_session_mapping") || "{}");
    mapping[convId] = grcPath;
    const keys = Object.keys(mapping);
    if (keys.length > MAX_SESSION_MAPPINGS) {
      for (const staleKey of keys.slice(0, keys.length - MAX_SESSION_MAPPINGS)) {
        delete mapping[staleKey];
      }
    }
    localStorage.setItem("grc_session_mapping", JSON.stringify(mapping));
  } catch (e) {}
}

async function pollConversationState() {
  // A previous tick's auto-reopen /grc/open is still awaiting its (up to
  // ~20s) canvas-ready outcome — see autoOpenInFlight's own comment. Skip
  // this whole tick rather than re-detecting the same still-pending
  // transition and firing a duplicate open.
  if (state.autoOpenInFlight) return;
  const btn = document.getElementById("browse-btn");
  let fresh = true;
  let currentPath;
  try {
    const frame = document.getElementById("chat-frame");
    if (!frame) return;
    const win = frame.contentWindow;
    if (!win) return;
    const loc = win.location;
    if (!loc || loc.href === "about:blank") return;
    currentPath = loc.pathname;
    fresh = currentPath === "/";
  } catch (e) {
    // Transitional or loading states must exit early to prevent fake "/" transitions and premature unloads
    return;
  }

  btn.disabled = !fresh;
  btn.title = fresh ? "" : "Start a new conversation to load a different file";

  // Self-heal from a conversation id that no longer resolves to anything
  // (its IndexedDB entry got cleared some other way, or the URL was
  // hand-edited): the vendor widget renders nothing for it — no textarea,
  // no sidebar, no send button — which then persists across a page reload
  // via grc_active_conv_id, and every OTHER recovery control (the "+"
  // new-conversation button, the model selector) hides itself too, since
  // they all depend on finding something inside that now-empty iframe. A
  // few consecutive empty polls during ordinary page-transition/mount
  // timing is normal, so only treat it as truly stuck after enough of
  // them in a row (~4.5s at this 750ms interval).
  if (!fresh) {
    let hasContent = false;
    try {
      hasContent = !!document.getElementById("chat-frame")
        .contentWindow.document.querySelector("textarea");
    } catch (e) {}
    if (hasContent) {
      state.brokenIframePolls = 0;
    } else if (++state.brokenIframePolls >= 6) {
      console.warn("Conversation appears unrecoverable — resetting.");
      state.brokenIframePolls = 0;
      resetConversation();
      return;
    }
  } else {
    state.brokenIframePolls = 0;
  }

  // Check version changes on backend to auto-refresh if agent changed params!
  try {
    const statusRes = await fetch("/grc/status").then(r => r.json());
    if (statusRes.version !== undefined && statusRes.version !== state.lastGrcVersion) {
      state.lastGrcVersion = statusRes.version;
      await refresh();
    }
  } catch (e) {}

  if (currentPath !== state.lastPath) {
    setUrlConvId(currentPath);
    if (currentPath === "/") {
      localStorage.removeItem("grc_active_conv_id");
      // Suppressed for a resetChatFrame(true) repair (chat-widget-only —
      // see resetConversation) — consumed once so it can't swallow a later,
      // genuine abandon (e.g. the vendor widget's own internal "New
      // conversation" click, which this transition check is the only way
      // this page ever learns about).
      if (state.preserveGrcOnReset) {
        state.preserveGrcOnReset = false;
      } else {
        unloadGrc();
      }
    } else {
      localStorage.setItem('grc_active_conv_id', currentPath);

      const mappedGrc = getSessionGrcPath(currentPath);
      if (mappedGrc) {
        state.autoOpenInFlight = true;
        try {
          const res = await fetch("/grc/open", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({path: mappedGrc})
          });
          const data = await res.json();
          if (data.ok) {
            addSessionHistory(currentPath, mappedGrc);
            if (data.canvas_ready === false) {
              // refresh() itself now sets the error message — see its own
              // ordering note (openGraph's identical case).
              await refresh(false, false, data.canvas_error);
            } else {
              await refresh();
            }
          }
        } catch (e) {
          console.error("Auto-open GRC failed:", e);
        } finally {
          state.autoOpenInFlight = false;
        }
      } else {
        try {
          const statusRes = await fetch("/grc/status").then(r => r.json());
          if (statusRes.path) {
            saveSessionGrcPath(currentPath, statusRes.path);
            addSessionHistory(currentPath, statusRes.path);
          } else {
            state.isGrcLoaded = false;
            await refresh();
          }
        } catch (e) {}
      }
    }
    state.lastPath = currentPath;
  }
  integrateSettings();
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

setInterval(pollConversationState, 750);

function integrateSettings() {
  try {
    const iframe = document.getElementById("chat-frame");
    if (!iframe) return;
    const doc = iframe.contentDocument || iframe.contentWindow?.document;
    const overlay = document.getElementById("left-sidebar-overlay");
    if (!doc || !overlay) return;

    // Inject spacing and display rules to force the sidebar to be visible inside the iframe
    if (!doc.getElementById("parent-injected-padding")) {
      const styleSheet = doc.createElement("style");
      styleSheet.id = "parent-injected-padding";
      styleSheet.textContent = `
        aside, .sidebar, [role="navigation"] {
          display: flex !important;
          visibility: visible !important;
          width: 240px !important;
          min-width: 240px !important;
          max-width: 240px !important;
          position: relative !important;
          padding-bottom: 90px !important;
        }
      `;
      doc.head.appendChild(styleSheet);
    }

    // Find the sidebar container.
    let sidebar = doc.querySelector("aside") || doc.querySelector(".sidebar") || doc.querySelector("[role='navigation']");

    if (!sidebar) {
      // Fallback 1: search for logo or text indicators
      const elements = doc.querySelectorAll("a, button, div, span");
      let indicator = null;
      for (const el of elements) {
        const txt = (el.textContent || "").trim().toLowerCase();
        if (txt === "qoherent grc agent" || txt === "pydantic ai" || txt === "new conversation" || txt === "+ new conversation") {
          indicator = el;
          break;
        }
      }
      if (indicator) {
        let parent = indicator.parentElement;
        while (parent && parent !== doc.body) {
          const className = parent.className || "";
          if (
            parent.tagName === "ASIDE" ||
            parent.classList.contains("sidebar") ||
            className.includes("sidebar") ||
            className.includes("border-r") ||
            className.includes("w-64") ||
            className.includes("w-60")
          ) {
            sidebar = parent;
            break;
          }
          parent = parent.parentElement;
        }
        if (!sidebar) {
          sidebar = indicator.parentElement?.parentElement;
        }
      }
    }

    if (!sidebar) {
      // Fallback 2: Look for the first child of the first flex/grid container under root
      const root = doc.getElementById("root") || doc.body;
      const flexContainer = Array.from(root.querySelectorAll("*")).find(el => {
        try {
          const display = iframe.contentWindow.getComputedStyle(el).display;
          return display === "flex" || display === "grid";
        } catch (e) {
          return false;
        }
      });
      if (flexContainer && flexContainer.firstElementChild) {
        sidebar = flexContainer.firstElementChild;
      }
    }

    // Clear History (this overlay) must NEVER appear during an active
    // conversation. Hide is the default, applied BEFORE the sidebar
    // detection below — so a transient throw mid-detection (e.g. the iframe
    // momentarily inaccessible during navigation) can't leave it stuck
    // visible mid-session. It's only (re)shown when on the fresh screen AND
    // a visible sidebar is actually found.
    //
    // Reads the iframe's live path directly (getChatFramePath()), NOT
    // state.lastPath: this poll runs every 300ms but state.lastPath is only
    // written by pollConversationState's separate 750ms tick, so trusting it
    // here left a live-measured ~734ms window, after every single message
    // sent, where this overlay showed on top of an already-active
    // conversation — a real, cited-elsewhere-as-"sometimes" bug, not
    // theoretical.
    overlay.style.display = "none";
    if (getChatFramePath() === "/" && sidebar) {
      const rect = sidebar.getBoundingClientRect();
      const style = iframe.contentWindow.getComputedStyle(sidebar);
      const isVisible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
      if (isVisible) {
        // Show overlay and match dimensions dynamically
        overlay.style.display = "flex";
        overlay.style.width = `${rect.width}px`;
      }
    }
  } catch (err) {
    console.error("integrateSettings failed:", err);
  }

  // Keep chat input disabled if no GRC file is loaded
  try {
    updateChatInputState();
  } catch (e) {}

  integrateModelSelector();
  integrateNewConversationButton();
  renderSessionHistory();
}

function updateChatInputState() {
  const iframe = document.getElementById("chat-frame");
  if (!iframe) return;
  const doc = iframe.contentDocument || iframe.contentWindow?.document;
  if (!doc) return;

  const textarea = doc.querySelector("textarea");
  const sendBtn = doc.querySelector("form button") || doc.querySelector("textarea ~ button") || doc.querySelector("button[type='submit']");

  if (!textarea) return;

  if (!state.isGrcLoaded) {
    textarea.disabled = true;
    textarea.placeholder = "Please load a GRC file using the Browse button to start chatting.";
    textarea.style.opacity = "0.5";
    textarea.style.cursor = "not-allowed";
    if (sendBtn) {
      sendBtn.disabled = true;
      sendBtn.style.opacity = "0.3";
      sendBtn.style.cursor = "not-allowed";
    }
  } else {
    if (textarea.disabled) {
      textarea.disabled = false;
      textarea.placeholder = "What would you like to know?";
      textarea.style.opacity = "1";
      textarea.style.cursor = "text";
      if (sendBtn) {
        sendBtn.disabled = false;
        sendBtn.style.opacity = "1";
        sendBtn.style.cursor = "pointer";
      }
    }
  }
}

function setMsg(text, cls) {
  const el = document.getElementById("msg");
  el.textContent = text || "";
  el.className = text ? `visible ${cls || ""}` : "";
}

async function openGraph(path) {
  if (!path) return;
  setMsg("Loading...");
  try {
    const res = await fetch("/grc/open", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({path})
    });
    const data = await res.json();
    if (!data.ok) { setMsg(data.message || "Failed to load file.", "error"); return; }
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
      renderEmptyState();
      return;
    }
    if (!inspectRes.ok) {
      state.isGrcLoaded = false;
      setMsg("Inspect failed.", "error");
      return;
    }
    state.isGrcLoaded = true;
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
    state.canvasReady = effectiveCanvasReady;
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
  }
}

// Keep the GTK canvas window matched to the actual pane size — a mismatch
// clips the flowgraph AND pushes GRC's own scrollbars outside the visible
// iframe viewport, making it both cropped and unpannable (see
// canvas_app.py's start_resize_server for the receiving end).
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

// Layout-forcing work (ResizeObserver's initial callback + integrateSettings
// reading getBoundingClientRect) used to run during parse, before all
// subresources (chat iframe) had loaded -> "Layout was forced before the page
// was fully loaded" / flash of unstyled content. Defer both behind `load`.
function startLayoutDependentWork() {
  new ResizeObserver(() => syncCanvasSize(false)).observe(document.getElementById("canvas-container"));
  setInterval(integrateSettings, 300);
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
  const overlay = document.getElementById("model-selector-overlay");
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
    document.getElementById("model-selector-overlay").title =
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
  const overlay = document.getElementById("model-selector-overlay");
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
      renderModelSuggestions();
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

// Position our own provider/model controls directly over the chat
// widget's model combobox button so they read as one native toolbar
// control instead of a bolted-on side panel.
function integrateModelSelector() {
  try {
    const iframe = document.getElementById("chat-frame");
    const doc = iframe.contentDocument || iframe.contentWindow?.document;
    const overlay = document.getElementById("model-selector-overlay");
    if (!doc || !overlay) return;

    const combobox = doc.querySelector('[role="combobox"]');
    if (!combobox) { overlay.style.display = "none"; return; }

    if (!doc.getElementById("parent-injected-combobox-hide")) {
      const style = doc.createElement("style");
      style.id = "parent-injected-combobox-hide";
      // visibility (not display) so the toolbar's layout doesn't reflow
      style.textContent = `[role="combobox"] { visibility: hidden !important; }`;
      doc.head.appendChild(style);
    }

    const boxRect = combobox.getBoundingClientRect();
    const iframeRect = iframe.getBoundingClientRect();
    overlay.style.display = "flex";
    overlay.style.left = `${iframeRect.left + boxRect.left}px`;
    overlay.style.top = `${iframeRect.top + boxRect.top}px`;
    overlay.style.height = `${boxRect.height}px`;
    overlay.style.width = `${Math.max(boxRect.width, 340)}px`;
  } catch (err) {
    console.error("integrateModelSelector failed:", err);
  }
}

// Position a "+" new-conversation button just left of the chat's own send
// button, so it reads as part of the same toolbar.
function integrateNewConversationButton() {
  try {
    const iframe = document.getElementById("chat-frame");
    const doc = iframe.contentDocument || iframe.contentWindow?.document;
    const btn = document.getElementById("new-conversation-btn");
    if (!doc || !btn) return;

    const sendBtn = doc.querySelector('form button[type="submit"]') || doc.querySelector("form button");
    if (!sendBtn) { btn.style.display = "none"; return; }

    const sendRect = sendBtn.getBoundingClientRect();
    const iframeRect = iframe.getBoundingClientRect();
    const size = sendRect.height || 36;
    btn.style.display = "flex";
    btn.style.width = `${size}px`;
    btn.style.height = `${size}px`;
    btn.style.left = `${iframeRect.left + sendRect.left - size - 8}px`;
    btn.style.top = `${iframeRect.top + sendRect.top}px`;
    // Always enabled — even while already on a fresh conversation, a file
    // can be loaded with no message sent yet, and this is the only way
    // back to Browse in that state (see startNewConversation).
    btn.disabled = false;
  } catch (err) {
    console.error("integrateNewConversationButton failed:", err);
  }
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
    localStorage.setItem("grc_session_history", JSON.stringify(list.slice(0, 20)));
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

function renderSessionHistory() {
  const panel = document.getElementById("session-history-panel");
  if (!panel) return;
  const entries = loadSessionHistory();
  // Same staleness bug as the Clear History overlay (see integrateSettings)
  // — read the iframe's live path directly rather than the slower-cadence
  // state.lastPath, so this panel can't linger visible after a message has
  // already started a real conversation.
  if (!entries.length || getChatFramePath() !== "/") {
    panel.style.display = "none";
    return;
  }

  const iframe = document.getElementById("chat-frame");
  let bottomOffset = 96;
  try {
    const doc = iframe.contentDocument || iframe.contentWindow?.document;
    const toolbar = doc && doc.querySelector(".sticky.bottom-0");
    if (toolbar) bottomOffset = toolbar.getBoundingClientRect().height;
  } catch (e) { /* keep fallback */ }
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
    // A pending entry (file loaded but never chatted in) has no real
    // conversation id to resume — reopen the file fresh instead.
    makeRowFocusable(row, () => {
      if (entry.convId) document.getElementById("chat-frame").src = entry.convId;
      else openGraph(entry.path);
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
  badge.textContent = "checking...";
  try {
    const res = await fetch("/grc/health").then(r => r.json());
    if (res.ok) {
      badge.className = "healthy";
      badge.textContent = "connected";
      badge.title = res.message;
      apikeyBtn.style.display = "none";
    } else {
      badge.className = "unhealthy";
      badge.textContent = "disconnected";
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
      // Re-check health now that the key is set in the current process
      checkProviderHealth();
    } else {
      setMsg(data.message || "Failed to save API key.", "error");
    }
  } catch (e) {
    setMsg(String(e), "error");
  }
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
document.getElementById("reset-conversation-btn").addEventListener("click", resetConversation);
document.getElementById("new-conversation-btn").addEventListener("click", startNewConversation);
document.getElementById("browse-up-btn").addEventListener("click", browseUp);
document.getElementById("browse-cancel-btn").addEventListener("click", closeBrowse);
document.getElementById("chat-frame").addEventListener("load", integrateSettings);
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
