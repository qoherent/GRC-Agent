# AGC


High performance Automatic Gain Control. Power is approximated by absolute value.
Here is a diagram showing how it works:
[](https://wiki.gnuradio.org/index.php?title=File:AGC_loop.jpg)
## Parameters
(_R_): [_Run-time adjustable_](https://wiki.gnuradio.org/index.php/GNURadioCompanion#Variable_Controls)

Rate (_R_)
    The update rate of the loop.

Reference (_R_)
    Reference value to adjust signal power to.

Gain (_R_)
    Initial gain value.

Max gain (_R_)
    Maximum gain value (0 for unlimited)
## Example Flowgraph
This flowgraph shows an Automatic Gain Control block in an AM receiver.
[](https://wiki.gnuradio.org/index.php?title=File:FunCube_AM.png)
## Source Files

C++ files
    [Complex input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/lib/agc_cc_impl.cc)     [Float input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/lib/agc_ff_impl.cc)     [Algorithms implementation](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/include/gnuradio/analog/agc.h)

Header files
    [Complex input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/lib/agc_cc_impl.h)     [Float input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/lib/agc_ff_impl.h)

Public header files
    [Complex input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/include/gnuradio/analog/agc_cc.h)     [Float input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/include/gnuradio/analog/agc_ff.h)

Block definition
    [Yaml](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/grc/analog_agc_xx.block.yml)

[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"):
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")
