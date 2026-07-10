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

    app = Application([], p)
    app.register(None)
    app.activate()

    # Load flowgraph
    fg = p.make_flow_graph(grc_file_path)
    fg.update_elements_to_draw()

    # Create Main Window to contain Scrolled DrawingArea
    window = Gtk.Window()
    window.set_title("GRC Canvas")
    window.set_default_size(1000, 800)
    window.connect("destroy", Gtk.main_quit)

    # Scrolled Window
    scrolled_window = Gtk.ScrolledWindow()
    scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

    # Instantiate the GRC DrawingArea widget
    drawing_area = DrawingArea(fg)
    fg.drawing_area = drawing_area

    # Hook into labels and shapes generation
    try:
        import cairo
        temp_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        temp_cr = cairo.Context(temp_surf)
        fg.create_labels(temp_cr)
        fg.create_shapes()
    except Exception as e:
        print("Failed to initialize canvas shapes:", e)

    scrolled_window.add(drawing_area)
    window.add(scrolled_window)

    # Auto-save triggers
    def trigger_reload():
        print("Auto-saving and reloading flowgraph...")
        try:
            p.save_flow_graph(fg, grc_file_path)
            urllib.request.urlopen("http://localhost:7932/grc/reload")
        except Exception as e:
            print("Failed to trigger reload:", e)

    # 1. Trigger save/reload on mouse button release (moves/drags)
    def on_button_release(widget, event):
        trigger_reload()
        return False

    drawing_area.connect("button-release-event", on_button_release)

    # 2. Trigger save/reload on dialog windows close (parameter edits)
    def on_window_added(application, win):
        print(f"Window added to GTK App context: {win}")
        def on_window_destroy(w, event=None):
            trigger_reload()
            return False
        win.connect("destroy", on_window_destroy)

    app.connect("window-added", on_window_added)

    window.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
