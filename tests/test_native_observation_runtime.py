"""Opt-in native acceptance for bounded native Qt File Sink observations.

Run under a private D-Bus session and Xvfb with GDK_BACKEND=x11,
QT_QPA_PLATFORM=xcb and WAYLAND_DISPLAY/WAYLAND_SOCKET unset. Set
GRC_NATIVE_OBSERVATION_RUNTIME=1 and use the system-bridged .venv interpreter.
The test launches GNU Radio Companion's GTK Application and dispatches its real
FLOW_GRAPH_EXEC action under the installed asyncio/GLib event loop. The child
flowgraph is generated and executed only by GRC's native Execute path.
"""

import contextlib
import os
import tempfile
from pathlib import Path

import pytest


@pytest.mark.parametrize("live", [False, pytest.param(True, marks=pytest.mark.integration)], ids=["offline", "ollama_cloud"])
@pytest.mark.gui
@pytest.mark.skipif(
    os.environ.get("GRC_NATIVE_OBSERVATION_RUNTIME") != "1",
    reason="set GRC_NATIVE_OBSERVATION_RUNTIME=1 to launch native GNU Radio Companion",
)
def test_native_observation_runtime(tmp_path, monkeypatch, live):
    import asyncio
    import math

    live_model = None
    if live:
        from dotenv import dotenv_values
        from pydantic_ai.models.ollama import OllamaModel
        from pydantic_ai.providers.ollama import OllamaProvider

        from grc_agent.settings import env_path

        config_values = dotenv_values(env_path())
        assert config_values.get("OLLAMA_CLOUD_MODEL"), "Configure OLLAMA_CLOUD_MODEL"
        assert config_values.get("OLLAMA_CLOUD_API_KEY"), "Configure Ollama Cloud credentials"
        live_model = OllamaModel(
            config_values["OLLAMA_CLOUD_MODEL"],
            provider=OllamaProvider(base_url="https://ollama.com/v1", api_key=config_values["OLLAMA_CLOUD_API_KEY"]),
        )

    assert os.environ.get("GDK_BACKEND") == "x11", "Use the isolated Xvfb command"
    assert os.environ.get("QT_QPA_PLATFORM") == "xcb", "Qt must use the private X display"
    assert not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("WAYLAND_SOCKET")

    import numpy as np

    config = tmp_path / "xdg-config"
    data = tmp_path / "xdg-data"
    cache = tmp_path / "xdg-cache"
    state = tmp_path / "xdg-state"
    project = tmp_path / "project"
    for directory in (config, data, cache, state, project):
        directory.mkdir(parents=True, exist_ok=True)
    (state / "gnuradio").mkdir()
    (config / "gnuradio").mkdir()
    settings_file = tmp_path / "isolated.env"
    settings_file.write_text("GRC_PROVIDER=ollama_local\nGRC_PROJECT_DIR=\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("GRC_AGENT_ENV", str(settings_file))

    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk
    assert Gdk.Display.get_default().get_name() == os.environ["DISPLAY"]

    from grc_agent import event_loop, fs_tools
    from grc_agent.adapter import (
        change_graph,
        get_gui_platform,
        get_platform,
        gui_actions,
        gui_application_cls,
        register_execution_messenger,
    )
    from grc_agent.exec_monitor import ExecutionErrorMonitor
    from grc_agent.native_canvas import NativeCanvasManager, NativeFlowgraphProxy

    event_loop.install()
    loop = event_loop.main_event_loop()
    platform = get_gui_platform()
    core_platform = get_platform()
    graph_path = project / "finite_observation.grc"
    first_output = project / "capture_first.f32"
    second_output = project / "capture_second.f32"

    graph = core_platform.make_flow_graph()
    graph.grc_file_path = str(graph_path)
    options = graph.options_block
    options.params["id"].set_value("finite_observation")
    options.params["output_language"].set_value("python")
    options.params["generate_options"].set_value("qt_gui")
    graph.rewrite()
    added = change_graph(
        graph,
        add_blocks=[
            {
                "block_id": "analog_sig_source_x",
                "instance_name": "source",
                "params": {
                    "type": "float",
                    "samp_rate": "48000",
                    "waveform": "analog.GR_COS_WAVE",
                    "freq": "750",
                    "amp": "0.25",
                    "offset": "0",
                    "phase": "0",
                },
            },
            {
                "block_id": "blocks_head",
                "instance_name": "head",
                "params": {"type": "float", "num_items": "4096"},
            },
            {
                "block_id": "blocks_file_sink",
                "instance_name": "sink",
                "params": {
                    "type": "float",
                    "file": str(first_output),
                    "append": "False",
                    "unbuffered": "False",
                    "vlen": "1",
                },
            },
        ],
        add_connections=["source:0->head:0", "head:0->sink:0"],
    )
    assert added["ok"] is True, added
    graph.validate()
    assert graph.is_valid(), list(graph.iter_error_messages())
    core_platform.save_flow_graph(str(graph_path), graph)
    assert graph_path.is_file()

    application_cls = gui_application_cls()
    application = application_cls([str(graph_path)], platform)
    application.register(None)
    application.activate()
    window = application.get_active_window()
    assert window is not None
    page = window.current_page
    assert Path(page.file_path).resolve() == graph_path.resolve()
    assert Path(page.flow_graph.grc_file_path).resolve() == graph_path.resolve()

    # GRC's Execute handler probes xterm availability even for the qt_gui
    # workflow. Isolate that preference so the no-xterm warning cannot open a
    # modal dialog in headless acceptance.
    application.config.xterm_missing(platform.config.xterm_executable)
    errors = []
    monitor = ExecutionErrorMonitor(
        on_error=lambda code, log, spawn_failed=False: errors.append((code, log, spawn_failed))
    )
    register_execution_messenger(monitor.handle_message)
    monitor.set_process_provider(lambda: (id(page), getattr(page, "process", None)))

    canvas = NativeCanvasManager(window, platform)
    canvas.app = application
    canvas.setup_signal_handlers()
    proxy = NativeFlowgraphProxy(canvas, exec_monitor=monitor)
    fs_tools.set_active_graph_providers(
        grc_path_fn=lambda: canvas.path,
        flow_graph_fn=lambda: canvas.current_flow_graph,
        project_dir_fn=lambda: project,
    )
    window.show_all()
    actions = gui_actions()
    executed_processes = []
    actions.FLOW_GRAPH_EXEC.connect("activate", lambda *_args: executed_processes.append(page.process))

    async def qualify_live_model(run_id, expected_match, expected_rms):
        if live_model is None:
            return
        from pydantic import BaseModel
        from pydantic_ai import Agent
        from pydantic_ai.messages import ToolCallPart, ToolReturnPart
        from pydantic_ai.usage import UsageLimits

        from grc_agent.agent import grc_tools

        class Verdict(BaseModel):
            matches_request: bool
            observed_rms: float
            peak_frequency_hz: float
            evidence_run_id: int

        agent = Agent(
            live_model, output_type=Verdict, name="native_observation_acceptance",
            tools=[tool for tool in grc_tools() if tool.name == "grc_interaction"],
        )
        result = await asyncio.wait_for(agent.run(
            f"Inspect existing captured run {run_id} using numeric measurement evidence. "
            "Do not start, stop, or edit the graph; do not request images. "
            "Select offset 0 and count 4096, sample rate 48000 Hz. "
            "The requested signal is a 750 Hz real cosine with peak amplitude 0.25. "
            "Decide whether the actual measured RMS matches 0.25/sqrt(2) within absolute "
            "tolerance 0.000001 and peak frequency matches 750 Hz within 0.000001 Hz. "
            "Report actual measurements and evidence run ID, not values assumed from the request.",
            deps=proxy, usage_limits=UsageLimits(request_limit=4),
        ), timeout=120)
        verdict = result.output
        assert verdict.matches_request is expected_match
        assert verdict.observed_rms == pytest.approx(expected_rms, abs=1e-6)
        assert verdict.peak_frequency_hz == pytest.approx(750, abs=1e-6)
        assert verdict.evidence_run_id == run_id
        calls = [p for message in result.all_messages() for p in message.parts
                 if isinstance(p, ToolCallPart) and p.tool_name == "grc_interaction"]
        assert calls and all(call.args_as_dict()["mode"] == "measure" for call in calls)
        evidence = [p for message in result.all_messages() for p in message.parts
                    if isinstance(p, ToolReturnPart) and p.tool_name == "grc_interaction"]
        assert evidence and all(part.outcome == "success" for part in evidence)
        print("LIVE_OBSERVATION", {"model": live_model.model_name, **verdict.model_dump()})

    async def accept_two_runs():
        first = await proxy.run_observed_flowgraph(
            "sink", wait=True, stop_after_seconds=5.0
        )
        # Independent oracle checks actual native output, even if lifecycle fails.
        actual = np.fromfile(first_output, dtype=np.float32)
        assert first_output.stat().st_size == 4096 * 4
        expected = 0.25 * np.cos(2 * np.pi * 750 * np.arange(4096) / 48000)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-6)
        assert first["status"] == "stopped_after_timeout", (first, monitor.get_last_run_log())
        # The GUI lifecycle is stopped, not claimed naturally successful.
        # The independent oracle above establishes the requested sample window.
        assert first["observation_status"] == "sealed", first
        assert first["run_id"] == 1
        first_measurement = proxy.measure_run(first["run_id"], 0, 4096, 48000, include_image=True)
        assert first_measurement.__class__.__name__ == "ToolReturn"
        first_metrics = first_measurement.return_value["measurements"]
        assert first_metrics["dominant_frequency"] == pytest.approx(750, abs=1e-6)
        assert first_metrics["count"] == 4096
        assert first_metrics["finite_count"] == 4096
        assert first_metrics["mean_real"] == pytest.approx(0.0, abs=1e-5)
        assert first_metrics["rms_magnitude"] == pytest.approx(0.25 / math.sqrt(2), abs=1e-6)
        first_png = first_measurement.content[0].data
        assert first_png.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(first_png) > 1000
        assert first_measurement.return_value["provenance"]["total_samples"] == 4096

        await qualify_live_model(first["run_id"], True, 0.25 / math.sqrt(2))

        changed = change_graph(
            page.flow_graph,
            update_params=[
                {"instance_name": "source", "params": {"amp": "0.5"}},
                {"instance_name": "sink", "params": {"file": str(second_output)}},
            ],
        )
        assert changed["ok"] is True, changed
        saved = await proxy.save_graph()
        assert Path(saved["path"]).resolve() == graph_path.resolve()

        second = await proxy.run_observed_flowgraph(
            "sink", wait=True, stop_after_seconds=5.0
        )
        assert second["status"] == "stopped_after_timeout", (second, monitor.get_last_run_log())
        assert second["observation_status"] == "sealed", second
        assert second["run_id"] == 2
        second_measurement = proxy.measure_run(second["run_id"], 0, 4096, 48000, include_image=True)
        second_metrics = second_measurement.return_value["measurements"]
        second_actual = np.fromfile(second_output, dtype=np.float32)
        np.testing.assert_allclose(second_actual, expected * 2, rtol=0, atol=1e-6)
        assert second_metrics["dominant_frequency"] == pytest.approx(750, abs=1e-6)
        assert second_metrics["count"] == 4096
        assert second_metrics["mean_real"] == pytest.approx(0.0, abs=1e-5)
        assert second_metrics["rms_magnitude"] == pytest.approx(0.5 / math.sqrt(2), abs=1e-6)
        assert second_metrics["rms_magnitude"] == pytest.approx(
            first_metrics["rms_magnitude"] * 2, abs=1e-6
        )
        second_png = second_measurement.content[0].data
        assert second_png.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(second_png) > 1000
        assert second_measurement.return_value["provenance"]["total_samples"] == 4096
        assert second_measurement.return_value["provenance"]["path"] == str(second_output)

        # Same oracle accepts the requested amplitude and rejects the changed signal.
        def matches_requested_signal(metrics):
            return (
                metrics["count"] == 4096
                and math.isclose(metrics["rms_magnitude"], 0.25 / math.sqrt(2), abs_tol=1e-6)
                and math.isclose(metrics["dominant_frequency"], 750, abs_tol=1e-6)
            )
        assert matches_requested_signal(first_metrics)
        assert not matches_requested_signal(second_metrics)
        await qualify_live_model(second["run_id"], False, 0.5 / math.sqrt(2))
        numerical = proxy.measure_run(second["run_id"], 0, 4096, 48000)
        assert numerical.content is None
        assert numerical.return_value["measurements"] == second_metrics
        assert numerical.metadata["display_images"][0].startswith("data:image/png;base64,")
        assert numerical.return_value["provenance"]["completion"] == "stopped"
        assert page.process is None
        canvas_image = proxy.capture_canvas().content[0].data
        assert canvas_image.startswith(b"\x89PNG\r\n\x1a\n")
        for name, data in (("plot.png", second_png), ("canvas.png", canvas_image)):
            fd, temporary = tempfile.mkstemp(dir=tmp_path)
            with os.fdopen(fd, "wb") as artifact:
                artifact.write(data)
                artifact.flush()
                os.fsync(artifact.fileno())
            os.replace(temporary, tmp_path / name)
        print("NATIVE_OBSERVATION_ARTIFACTS", tmp_path)
        with pytest.raises(ValueError, match="run_id"):
            proxy.measure_run(first["run_id"], 0, 4096, 48000, include_image=True)

        changed_again = change_graph(
            page.flow_graph,
            update_params=[{"instance_name": "source", "params": {"amp": "0.75"}}],
        )
        assert changed_again["ok"] is True, changed_again
        with pytest.raises(ValueError, match="flowgraph changed"):
            proxy.measure_run(second["run_id"], 0, 4096, 48000, include_image=True)

        assert len(executed_processes) == 2
        assert all(process is not None and process.poll() is not None for process in executed_processes)
        assert monitor.get_last_run_log()["run_id"] == second["run_id"]
        assert monitor.get_last_run_log()["run_in_progress"] is False
        assert errors == []
        return first, first_metrics, second, second_metrics

    from gnuradio.grc.core import Messages

    main_window_messenger = window.add_console_line
    try:
        first, metrics_first, second, metrics_second = loop.run_until_complete(accept_two_runs())
        assert first["run_id"] != second["run_id"]
        assert metrics_second["rms_magnitude"] > metrics_first["rms_magnitude"]
    finally:
        # Test failure must not leave the generated Qt application running.
        if monitor.is_tracking or page.process is not None:
            loop.run_until_complete(proxy.run_flowgraph(action="stop"))
        for callback in (monitor.handle_message, main_window_messenger):
            with contextlib.suppress(ValueError):
                Messages.MESSENGERS_LIST.remove(callback)
        fs_tools.set_active_graph_providers(
            grc_path_fn=lambda: None,
            flow_graph_fn=lambda: None,
            project_dir_fn=lambda: None,
        )
        window.destroy()
        application.quit()
        loop.close()
