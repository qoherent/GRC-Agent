# PlutoSDR Source
The PlutoSDR (a.k.a. ADALM-PLUTO) is a low-cost SDR made by Analog Devices, based on a [binned](https://en.wikipedia.org/wiki/Product_binning) version of the AD9364 RFIC (same RFIC as in the USRP B200) which AD labels AD9363. It can operate from 70 MHz to 6 GHz using simple "hack" described at [the bottom of this page](https://wiki.analog.com/university/tools/pluto/users/customizing), and has a max sample rate of 56 MHz, but because it only has USB 2.0, the 56 MHz can only be received in short bursts. The max sample rate when continuously receiving is more like 4 or 5 MHz. It has a Xilinx Zynq Z-7010 FPGA + ARM CPU on board, the ARM CPU runs a lightweight version of linux. It's set up to run as an IP device; the USB port on it appears as a USB to ethernet bridge. It also shows up a mass storage device which lets you easily change the config (e.g. IP address) or load new firmware.

For more info on getting the PlutoSDR installed and running, see this page <https://wiki.analog.com/resources/tools-software/linux-software/gnuradio>.

## Parameters

LO Frequency
    Selects the RX local oscillator frequency.

IIO context URI
    IP address of the unit, e.g. "ip:192.168.2.1" (without the quotes)

Sample Rate
    Sample rate in samples per second, this will define how much bandwidth your SDR receives at once (the RF bandwidth parameter below just defines the filter). limits: >= 520833 and <= 61440000

RF Bandwidth
    Configures RX analog filters: RX TIA LPF and RX BB LPF. limits: >= 200000 and <= 52000000

Buffer size
    Size of the internal buffer in samples. The IIO blocks will only input/output one buffer of samples at a time. To get the highest continuous sample rate, try using a number in the millions.

Quadrature
    True/False

RF DC Correction
    True/False

BB DC Correction
    True/False

Gain Mode (Rx1)
    Selects one of the available modes: manual, slow_attack, hybrid and fast_attack. For most spectrum sensing type applications, use a manual gain, so that you actually know when a signal is present or not, and it's relative power.

Manual Gain (Rx1)(dB)
    gain value, max of 71 dB, or 62 dB when over 4 GHz center freq

Filter
    Allows a FIR filter configuration to be loaded from a file.

Filter Auto
    When enabled loads a default filter and thereby enables lower sampling / baseband rates.

## Example Flowgraph

This flowgraph shows a broadcast FM receiver.

## Source Files

The actual source and sink blocks are created by an 'Industrial I/O' module. See <https://wiki.analog.com/resources/tools-software/linux-software/gnuradio> for details.
