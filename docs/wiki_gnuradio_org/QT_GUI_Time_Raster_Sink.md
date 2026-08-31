# QT GUI Time Raster Sink
This is a QT-based graphical sink that takes in numerical streams and plots a time_raster (spectrogram) plot.

This sink can plot messages that contain either uniform vectors of float 32 values (pmt::is_f32vector) or PDUs where the data is a uniform vector of float 32 values.

**Note:** This block does not limit the items per second it consumes, even though it has an "Update Rate" parameter; it will drop samples if the incoming data rate is higher than the product of the number of columns and the update rate. It is up to the user to choose an update rate that represents his processing needs.

## Parameters

(_R_): _Run-time adjustable_

Name
    Title for the plot

Sample Rate
    Sample rate of signal

Num. Rows (_R_)
    Number of rows to plot

Num. Cols (_R_)
    Number of cols to plot

Grid

Int. min

Int. max

Multiplier (_R_)
    Vector of floats as a scaling multiplier for each input stream

Offset (_R_)
    Vector of floats as an offset for each input stream

Number of Inputs
    Number of streams connected

Update Period (_R_)

GUI Hint
    See GUI Hint for info about how to organize multiple QT GUIs

Axis Labels

Line Label

Line Color

Line Alpha

X-Axis Label, X-Axis Start Value, X-Axis End Value, Y-Axis Label, Y-Axis Start Value, Y-Axis End Value (New as of 3.9)
    Allows the Time Raster to be able to look like a QT GUI Waterfall Sink if desired

## Example Flowgraph

This flowgraph and output show a QT GUI Time Raster Sink.

### Interactive Demo

This flowgraph shows how to adjust processing rate in a pure simulation flow graph using the new-style Throttle block.

[GRC Flowgraph](/images/f/fc/Demonstrate_qt_gui_raster_sink.grc "Demonstrate qt gui raster sink.grc")

## Source Files

C++ files
    [Float input](https://github.com/gnuradio/gnuradio/blob/master/gr-qtgui/lib/time_raster_sink_f_impl.cc)
    [Bit input](https://github.com/gnuradio/gnuradio/blob/master/gr-qtgui/lib/time_raster_sink_b_impl.cc)

Header files
    [Float input](https://github.com/gnuradio/gnuradio/blob/master/gr-qtgui/lib/time_raster_sink_f_impl.h)
    [Bit input](https://github.com/gnuradio/gnuradio/blob/master/gr-qtgui/lib/time_raster_sink_b_impl.h)

Public header files
    [Float input](https://github.com/gnuradio/gnuradio/blob/master/gr-qtgui/include/gnuradio/qtgui/time_raster_sink_f.h)
    [Bit input](https://github.com/gnuradio/gnuradio/blob/master/gr-qtgui/include/gnuradio/qtgui/time_raster_sink_b.h)

Block definition
    [[1]](https://github.com/gnuradio/gnuradio/blob/master/gr-qtgui/grc/qtgui_time_raster_x.block.yml)
