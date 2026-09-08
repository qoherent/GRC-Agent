# Band Pass Filter


This filter is a convenience wrapper for an [Decimating FIR Filter](https://wiki.gnuradio.org/index.php?title=Decimating_FIR_Filter "Decimating FIR Filter") (or the interpolating FIR filter) and a firdes taps generating function of band-pass type, i.e. calling firdes.band_pass() or firdes.complex_band_pass().
## Parameters
(_R_): [_Run-time adjustable_](https://wiki.gnuradio.org/index.php/GNURadioCompanion#Variable_Controls)

FIR Type (_R_)
    Specify whether input/output is real or complex, and if the taps are real or complex.

Decimation
    Decimation rate of filter, must be an integer, and cannot change in realtime.

Gain (_R_)
    Scaling factor applied to output.

Sample Rate (_R_)
    Input sample rate.

Low Cutoff Freq (_R_)
    Lower cutoff frequency in Hz

High Cutoff Freq (_R_)
    Upper cutoff frequency in Hz

Transition Width (_R_)
    Transition width between stop-band and pass-band in Hz

Window (_R_)
    Type of window to use

Beta (_R_)
    The beta parameter only applies to the Kaiser window.
## Example Flowgraph
This flowgraph shows the use of a Band Pass Filter block. This is a working AM broadcast band receiver.
[](https://wiki.gnuradio.org/index.php?title=File:Complex_to_Mag.png)
## Source Files

[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"):
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")
