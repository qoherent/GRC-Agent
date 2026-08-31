# UHD USRP Source
The USRP Source Block is used to stream samples from a USRP device (i.e. act as the receiver).

There is no need to use a Throttle block when a hardware source like a USRP Source is used, because the USRP acts as the throttle.

There are two methods of setting parameters and adjusting them while running.

Variables and GUI Widgets
     Parameters may be set by a Variable block, a GUI Widget such as QT GUI Range, or directly in the block Properties.

Messages
     The input message port called "command" can be used to change frequency, gain, and other parameters via Message Passing. A complete list of message commands can be found in [Common command keys](https://www.gnuradio.org/doc/doxygen/page_uhd.html#uhd_command_syntax_cmds).

     A common arrangement is to use the QT GUI Sink's output message port to connect to the USRP Source's input port, so that when a user double-clicks within the GUI to change frequency, the change gets propagated to the USRP device. The Example Flowgraph section shows this and other message-based methods.

## Parameters

Output Type
     This parameter controls the data type of the output stream in GNU Radio.

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

The example flowgraph below is taken from Guided_Tutorial_Hardware_Considerations. It shows how the message ports can be connected so that changing the frequency from within the QT GUI Sink (by double clicking on the X-axis) is propagated to the USRP. Even though it is hidden by the GUI Sink block, there is a connection from the output message port (freq) on the left to the input message port (freq) on the right. Variables are used to set the sample rate, RF gain, and initial tuning.

* * *

A significant drawback to the method above is that once a frequency is selected by double clicking, the frequency displayed in the GUI Range is no longer correct. To remedy this situation, messages can be used to change all of the run-time functions. Two approaches are presented here. The first is to use a QT GUI Message Edit Box. Not only can it generate commands, but it can display commands from other sources such as a QT GUI Sink. That way, the parameter (such as frequency) is always displayed correctly. The following flowgraph using this method not only sets the frequency, but also the RF gain.

* * *

A second message-based method can be found in [UHD Message Tuner](https://github.com/gnuradio/gnuradio/blob/master/gr-uhd/examples/grc/uhd_msg_tune.grc). Each of the parameters is set using a QT GUI Entry block. Every two seconds, the Message Strobe block sends all of the parameters to the USRP. Note that the initial frequency and gain values must be set using Variable blocks.
