// Qoherent GRC Agent — dashboard logic. Extracted from panel.html (still
// vanilla, no build step). All functions stay top-level/global because the
// HTML wires some via inline onclick="..." handlers. The previously
// scattered `let` globals are consolidated into one `state` object: declared
// once at the very top, so every read/write goes through it and there is no
// temporal-dead-zone ordering hazard (an earlier bare `let isGrcLoaded`
// declared too low once threw a ReferenceError that silently killed the whole
// inline script).

const ICON_DIR = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>';
const ICON_FILE = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>';

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
  ollamaModel: "qwen3.6:35b-a3b-q4_K_M",
  openrouterModel: "openai/gpt-4o-mini",
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
function startNewConversation() {
  localStorage.removeItem("grc_active_conv_id");
  document.getElementById("chat-frame").src = `/?r=${crypto.randomUUID()}`;
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
function resetConversation() {
  state.brokenIframePolls = 0;
  startNewConversation();
}

function restoreSession() {
  const activeConvId = localStorage.getItem("grc_active_conv_id");
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
        if (iframeWin.indexedDB) {
          iframeWin.indexedDB.deleteDatabase("chat-storage");
          if (iframeWin.indexedDB.databases) {
            iframeWin.indexedDB.databases().then(dbs => {
              for (const db of dbs) {
                if (db.name) {
                  iframeWin.indexedDB.deleteDatabase(db.name);
                }
              }
            });
          }
        }
      }
    } catch (e) {}
  }

  // 3. Delete parent IndexedDB databases
  try {
    indexedDB.deleteDatabase("chat-storage");
    if (indexedDB.databases) {
      indexedDB.databases().then(dbs => {
        for (const db of dbs) {
          if (db.name) {
            indexedDB.deleteDatabase(db.name);
          }
        }
      });
    }
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
    if (currentPath === "/") {
      localStorage.removeItem("grc_active_conv_id");
      unloadGrc();
    } else {
      localStorage.setItem('grc_active_conv_id', currentPath);

      const mappedGrc = getSessionGrcPath(currentPath);
      if (mappedGrc) {
        try {
          const res = await fetch("/grc/open", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({path: mappedGrc})
          });
          const data = await res.json();
          if (data.ok) {
            state.isGrcLoaded = true;
            addSessionHistory(currentPath, mappedGrc);
            await refresh();
          }
        } catch (e) {
          console.error("Auto-open GRC failed:", e);
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
    overlay.style.display = "none";
    if (state.lastPath === "/" && sidebar) {
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
    setMsg(`Loaded ${data.path}`, "ok");
    // Record a session-history entry right away — a real conversation id
    // doesn't exist yet (the vendor chat widget only mints one once the
    // first message is sent), so this is a "pending" entry (no convId).
    // It's upgraded to the real conversation once the user actually
    // chats (see pollConversationState) — without this, a file load that
    // never turns into a chat message would never show up anywhere.
    addSessionHistory(null, data.path);
    startNewConversation();
    // forceCanvasReload: a brand-new canvas_app.py just connected to Broadway;
    // force the iframe to re-init broadway.js so it doesn't sit on a stale /
    // disconnected WebSocket from the prior canvas.
    await refresh(true);
    setTimeout(() => setMsg(""), 2500);
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
  if (firstRow) firstRow.focus();
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
  const canvasIframe = document.getElementById("canvas-iframe");
  const canvasPlaceholder = document.getElementById("canvas-placeholder");
  if (canvasIframe && canvasPlaceholder) {
    canvasIframe.style.display = "none";
    canvasIframe.src = "about:blank";
    canvasPlaceholder.style.display = "flex";
  }
}

async function refresh(forceCanvasReload = false) {
  try {
    const [statusRes, inspectRes] = await Promise.all([
      fetch("/grc/status").then(r => r.json()),
      fetch("/grc/inspect").then(r => r.json())
    ]);
    document.querySelector("#current-path .value").textContent = statusRes.path || "-";
    if (statusRes.version !== undefined) {
      state.lastGrcVersion = statusRes.version;
    }
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
    // Broadway URL comes from the server (env-overridable port) so this
    // isn't hardcoded to 8085 — falls back only if the server didn't send it.
    const broadwayUrl = statusRes.broadway_url || "http://localhost:8085/";
    const canvasIframe = document.getElementById("canvas-iframe");
    const canvasPlaceholder = document.getElementById("canvas-placeholder");
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
    state.isGrcLoaded = false;
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
function renderModelSuggestions() {
  const provider = document.getElementById("model-provider-select").value;
  const list = document.getElementById("model-name-suggestions");
  const suggestion = provider === "ollama" ? state.ollamaModel : state.openrouterModel;
  list.innerHTML = `<option value="${suggestion}"></option>`;
  const overlay = document.getElementById("model-selector-overlay");
  overlay.title = provider === "ollama"
    ? "Pull a model first: ollama pull <name>. Restart the app after saving to use it."
    : "Browse models at openrouter.ai/models." +
      (state.openrouterKeySet ? "" : " OPENROUTER_API_KEY is not set in .env.") +
      " Restart the app after saving to use it.";
}

function onModelProviderChange() {
  const provider = document.getElementById("model-provider-select").value;
  document.getElementById("model-name-input").value =
    provider === "ollama" ? state.ollamaModel : state.openrouterModel;
  renderModelSuggestions();
  saveModelSettings();
}

async function loadModelSettings() {
  try {
    const res = await fetch("/grc/settings").then(r => r.json());
    if (!res.ok) return;
    state.openrouterKeySet = !!res.openrouter_api_key_set;
    if (res.ollama_model) state.ollamaModel = res.ollama_model;
    if (res.openrouter_model) state.openrouterModel = res.openrouter_model;
    document.getElementById("model-provider-select").value = res.provider;
    document.getElementById("model-name-input").value = res.model;
    renderModelSuggestions();
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
      else state.openrouterModel = model;
      renderModelSuggestions();
    }
  } catch (e) {
    overlay.classList.add("error");
  }
  setTimeout(() => overlay.classList.remove("saved", "error"), 2500);
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
  if (!entries.length || state.lastPath !== "/") {
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

// ---- Event wiring ----
// All handlers are bound HERE (after every function above is defined) rather
// than via inline onclick=/onload= attributes in the HTML. An inline
// onload="integrateSettings()" on the chat iframe used to fire BEFORE this
// script finished loading -> "integrateSettings is not defined", and inline
// onclick handlers can similarly throw "not defined" if clicked during a
// cold first load. Binding here guarantees the function exists when the
// handler fires.
document.getElementById("clear-history-btn").addEventListener("click", clearHistory);
document.getElementById("browse-btn").addEventListener("click", openBrowse);
document.getElementById("reset-conversation-btn").addEventListener("click", resetConversation);
document.getElementById("new-conversation-btn").addEventListener("click", startNewConversation);
document.getElementById("browse-up-btn").addEventListener("click", browseUp);
document.getElementById("browse-cancel-btn").addEventListener("click", closeBrowse);
document.getElementById("chat-frame").addEventListener("load", integrateSettings);
document.getElementById("model-provider-select").addEventListener("change", onModelProviderChange);
document.getElementById("model-name-input").addEventListener("change", saveModelSettings);
loadModelSettings();

// Close browse overlay when clicking outside the dialog content
document.getElementById("browse-overlay").addEventListener("click", function(e) {
  if (e.target === this) {
    closeBrowse();
  }
});

// Close browse overlay when pressing the Escape key
window.addEventListener("keydown", function(e) {
  if (e.key === "Escape") {
    closeBrowse();
  }
});
