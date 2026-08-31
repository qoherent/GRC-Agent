# Freq Xlating FIR
The **Frequency Translating Finite Impulse Response Filter** block performs a frequency translation on the signal and simultaneously downsamples the signal via a decimating FIR filter. The main use of this block is an effective channelizer, to pull out a narrowband portion of a wideband signal, without that narrowband portion having to be centered in frequency. Channelization in this manner is particularly useful for Software Defined Radios (SDRs) that capture a wide bandwidth via a very high sampling rate, yet the desired signal only occupies a narrow slice of bandwidth.

This block does not support C++ output, so it cannot be used when the output language of a flowgraph in GRC is C++.

See [this page](http://blog.sdr.hu/grblocks/xlating-fir.html) for more details.

## Source Files

C++ files
    [freq_xlating_fir_filter_impl.cc](https://github.com/gnuradio/gnuradio/blob/main/gr-filter/lib/freq_xlating_fir_filter_impl.cc)

Header files
    [freq_xlating_fir_filter_impl.h](https://github.com/gnuradio/gnuradio/blob/main/gr-filter/lib/freq_xlating_fir_filter_impl.h)

Block definition
    [filter_freq_xlating_fir_filter_xxx.block.yml](https://github.com/gnuradio/gnuradio/blob/main/gr-filter/grc/filter_freq_xlating_fir_filter_xxx.block.yml)
