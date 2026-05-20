# Sample Rate

Decimation is a sample-rate change that reduces the sampling rate. In GNU Radio
flowgraphs, decimation is the downsampling side of rate-change processing and
must be accounted for when reasoning about stream rates and rate-changing
blocks.

Interpolation is a sample-rate change that increases the sampling rate. The GNU
Radio Sample Rate Change tutorial describes interpolation as increasing the
sampling rate and available bandwidth, for example with an interpolating FIR
filter.

Sample-rate docs explain concepts only. They do not authorize setting variables,
choosing block defaults, or mutating graph parameters.

Provenance: Source title: Sample Rate Change. Source URL:
https://wiki.gnuradio.org/index.php/Sample_Rate_Change. Retrieval topic: sample
rate change decimation interpolation. Aliases: sample_rate, sample_rate_change,
decimation, interpolation. Official or primary: official GNU Radio Wiki page.
Why relevant: this snippet grounds docs QA rows about sample-rate change terms.
