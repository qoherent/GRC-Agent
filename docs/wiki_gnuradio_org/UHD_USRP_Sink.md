# UHD USRP Sink
Block used to stream samples to a USRP device (i.e. act as the transmitter).

To adjust parameters like Center Frequency while running, use QT GUI Range or another control.

There is no need to use a Throttle block when a hardware sink like a USRP Sink is used, because the USRP acts as the throttle.

The input message port called "command" can be used to change frequency, gain, and other parameters via Message Passing. A common arrangement is to use the QT GUI Frequency Sink's output message port and connect it to the USRP Source/Sink's input port, so that when a user double-clicks within the GUI to change frequency, the change gets propagated to the USRP device. A complete list of commands can be found in [Common command keys](https://www.gnuradio.org/doc/doxygen/page_uhd.html#uhd_command_syntax_cmds).

## Bursty Transmission

There are multiple ways to do bursty transmission without triggering underruns:

  1. Using SOB/EOB tags (start and end of burst) which are pmt booleans. When present, burst tags should be set to true (pmt::PMT_T). The "tx_sob", "tx_time", and "tx_eob" stream tags are used to control burst timing. "tx_sob" refers to Transmit, Start of Burst. The "tx_time" tag includes the time at which this burst is supposed to begin. Both of these tags must be associated with the first sample of the transmit burst. The "tx_eob" tag refers to Transmit, End of Burst. This tag must be associated with the last sample of a burst. This will tell the USRP when it can idle its transmit hardware. To use this style of transmit time tagging, leave the "tsb_tag_name" parameter in the usrp_sink constructor blank in your radio application. The offset of the "tx_sob" will usually be exactly one sample after the preceding tx_eob tag's offset. While the "tx_sob" and "tx_eob" value should simply be PMT_T, the value of "tx_time" is a PMT encoded tuple of values. The first item in the pair is the PMT encoded uint64 epoch time (wall time). The second item in the pair is the PMT encoded double fractional epoch time. An epoch timestamp of 1416299676.3453495 would appear as (1416299676, 0.3453495). If sob/eob tags or length tags are used, this block understands that the data is bursty, and will configure the USRP to make sure there's no underruns after transmitting the final sample of a burst. If tsb_tag_name is not an empty string, all "tx_sob" and "tx_eob" tags will be ignored, and the input is assumed to a tagged stream.
  2. Using tagged streams (See Tagged Stream Blocks). In GNU Radio, a tagged stream specifically refers to using a "length" tag to tell GNU Radio blocks how long a specific chunk of samples is. This can be useful when doing packet processing, where the number of samples to be used is known. The USRP Sink block supports the use of Tagged Streams for timed bursts. To use this capability, you must specify which string the usrp_sink block should be looking for to denote the length of the next PDU to be transmitted. Set the "tsb_tag_name" parameter in the usrp_sink to whatever string your radio application uses to denote your PDU length. A commonly used string for this purpose is simply "tx_pkt_len". If using Tagged Streams for timed bursts, you must include your "tx_pkt_len" tag and a "tx_time" tag on the first sample of a tx burst. If your first "tx_pkt_len" tag has an offset of 0, and your packet length is 1000 items, your next "tx_pkt_len" and "tx_time" tags must appear with an offset of 1000. TX bursts should not overlap, and there should not be gaps in samples between bursts. The value of "tx_pkt_len" should be a PMT encoded integer indicating the length of your PDU in items (i.e. samples) not bytes. "tx_time" works the same as described in the previous paragraph.

## Parameters:

Input Type
     This parameter controls the data type of the input stream in GNU Radio.

Wire Format
     This parameter controls the form of the data over the bus/network. Complex bytes may be used to trade off precision for bandwidth. Not all formats are supported on all devices.

Stream Args
     Optional arguments to be passed in the UHD streamer object. Streamer args is a list of key/value pairs; usage is determined by the implementation. [Docs for stream args](https://files.ettus.com/manual/structuhd_1_1stream__args__t.html#a4463f2eec2cc7ee70f84baacbb26e1ef)

Stream Channels
     Optionally used to specify which channels are used, e.g. [0,1]

Devices Address
     The device address is a delimited string used to locate UHD devices on your system. If left blank, the first UHD device found will be used. Use the device address to specify a specific device or list of devices.

       Examples: serial=12345678
                 addr=192.168.10.2
                 addr0=192.168.10.2, addr1=192.168.10.3

Device Arguments
     Other various arguments that can be passed to the USRP Source, see [USRP Device Configuration](http://files.ettus.com/manual/page_configuration.html)

Sync
     Can be used to get USRP to attempt to sync to either PC's clock, or PPS signal if it exists.

Clock Rate [Hz]
     The clock rate shouldn't be confused with sample rate, but they are related. The B2X0 and E31X USRPs use a flexible clock rate that will either be equal to the requested sample rate, or a multiple of it. Best left to default unless specific behavior is needed.

Num Mboards
     Selects the number of USRP motherboards (i.e. physical USRP devices) in this device configuration.

Mbx Clock Source
     Where the motherboard should sync its clock references. External refers to the 10 MHz input on the USRP. O/B GPSDO is the optional onboard GPSDO module, which provides its own 10 MHz (and PPS) signals.

Mbx Time Source
     Where the motherboard should sync its time references. External refers to the PPS input on the USRP. O/B GPSDO is the optional onboard GPSDO module, which provides its own PPS (and 10 MHz) signals.

Mbx Subdev Spec
     Each motherboard should have its own subdevice specification and all subdevice specifications should be the same length. Select the subdevice or subdevices for each channel using a markup string. The markup string consists of a list of dboard_slot:subdev_name pairs (one pair per channel). If left blank, the UHD will try to select the first subdevice on your system. See the application notes for further details.Single channel example: ":AB"

Num Channels
     Selects the total number of channels in this multi-USRP configuration. Ex: 4 motherboards with 2 channels per board = 8 channels total.

Sample Rate
     The number of samples per second, which is equal to the bandwidth in Hz we wish to observe. The UHD device driver will try its best to match the requested sample rate. If the requested rate is not possible, the UHD block will print an error at runtime.

TSB Tag Name
     When a nonempty string is given, the USRP Sink will look for length tags to determine transmit burst lengths. This parameter simply lets the block know which key to be monitoring for.

Chx Center Frequency
     The center frequency is the overall frequency of the RF chain.

Chx Gain Value
     Value used for gain, which is either between 0 and the max gain for the USRP (usually around 70 to 90), when using the default "Absolute" Gain Type. When using "Normalized" Gain Type, it will always be 0.0 to 1.0, where 1.0 will be mapped to the max gain of the USRP being used.

Chx Gain Type
     Absolute (in dB) or Normalized (0 to 1)

Chx Antenna
     For subdevices with only one antenna, this may be left blank. Otherwise, the user should specify one of the possible antenna choices. See the daughter-board application notes for the possible antenna choices.

Chx Bandwidth
     The bandwidth used by the USRP's anti-aliasing filter. To use the default bandwidth filter setting, this should be zero. Only certain subdevices have configurable bandwidth filters. See the daughterboard application notes for possible configurations.

Chx Enable DC Offset Correction
     Attempts to remove the DC offset, i.e. the average value of a signal, which is something that will be very visible in the Frequency domain.

Chx Enable IQ Imbalance Correction
     Attempts to correct any IQ imbalance, which is when there is a mismatch between the I and Q signal paths, typically causing a stretching effect to a constellation.

## Example Flowgraph

This flowgraph transmits an Amplitude Modulated signal.
