# QT GUI Time Sink
A graphical sink to display multiple signals in time.

This block does not support C++ output, so it cannot be used when the output language of a flowgraph in GRC is C++.

This is a QT-based graphical sink that takes sets of float or complex streams and plots them in the time domain. Each signal is plotted with a different color, and options of the block can be used to change the label and color for a given input number.

The sink supports plotting streaming float data, complex data or messages. The message port is named "in". The two modes cannot be used simultaneously, and should be set to 0 when using the message mode. GRC handles this issue by providing the "Float Message" type that removes the streaming port(s).

There are many parameters, across three different tabs of General, Trigger and Config, most of which are self-explanatory.

## Source Files

C++ files
    [time_sink_c_impl.cc](https://github.com/gnuradio/gnuradio/blob/main/gr-qtgui/lib/time_sink_c_impl.cc) (complex)
    [time_sink_f_impl.cc](https://github.com/gnuradio/gnuradio/blob/main/gr-qtgui/lib/time_sink_f_impl.cc) (float)

Header files
    [time_sink_c_impl.h](https://github.com/gnuradio/gnuradio/blob/main/gr-qtgui/lib/time_sink_c_impl.h) (complex)
    [time_sink_f_impl.h](https://github.com/gnuradio/gnuradio/blob/main/gr-qtgui/lib/time_sink_f_impl.h) (float)

Public header files
    [time_sink_c.h](https://github.com/gnuradio/gnuradio/blob/main/gr-qtgui/include/gnuradio/qtgui/time_sink_c.h) (complex)
    [time_sink_f.h](https://github.com/gnuradio/gnuradio/blob/main/gr-qtgui/include/gnuradio/qtgui/time_sink_f.h) (float)

Block definition
    [qtgui_time_sink_x.block.yml](https://github.com/gnuradio/gnuradio/blob/main/gr-qtgui/grc/qtgui_time_sink_x.block.yml)
