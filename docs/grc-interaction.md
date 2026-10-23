# Using GRC interaction evidence

`grc_interaction` groups native execution and observation. Catalog search, graph inspection/editing, saving, filesystem, shell, and web retain their separate responsibilities.

| Mode | Result | Approval |
| --- | --- | --- |
| `start` | Native GRC Execute; optional File Sink capture | Required |
| `stop` | Native GRC Stop; finalizes a pending capture when exit is observed | Not required |
| `log` | Run-associated stdout/stderr, exit/error/truncation facts | Not required |
| `measure` | Numeric measurements; plot shown in chat without sending image content to the model | Not required |
| `plot` | Measurements plus image content for a vision-capable model | Not required |
| `canvas` | GRC-native topology image for a vision-capable model | Not required |

## Capture workflow

1. Use normal graph editing and its approval flow to add a standard File Sink at the intended observation point. Choose an output path inside the project that does not yet exist and disable append. Save an unsaved graph before execution.
2. Start with `mode="start"`, `sink="<instance name>"`, `wait=true`. A persistent GUI graph can use `stop_after_seconds`; a wait timeout alone leaves it running. If the run is still active, stop it before measuring.
3. Use the returned `run_id` in `mode="measure"`, with an explicit sample range and the actual observation sample rate when known. Zero sample rate means unknown, so spectral units remain cycles/sample.
4. Compare the returned measurements against independent task requirements and tolerances. Check total and selected sample counts. `completion="stopped"` means a deliberately stopped capture, not proof that an entire signal or protocol finished.

Raw File Sink samples have no embedded rate metadata. The tool derives dtype/vector length from the executed GRC graph; sample rate remains explicitly caller-supplied. Vector records are reported as flattened scalar samples—do not assume that frequency-domain vectors or channel vectors form a temporal waveform.

Each measurement carries graph/file/run/sink identity, dtype, vector length, selected range, total/omitted samples, selected-byte SHA-256 and completion provenance. Changed files, graphs, runs and pages are rejected. Empty/nonfinite data cannot produce a passing-looking numeric result. An FFT peak is a bin estimate using a rectangular window with DC included; it is not an SNR estimate or a generic correctness verdict.

The human-readable plot is stored in standard tool-return metadata for `measure`, so a text-only model can still verify numerical evidence. `plot` and `canvas` intentionally include model image content and require image-capable model support. Tool images and text are retained on transcript replay.

## Native runtime prerequisite

GNU Radio's native runner must publish the spawned process to the page that Execute/Stop controls. Some distro builds of GNU Radio 3.10.12 omitted that assignment, leaving native Stop unable to see the running process. Qualify Execute/Stop and process ownership when installing or upgrading GNU Radio; fix the native package rather than introducing a replacement runner.

## Verification

The [native observation test](../tests/test_native_observation_runtime.py) checks actual samples, run identity, process exit, stale-evidence rejection, numerical results, and generated plots. Its opt-in live variant checks that the configured Ollama Cloud model accepts a known-good signal and rejects a deliberately wrong signal through the numerical tool. This evidence is specific to the tested task; RF hardware and arbitrary protocols need their own acceptance cases.

Run GUI tests on an isolated display and application session. Force both GUI stacks onto Xvfb so an existing Wayland session cannot receive test windows:

```bash
dbus-run-session -- xvfb-run -a env -u WAYLAND_DISPLAY -u WAYLAND_SOCKET \
  GDK_BACKEND=x11 QT_QPA_PLATFORM=xcb GRC_NATIVE_OBSERVATION_RUNTIME=1 \
  uv run --python .venv/bin/python pytest tests/test_native_observation_runtime.py -m 'not integration'
```

For the live variant, use `-m integration` and the Ollama Cloud model/key configured in `.env`. Do not treat successful unit tests or a clean process exit as proof of the requested signal behavior.
