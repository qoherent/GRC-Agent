# ruff: noqa: E402
import os
import sys
import urllib.request

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk
from gnuradio import gr
from gnuradio.grc.gui.Platform import Platform
from gnuradio.grc.gui.DrawingArea import DrawingArea
from gnuradio.grc.gui.Application import Application


def main():
    if len(sys.argv) < 2:
        print("Usage: python canvas_app.py <path_to_grc>")
        sys.exit(1)

    grc_file_path = os.path.abspath(sys.argv[1])
    print(f"Starting canvas app for: {grc_file_path}")

    # Set up GRC Platform & Application Context
    p = Platform(
        version=gr.version(),
        version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
        prefs=gr.prefs(),
        install_prefix=gr.prefix()
    )
    p.build_library()

    # Pass the flowgraph path directly to the native Application to load it inside the MainWindow
    app = Application([grc_file_path], p)
    app.register(None)
    app.activate()

    # Get the active MainWindow created by the application
    window = Gtk.Application.get_default().get_active_window()
    if not window:
        print("Failed to get GRC active MainWindow")
        sys.exit(1)

    # Set window properties to occupy full screen/viewport
    window.set_default_size(1200, 900)
    window.connect("destroy", Gtk.main_quit)

    # Hide MenuBar, Toolbar, and Block Library right sidebar to show only the canvas
    try:
        vbox = window.get_children()[0]
        vbox.get_children()[0].hide()  # Hide MenuBar
        vbox.get_children()[1].hide()  # Hide Toolbar

        hpaned = vbox.get_children()[2]
        hpaned.get_children()[1].hide()  # Hide Block Library right sidebar
    except Exception as e:
        print("Failed to hide GRC window components:", e)

    # Recursively find the DrawingArea widget to attach auto-save triggers
    def find_drawing_area(widget):
        if widget.__class__.__name__ == 'DrawingArea':
            return widget
        if hasattr(widget, 'get_children'):
            for child in widget.get_children():
                res = find_drawing_area(child)
                if res:
                    return res
        return None

    drawing_area = find_drawing_area(window)

    def trigger_reload():
        print("Auto-saving and reloading flowgraph...")
        try:
            if drawing_area and hasattr(drawing_area, "_flow_graph"):
                p.save_flow_graph(drawing_area._flow_graph, grc_file_path)
            urllib.request.urlopen("http://localhost:7932/grc/reload")
        except Exception as e:
            print("Failed to trigger reload:", e)

    # 1. Save/reload when dragging/moving blocks (button release)
    def on_button_release(widget, event):
        trigger_reload()
        return False

    if drawing_area:
        drawing_area.connect("button-release-event", on_button_release)

    # 2. Save/reload when properties dialogs are closed (parameter edits)
    def on_window_added(application, win):
        # We only want to listen to properties dialog windows (not the main window itself)
        if win != window:
            print(f"Properties dialog added to context: {win}")
            def on_window_destroy(w, event=None):
                trigger_reload()
                return False
            win.connect("destroy", on_window_destroy)

    app.connect("window-added", on_window_added)

    Gtk.main()


if __name__ == "__main__":
    main()
