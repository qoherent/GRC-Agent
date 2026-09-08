# Band-pass Filter Taps


Generates taps for a band-pass filter and stores it in variable called whatever the ID is set to. It's essentially a convenience wrapper for calling firdes.band_pass() or firdes.complex_band_pass(). None of the parameters are realtime changeable because the taps are created once at the beginning of the flowgraph.
## Parameters

ID
    Similar to the [Variable](https://wiki.gnuradio.org/index.php?title=Variable "Variable") block, the ID will hold the values generated.

Tap Type
    Whether the taps are real or complex.

Gain
    Scaling factor applied to output.

Sample Rate
    Input sample rate.

Low Cutoff Freq
    Lower cutoff frequency in Hz

High Cutoff Freq
    Upper cutoff frequency in Hz

Transition Width
    Transition width between stop-band and pass-band in Hz

Window
    Type of window to use

Beta
    The beta paramater only applies to the Kaiser window.
## Example Flowgraph
This flowgraph can be found at <https://github.com/gnuradio/gnuradio/blob/master/gr-filter/examples/filter_taps.grc>.
[](https://wiki.gnuradio.org/index.php?title=File:Filter_taps_fg.png)
## Source Files

[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"):
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")
