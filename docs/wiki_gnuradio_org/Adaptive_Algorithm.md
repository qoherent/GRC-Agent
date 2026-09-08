# Adaptive Algorithm


Creates an Adaptive Algorithm object to be used in [Linear_Equalizer](https://wiki.gnuradio.org/index.php?title=Linear_Equalizer "Linear Equalizer") or [Decision_Feedback_Equalizer](https://wiki.gnuradio.org/index.php?title=Decision_Feedback_Equalizer "Decision Feedback Equalizer") to define how the error signal is calculated and how taps are updated, depending on the specified algorithm
## Parameters
(_R_): [_Run-time adjustable_](https://wiki.gnuradio.org/index.php/GNURadioCompanion#Variable_Controls)

Algorithm Type
    enum to specify which adaptive algorithm will be used; LMS, NLMS, CMA are the valid choices

Digital Constellation Object
    A [Constellation_Object](https://wiki.gnuradio.org/index.php?title=Constellation_Object "Constellation Object") which specifies the modulation used to adapt using decision directed mode of the equalizer

Step Size
    Specifies how quickly the adaptive algorithm will converge. Too high and the equalizer becomes unstable. The optimal value is dependent on the statistical properties of the input signal

Modulus
    (CMA only) Specifies the number of constellation points, e.g. for QPSK modulus = 4
## Example Flowgraph
See [Linear_Equalizer](https://wiki.gnuradio.org/index.php?title=Linear_Equalizer "Linear Equalizer") for a flowgraph utilizing the Adaptive Algorithm Object
[](https://wiki.gnuradio.org/index.php?title=File:Adaptive_algorithm.png)
[](https://wiki.gnuradio.org/index.php?title=File:Adaptive_algorithm_LMS.png)
[](https://wiki.gnuradio.org/index.php?title=File:Adaptive_algorithm_CMA.png)
## Source Files

C++ files
    [TODO](https://github.com/gnuradio/gnuradio)

Header files
    [TODO](https://github.com/gnuradio/gnuradio)

Public header files
    [TODO](https://github.com/gnuradio/gnuradio)

Block definition
    [[1]](https://raw.githubusercontent.com/gnuradio/gnuradio/master/gr-digital/grc/digital_adaptive_algorithm.block.yml)

[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"):
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")
