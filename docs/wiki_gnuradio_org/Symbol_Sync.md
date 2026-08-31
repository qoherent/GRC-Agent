# Symbol Sync
The **Symbol Sync** block performs clock recovery. It synchronizes to the symbols in a digital signal, extracting them and reducing them to their individual representations, such as a bit. This is often a critical final step in the demodulation process, as clock recovery reduces a stream of samples of symbols to raw 1s and 0s. This block is the successor to the Clock Recovery MM and MSK Timing Recovery blocks, which are now deprecated.

The Symbol Sync block performs four main steps:

1\. Estimates and tracks symbol rate (i.e. number of samples per symbol), given an initial estimate of samples per symbol and an allowable deviation from that estimate.

2\. Performs the timing synchronization needed so that the signal is sampled at exactly the right moment in time, which is when each symbol/pulse is at its maximum value.

3\. Decimate the signal so that what comes out of the block is 1 sample per symbol (or multiple if the user would like, but it's usually set to 1 or sometimes 2).

4\. Filter the signal appropriately.

In essence, the Symbol Sync block operates a loop, typically a sampling length of a symbol, and then adjusts this loop until it is properly aligned with each symbol in the series.

For more information, see the [GNU Radio Conference 2017 presentation](https://www.gnuradio.org/grcon/grcon17/presentations/symbol_clock_recovery_and_improved_symbol_synchronization_blocks/) on this block (PDF slides and Video). Example flowgraphs using this block can be found [here](https://github.com/gnuradio/gnuradio/tree/main/gr-digital/examples/demod)

## Source Files

C++ files
    [symbol_sync_cc_impl.cc](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/symbol_sync_cc_impl.cc) [symbol_sync_ff_impl.cc](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/symbol_sync_ff_impl.cc)

Header files
    [symbol_sync_cc_impl.h](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/symbol_sync_cc_impl.h) [symbol_sync_ff_impl.h](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/symbol_sync_ff_impl.h)

Public header files
    [symbol_sync_cc.h](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/include/gnuradio/digital/symbol_sync_cc.h) [symbol_sync_ff.h](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/include/gnuradio/digital/symbol_sync_ff.h)

Block definition
    [digital_symbol_sync_xx.block.yml](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/grc/digital_symbol_sync_xx.block.yml)
