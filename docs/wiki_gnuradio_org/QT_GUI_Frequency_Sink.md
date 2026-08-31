# QT GUI Frequency Sink
A graphical sink to display multiple signals in frequency.

This is a QT-based graphical sink that takes a set of floating point streams and plots the PSD. Each signal is plotted with a different color, and functions can be used to change the label and color for a given input number.

The sink supports plotting streaming float data or messages. The message port is named "in". The two modes cannot be used simultaneously, and should be set to 0 when using the message mode. GRC handles this issue by providing the "Float Message" type that removes the streaming port(s).

## Parameters

fftsize
    size of the FFT to compute and display. If using the PDU message port to plot samples, the length of each PDU must be a multiple of the FFT size.

wintype
    type of window to apply (see gr::fft::window::win_type)

Center Frequency
    center frequency of signal (only used for x-axis labels)

Bandwidth
    bandwidth of signal (used to set x-axis labels)

Name
    title for the plot

GUI Hint
    See GUI Hint

## Messages

### Inputs

'freq'
    set the center frequency

'bw'
    set the bandwidth

### Outputs

'freq'
    the frequency where the output plot was double-clicked

## Example Flowgraph

## Source Files

C++ files
    [TODO](https://github.com/gnuradio/gnuradio)

Header files
    [TODO](https://github.com/gnuradio/gnuradio)

Public header files
    [TODO](https://github.com/gnuradio/gnuradio)

Block definition
    [TODO](https://github.com/gnuradio/gnuradio)
