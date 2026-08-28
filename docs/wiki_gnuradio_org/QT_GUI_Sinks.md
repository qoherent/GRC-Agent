# QT GUI Sinks

From GNU Radio

The `qtgui_*` sink blocks draw live plots of the running flowgraph in a QT
window. All streaming sinks in this family share one declaration model, and
one parameter on them accounts for the most common GRC graph error: **Number
of Inputs** (`nconnections`) declares how many input stream ports the sink
exposes, and every declared input port exists and is wired before the flow
graph can validate — a declared-but-unconnected port raises "Port is not
connected" at validation time, so set `nconnections` to exactly the number of
signals you actually connect.

## Number of inputs and declared ports

`nconnections` (label: Number of Inputs, default 1) is a GRC parameter whose
value sets the stream-input `multiplicity` of the sink: setting it to 3
declares input ports 0, 1 and 2, and all three must be connected for the graph
to validate. The OWL-generated `asserts:` section of the QT GUI Time Sink adds
an upper bound of `nconnections <= 5` for the complex type and
`nconnections <= 10` for the float type.

`nconnections` is hidden (together with the per-stream `size` display) when
the sink's `type` starts with `msg`: message-variant sinks take exactly one
message port named "in" instead of stream ports, and the message itself
carries the data for the plot.

## QT GUI Time Sink

`qtgui_time_sink_x` is a template over type (Complex, Float, Complex Message,
Float Message) that maps to the runtime classes `time_sink_c` / `time_sink_f`.
Its docstring: "A graphical sink to display multiple signals in time. This is
a QT-based graphical sink the takes set of a float streams and plots them in
the time domain. Each signal is plotted with a different color, and the [
label] and [color] functions can be used to change the label and color for a
given input number."

Key parameters, verbatim from the block YAML:

- **Number of Points** (`size`, default 1024) — hidden for the message types.
- **Sample Rate** (`srate`) — time axis; the generated code calls
  `set_samp_rate()`.
- **Y min / Y max** (`ymin` default -1, `ymax` default 1) with **Autoscale**.
- **Trigger** (`tr_mode`): `Free` (default), `Auto`, `Normal` or `Tag`,
  together with trigger slope, level, delay, channel and a trigger tag key —
  the generated code calls `set_trigger_mode(tr_mode, tr_slope, tr_level,
  tr_delay, tr_chan, tr_tag)` at runtime.

## QT GUI Frequency Sink

`qtgui_freq_sink_x` (types Complex, Float, Complex Message, Float Message)
plots the spectrum: "A graphical sink to display multiple signals in
frequency. This is a QT-based graphical sink the takes set of a complex
streams and plots the PSD. Each signal is plotted with a different color." It
takes a **Bandwidth (Hz)** parameter for the frequency axis and declares its
stream inputs exactly like the time sink — `nconnections` declared ports, all
of which must be connected for validation to pass.

## QT GUI Waterfall Sink

`qtgui_waterfall_sink_x` (types Complex, Float, Complex Message, Float
Message) plots a spectrogram: "A graphical sink to display multiple signals on
a waterfall (spectrogram) plot." The block YAML still declares a stream input
with `multiplicity: nconnections`, but the runtime documentation adds an
explicit caveat: "unlike the other qtgui sinks, this one does not support
multiple input streams. We have yet to figure out a good way to display
multiple, independent signals on this kind of a plot. If there are any
suggestions or examples of this, we would love to see them. Otherwise, to
display multiple signals here, it's probably best to sum the signals together
and connect that here."

## QT GUI Constellation Sink

`qtgui_const_sink_x` (complex streaming + message variants): "A graphical sink
to display the IQ constellation of multiple signals. This is a QT-based
graphical sink the takes set of a complex streams and plots them on an IQ
constellation plot."

## QT GUI Histogram Sink and Number Sink

`qtgui_histogram_sink_x` (types Float, Float Message) plots a histogram: "A
graphical sink to display a histogram" whose plot lets you "set and change at
runtime the number of points to plot at once and the number of bins".

`qtgui_number_sink` renders value readouts instead of plots: "A graphical sink
to display numerical values of input streams. Displays the data stream in as a
number in a simple text box GUI along with an optional bar graph." Its input
types are Float, Short and Char (the YAML's per-type item sizes list
`gr.sizeof_float, gr.sizeof_short, gr.sizeof_char`), again with
`multiplicity: nconnections`.

## Message-port variants

The message modes (`msg_complex`, `msg_float`) on the `_x` sinks change the
connecting rule rather than the plot: the stream ports disappear (multiplicity
0), `size` and `nconnections` are hidden, and the plot is fed PMT messages on
the single "in" message port. Choose a message type when the data arrives as
PDU messages — for example from an async packet chain — instead of a
continuous sample stream.

Sources: `/usr/share/gnuradio/grc/blocks/` — `qtgui_time_sink_x.block.yml`
(`nconnections` multiplicity, the `nconnections <= 5 / <= 10` assert, trigger
mode enum, msg hiding), `qtgui_freq_sink_x.block.yml`,
`qtgui_waterfall_sink_x.block.yml` (input multiplicity + fcn mapping),
`qtgui_const_sink_x.block.yml`, `qtgui_histogram_sink_x.block.yml`,
`qtgui_number_sink.block.yml` (item sizes); installed
`gnuradio.qtgui.time_sink_f`, `freq_sink_c`, `waterfall_sink_c`,
`const_sink_c`, `histogram_sink_f`, `number_sink` docstrings (quoted).