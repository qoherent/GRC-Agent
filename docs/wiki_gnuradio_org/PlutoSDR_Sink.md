# PlutoSDR Sink
The PlutoSDR (a.k.a. ADALM-PLUTO) is a low-cost SDR made by Analog Devices, based on a [binned](https://en.wikipedia.org/wiki/Product_binning) version of the AD9364 RFIC (same RFIC as in the USRP B200) which AD labels AD9363. It is set to operate between 325 MHz and 3.8 GHz can be extended to a range of 70 MHz to 6 GHz using simple "hack" described at [the bottom of this page](https://wiki.analog.com/university/tools/pluto/users/customizing) (note that there is no performance guarantee in the extended range), and has a max sample rate of 56 MHz, but because it only has USB 2.0, the 56 MHz can only be acheived in short bursts. The max sample rate when continuously transmitting is more like 4 or 5 MHz. It has a Xilinx Zynq Z-7010 FPGA + ARM CPU on board, the ARM CPU runs a lightweight version of linux. It's set up to run as an IP device; the USB port on it appears as a USB to ethernet bridge. It also shows up as a mass storage device which lets you easily change the config (e.g. IP address) or load new firmware.

For more info on getting the PlutoSDR installed and running, see this page <https://wiki.analog.com/resources/tools-software/linux-software/gnuradio>.

## Parameters

IIO context URI
    IP address of the unit, e.g. "ip:192.168.2.1" (without the quotes)

LO Frequency
    Selects the TX local oscillator frequency.

Sample Rate
    Sample rate in samples per second, this will define how much bandwidth your SDR transmits (the RF bandwidth parameter below just defines the filter). limits: >= 520833 and <= 61440000. A FIR filter needs to be loaded or set to auto for values below 2.083 MSPS.

RF Bandwidth
    Configures TX analog filters: TX BB LPF and TX Secondary LPF. limits: >= 200000 and <= 52000000

Buffer size
    Size of the internal buffer in samples. The IIO blocks will only input/output one buffer of samples at a time. To get the highest continuous sample rate, try using a number in the millions.

Cyclic
    Set to “true” if the “cyclic” mode is desired. In this case, the first buffer of samples will be repeated on the PlutoSDR until the program is stopped. The PlutoSDR IIO block will report its processing as complete: the blocks connected to the PlutoSDR IIO block won't execute anymore, but the rest of the flow graph will.

Attenuation TX1 (dB)
    Controls attenuation for TX1. The range is from 0 to 89.75 dB in 0.25dB steps. **Note:** Maximum output occurs at 0 attenuation.

Filter
    Allows a FIR filter configuration to be loaded from a file.

Filter Auto
    When enabled loads a default filter and thereby enables lower sampling / baseband rates.

## Example Flowgraph

This flowgraph shows a signal source feeding a Narrow Band FM modulator driving a PlutoSDR Sink block.

## Source Files

The actual source and sink blocks are created by an 'Industrial I/O' module. See <https://wiki.analog.com/resources/tools-software/linux-software/gnuradio> for details.
