# Sample Rate Tutorial
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial#searchInput)
## Contents
  * [1 Demonstrate the effects of Sample Rate with GRC](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial#Demonstrate_the_effects_of_Sample_Rate_with_GRC)
    * [1.1 Frequency: 2000](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial#Frequency:_2000)
    * [1.2 Frequency: 15000](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial#Frequency:_15000)
    * [1.3 Frequency: 18000](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial#Frequency:_18000)
  * [2 Source hardware example](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial#Source_hardware_example)
  * [3 Sink hardware example](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial#Sink_hardware_example)
  * [4 When there is no hardware block](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial#When_there_is_no_hardware_block)


## Demonstrate the effects of Sample Rate with GRC
The following flowgraph will be used to demonstrate the effects of sample rate on signal processing. In this discussion, the [Nyquist-Shannon sampling theorem](https://en.wikipedia.org/wiki/Nyquist%E2%80%93Shannon_sampling_theorem) establishes a minimum sampling rate of twice the signal frequency. Shannon's version of the theorem states: 
> If a function x(t) contains no frequencies higher than B hertz, it is completely determined by giving its ordinates at a series of points spaced 1/(2B) seconds apart. 
Note: the Sample Rate is set to 32 kHz. 
[![](https://wiki.gnuradio.org/images/7/7c/Samp_rate_demo_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Samp_rate_demo_fg.png)
### Frequency: 2000
With the frequency set at 2000, the time trace shows the expected sine wave, and the frequency plot shows a single signal at 2.0 kHz. 
[![](https://wiki.gnuradio.org/images/thumb/e/e7/Samp_rate_demo_out1.png/600px-Samp_rate_demo_out1.png)](https://wiki.gnuradio.org/index.php?title=File:Samp_rate_demo_out1.png)
### Frequency: 15000
Setting the frequency to 15000, the frequency plot shows a single signal at 15.0 kHz, but the time trace shows significant degradation of the waveform. 
[![](https://wiki.gnuradio.org/images/thumb/7/74/Samp_rate_demo_out2.png/600px-Samp_rate_demo_out2.png)](https://wiki.gnuradio.org/index.php?title=File:Samp_rate_demo_out2.png)
### Frequency: 18000
Setting the frequency to 18000, the time trace not only shows significant degradation of the waveform, but the frequency plot shows a single signal at 14.0 kHz! This is called _aliasing_ , which is an artifact of insufficient sampling rate. 
[![](https://wiki.gnuradio.org/images/thumb/a/af/Samp_rate_demo_out3.png/600px-Samp_rate_demo_out3.png)](https://wiki.gnuradio.org/index.php?title=File:Samp_rate_demo_out3.png)
## Source hardware example
There are several factors which determine the rate at which data flows from one block to the next. However, many beginners assume that if, for example, a waveform source is set to a certain frequency, and a sample rate is set, then that output signal will be at that rate. But, as opposed to a hardware circuit, the signal is just data in a buffer. The following sections will illustrate this. 
This discussion is based on the flowgraph of a RadioTeleTYpe (RTTY) receiver. It can be found at [[1]](https://raw.githubusercontent.com/duggabe/gr-RTTY-basics/master/RTTY_rcv/RTTY_receive.grc)
[![](https://wiki.gnuradio.org/images/thumb/8/8e/RTTY_rcv.png/960px-RTTY_rcv.png)](https://wiki.gnuradio.org/index.php?title=File:RTTY_rcv.png)
Frequency shift keying (FSK) tones are input to the microphone jack of the computer which has a sample rate of 48 kHz. That data is fed to a Frequency Xlating FIR Filter which shifts the tones above and below the center frequency. It also decimates (divides) the sample rate by 50, producing an output sample rate of 960. 
The Quadrature Demod produces a signal which is positive or negative depending on whether the tone is above or below the center frequency. The gain is calculated by `samp_rate/(2*math.pi*fsk_deviation*decim)` which gives 0.898757 
The RTTY symbol time is, by definition, exactly 22 ms. yielding the familiar 45 baud (1/0.022 rounded). To get an integer number of samples per symbol, a sample rate of 500 was chosen, producing 11 samples per symbol time. 
The output of the Quadrature Demod block has a sample rate of 960; the desired sample rate is 500. The Rational Resampler interpolates (multiplies) the sample rate by 500 and decimates (divides) it by 960 to produce an output sample rate of 500. 
The 'Terminal Display Sink' is an Embedded Python Block which reads the input stream of 1's and 0's, synchronizes on the start bit, creates a Baudot character from the five data bits, converts Baudot to UTF-8, and outputs the characters to a ZMQ PUSH Message Sink. 
## Sink hardware example
Whereas the example above is fairly straight forward, timing controlled by a hardware sink must be analyzed by starting at the output and working backwards through the flowgraph! 
The following discussion is based on this flowgraph of a Morse Code generator: 
[![](https://wiki.gnuradio.org/images/thumb/3/31/MorseGen_fg.png/900px-MorseGen_fg.png)](https://wiki.gnuradio.org/index.php?title=File:MorseGen_fg.png)
For this example, the output Audio Sink has a sample rate of 48 kHz. This is fed by a Rational Resampler which interpolates (multiplies) the sample rate by 4, so the input sample rate must be 12000 (12 kHz). 
The Multiply, IIR Filter, and Uchar to Float blocks do not change the sample rate. 
The Repeat block takes each data item of input and repeats it 1200 times. (This is a form of interpolation.) This forces an input sample rate of 10, which is the desired baud rate. To provide for various code speeds, Variable blocks define the following: 
The `speed` variable in words per minute can be set by the user to any of the following: 2, 3, 4, 6, 8, 12, 16, or 24 (all are factors of 48). 
The `baud` variable = speed / 1.2 
The `repeat` variable is fixed at 1200. 
The `samp_rate` variable = baud * repeat 
The 'Morse code vector source' is an Embedded Python Block which gets characters from the 'QT GUI Message Edit Box' and converts them into vectors, where each 1 is a dot bit time and each 0 is a space of one bit time. The complete description of Morse Code is given [here](https://en.wikipedia.org/wiki/Morse_code). The Morse Code generator project, including the flowgraph and Python code, can be found in [gr-morse-code-gen](https://github.com/duggabe/gr-morse-code-gen). 
## When there is no hardware block
Some flowgraphs, such as for testing or simulation, do not involve any hardware devices to set a sample rate. In those cases a [Throttle](https://wiki.gnuradio.org/index.php?title=Throttle "Throttle") block can be used instead. 
**[Throttle](https://wiki.gnuradio.org/index.php?title=Throttle "Throttle") is not adequate when the sampling rate is already defined by a sampling hardware device, like a sound card or an SDR frontend (USRP etc.)!**
[Throttle](https://wiki.gnuradio.org/index.php?title=Throttle "Throttle") simply slows down the _processing_ of samples, and does nothing to the samples themselves. Since GNU Radio blocks can only process signal as long as there is some input in their input buffer(s) and as long as there is some space for the output in their output buffer(s), Throttle can effectively limit the throughput of a flow graph by limiting how many samples it copies from its own in- to its output, or by only consuming samples at a limited rate. 
This is very useful in situations where you want to look at a synthetic signal in a visualization: 
[![](https://wiki.gnuradio.org/images/6/62/Demonstrate_throttle.png)](https://wiki.gnuradio.org/index.php?title=File:Demonstrate_throttle.png)
This is an example from the [Throttle](https://wiki.gnuradio.org/index.php?title=Throttle "Throttle") block documentation. In the upper component, the rate at which the [Signal Source](https://wiki.gnuradio.org/index.php?title=Signal_Source "Signal Source") can produce is limited by Throttle consuming them at the specified average rate. In the lower component, the other [Signal Source](https://wiki.gnuradio.org/index.php?title=Signal_Source "Signal Source") is not limited by anything but how fast your CPU can produce data and shuffle it into the [QT GUI Time Sink](https://wiki.gnuradio.org/index.php?title=QT_GUI_Time_Sink "QT GUI Time Sink") – leading to both a fully occupied CPU core just generating the signal, and a practically useless visualization in the "Unthrottled" Qt GUI Time Sink. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial&oldid=13802](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial&oldid=13802)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Sample+Rate+Tutorial "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Sample_Rate_Tutorial "Discussion about the content page \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial)
  * [View source](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial&action=history "Past revisions of this page \[alt-shift-h\]")


More
### Search
[](https://wiki.gnuradio.org/index.php?title=Main_Page "Visit the main page")
###  Navigation
  * [Wiki Home](https://wiki.gnuradio.org/index.php?title=Main_Page)
  * [GNU Radio Website](https://gnuradio.org)
  * [FAQ](https://wiki.gnuradio.org/index.php?title=FAQ)
  * [Get a Wiki Account](https://wiki.gnuradio.org/index.php?title=Wiki_account)


###  Guides
  * [Tutorials](https://wiki.gnuradio.org/index.php?title=Tutorials)
  * [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR)
  * [Contributing](https://wiki.gnuradio.org/index.php?title=Development)


###  Wiki Tools
  * [Recent changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChanges "A list of recent changes in the wiki \[alt-shift-r\]")
  * [Random page](https://wiki.gnuradio.org/index.php?title=Special:Random "Load a random page \[alt-shift-x\]")
  * [Help](https://www.mediawiki.org/wiki/Special:MyLanguage/Help:Contents "The place to find out")


###  Tools
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Sample_Rate_Tutorial "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Sample_Rate_Tutorial "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial&oldid=13802 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial&action=info "More information about this page")


  * This page was last edited on 4 April 2024, at 15:12.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


