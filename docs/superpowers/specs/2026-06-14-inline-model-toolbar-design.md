# Inline Model Toolbar — Replacing the Setup Wizard and Model Dialog

## Problem

Every GUI launch forced the user through a 3-page setup wizard (provider picker → Ollama setup → start hint). Selections were never persisted, so the wizard reappeared every time. A separate Model > Select Model dialog duplicated model selection with different persistence behavior (writes TOML, not preferences). The dead `provider_picker_dialog.py` still shipped.

## Solution

Replaced both the wizard and the Model dialog with a single inline **ModelToolbar** widget living permanently at the top of the chat pane.

## Implementation

### ModelToolbar (`src/grc_agent_gui/model_toolbar.py`, new file)

A ~36px horizontal bar above the chat area:

```
[Provider ▾]  [Model ▾]  [● status]  [↻ Refresh]
```

- **Provider combo** (`QComboBox`): Ollama (Local) / OpenRouter (Cloud).
- **Model combo** (`QComboBox`, editable for Ollama): lists models from the active provider.
- **Status label** (`QLabel`): colored dot + text ("ready", "no model", "unreachable", "checking").
- **Refresh button** (`QToolButton`): triggers `refresh_requested` signal → `MainWindow` re-probes and repopulates.
- **Signals**: `connect_requested(backend, model_name)`, `refresh_requested()`.

### MainWindow changes (`src/grc_agent_gui/main_window.py`)

Removed:
- `setup_stack` (3-page wizard with `ProviderPickerWidget`, `OllamaSetupWidget`, `OllamaStartHintWidget`)
- `main_stack` (no longer need two-page switching — always show main view)
- `Model` menu (with `Select Model...` action and `Currently loaded` entry)
- `open_model_dialog`, `_on_model_dialog_accepted`, `_on_model_dialog_finished`
- All `_on_setup_*` handlers (`_on_setup_provider_chosen`, `_on_setup_ollama_confirmed`, etc.)
- `_update_current_model_menu`, `_display_model_alias` (dead methods)
- References to `select_model_action`, `current_model_action`

Added:
- `ModelToolbar` above the `v_splitter` in `main_layout`
- `_on_toolbar_connect(backend, model_name)` — wires to existing `ModelSwapRunnable` infrastructure
- `_on_toolbar_refresh()` — re-probes and repopulates the model list
- `_probe_and_populate_models()` — calls `discover_ollama_models`, populates the toolbar, sets `backend_reachable`
- `QTimer.singleShot(0, self._probe_and_populate_models)` at end of `__init__` — auto-probes at startup
- Persistence: `_on_model_swap_finished` now calls `update_last_model()` and `update_provider_chosen()` on every swap

### Persistence

Every model/provider change now persists to **both** `preferences.json` (via `update_last_model` / `update_provider_chosen`) and `config.toml` (existing `update_toml_config_file`). Previously only the Model dialog wrote the TOML; the wizard wrote nothing.

### States

| State | Toolbar shows | User action |
|-------|--------------|-------------|
| First launch, Ollama reachable | Provider=Ollama, Model="(select model)" | Pick model → saved → ready |
| First launch, Ollama unreachable | Provider=Ollama, Status=red, "no model" | Start Ollama → click Refresh |
| Returning launch, model saved | Provider + model pre-filled | Chat immediately |
| OpenRouter selected | Provider=OpenRouter, model from env | Chat immediately |
| Model mid-swap | Both combos disabled | Wait for swap to finish |

### Test updates

- `GuiLaunchOnProbeFailureTests.test_model_menu_remains_accessible_for_recovery` → `test_model_toolbar_remains_accessible_for_recovery` (checks toolbar instead of menu)

### Remaining dead code (not removed, no longer imported by main app)

- `src/grc_agent_gui/setup_panel.py` — wizard pages
- `src/grc_agent_gui/provider_picker_dialog.py` — standalone dialog
- `tests/gui/test_ollama_setup_flow.py`
- `tests/gui/test_provider_picker.py`
