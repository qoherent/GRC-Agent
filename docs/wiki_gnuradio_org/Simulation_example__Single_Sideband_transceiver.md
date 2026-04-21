# Simulation example: Single Sideband transceiver
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Single_Sideband_transceiver#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Single_Sideband_transceiver#searchInput)
This tutorial explains how a Single Sideband (SSB) signal can be generated and received. The transmitter uses the Filter method. Receivers are shown for the Filter method and the Weaver method. 
## Contents
  * [1 Prerequisites](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Single_Sideband_transceiver#Prerequisites)
  * [2 Flowgraph](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Single_Sideband_transceiver#Flowgraph)
    * [2.1 Transmitter section](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Single_Sideband_transceiver#Transmitter_section)
    * [2.2 Receiver section](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Single_Sideband_transceiver#Receiver_section)
      * [2.2.1 Filter Method](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Single_Sideband_transceiver#Filter_Method)
      * [2.2.2 Weaver Method](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Single_Sideband_transceiver#Weaver_Method)
    * [2.3 Common Audio Output](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Single_Sideband_transceiver#Common_Audio_Output)
  * [3 Testing](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Single_Sideband_transceiver#Testing)


## Prerequisites
  * [**Intro to GR usage: GRC and flowgraphs**](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_GRC "Guided Tutorial GRC")
  * [**Variables in Flowgraphs**](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs")


## Flowgraph
The following flowgraph can be downloaded by clicking [Media:SSB_demo_2.grc](https://wiki.gnuradio.org/images/5/57/SSB_demo_2.grc "SSB demo 2.grc"). Using a terminal screen, start `gnuradio-companion` and open the downloaded file. 
[![](https://wiki.gnuradio.org/images/thumb/6/60/SSB_demo_2_fg.png/824px-SSB_demo_2_fg.png)](https://wiki.gnuradio.org/index.php?title=File:SSB_demo_2_fg.png)
### Transmitter section
  * The transmitter section is across the upper row of blocks starting with the [Audio Source](https://wiki.gnuradio.org/index.php?title=Audio_Source "Audio Source") (your microphone).
  * The [Hilbert](https://wiki.gnuradio.org/index.php?title=Hilbert "Hilbert") filter converts the audio from float to complex.
  * The [Band Pass Filter](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter "Band Pass Filter") has a low frequency cutoff of 300Hz and a high frequency cutoff of 3500Hz to pass normal voice frequencies. The _FIR Type_ is set to _Float - > Complex (Complex Taps)_. Because of the complex taps, the bandpass filter passes only the positive frequencies, and removes the negative frequencies to make the single sideband signal. The _Gain_ parameter is set by the [QT GUI Range](https://wiki.gnuradio.org/index.php?title=QT_GUI_Range "QT GUI Range") block labeled "Mic gain".
  * The [Swap IQ](https://wiki.gnuradio.org/index.php?title=Swap_IQ "Swap IQ") block interchanges the Real and Imaginary components which effectively changes upper and lower sidebands.
  * The first [Selector](https://wiki.gnuradio.org/index.php?title=Selector "Selector") block chooses which input to use, based on the [QT GUI Chooser](https://wiki.gnuradio.org/index.php?title=QT_GUI_Chooser "QT GUI Chooser") labeled "Transmit Sideband".
  * The [QT GUI Time Sink](https://wiki.gnuradio.org/index.php?title=QT_GUI_Time_Sink "QT GUI Time Sink") shows a 'scope trace' of the audio signal.
  * The [QT GUI Frequency Sink](https://wiki.gnuradio.org/index.php?title=QT_GUI_Frequency_Sink "QT GUI Frequency Sink") shows the frequency plot of the audio.
  * The [QT GUI Waterfall Sink](https://wiki.gnuradio.org/index.php?title=QT_GUI_Waterfall_Sink "QT GUI Waterfall Sink") shows the signals on a waterfall (spectrogram) plot. The bandwidth of the audio can be seen clearly on the plot.
  * The second [Selector](https://wiki.gnuradio.org/index.php?title=Selector "Selector") block chooses which receive method to use.
  * The [Virtual Sink](https://wiki.gnuradio.org/index.php?title=Virtual_Sink "Virtual Sink") and [Virtual Source](https://wiki.gnuradio.org/index.php?title=Virtual_Source "Virtual Source") blocks provide connection points to allow a cleaner presentation of the flowgraph. They are functionally the same as drawing a line between the two points with the same Stream ID, and have no impact on the data flow.


### Receiver section
#### Filter Method
  * The transmitter signal is fed to the receiver through the [Virtual Source](https://wiki.gnuradio.org/index.php?title=Virtual_Source "Virtual Source") block labeled "xmt0".
  * As in the transmitter section, the [Swap IQ](https://wiki.gnuradio.org/index.php?title=Swap_IQ "Swap IQ") and [Selector](https://wiki.gnuradio.org/index.php?title=Selector "Selector") blocks choose which sideband to use, based on the [QT GUI Chooser](https://wiki.gnuradio.org/index.php?title=QT_GUI_Chooser "QT GUI Chooser") labeled "Filter rcv SB". If the lower sideband is selected, the **Swap IQ** will swap the sidebands such that the lower sideband is shifted to the upper sideband.
  * The paramters for the [FFT Filter](https://wiki.gnuradio.org/index.php?title=FFT_Filter "FFT Filter") are set by the [Band-pass Filter Taps](https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps "Band-pass Filter Taps") block. Using _Complex_ taps, the output will be only the upper sideband with a frequency range of 300Hz to 3500Hz.
  * The [Complex To Real](https://wiki.gnuradio.org/index.php?title=Complex_To_Real "Complex To Real") block produces the real part of the data stream.


#### Weaver Method
In 1956 D. K. Weaver proposed a new modulation scheme for SSB generation. The Weaver Method (also known as the Third Method), has potential advantages compared to the filter or phasing methods, particularly when using DSP. The method uses a carrier signal applied to the audio passband which has been shifted down (for USB, or up for LSB) by the frequency of the carrier injection. 
  * The transmitter signal is fed to the receiver through the [Virtual Source](https://wiki.gnuradio.org/index.php?title=Virtual_Source "Virtual Source") block labeled "xmt1".
  * The [Frequency Xlating FIR Filter](https://wiki.gnuradio.org/index.php?title=Frequency_Xlating_FIR_Filter "Frequency Xlating FIR Filter") performs the frequency shift by the amount `(sb_sel)*(-1500.0)`, where `sb_sel` is the "Weaver rcv SB" chooser value (-1 or +1).
  * A [Complex To Float](https://wiki.gnuradio.org/index.php?title=Complex_To_Float "Complex To Float") block produces separate I and Q outputs which are then multiplied by [Signal Sources](https://wiki.gnuradio.org/index.php?title=Signal_Source "Signal Source") set to the carrier frequency.
  * The [Multiply Const](https://wiki.gnuradio.org/index.php?title=Multiply_Const "Multiply Const") block uses the `sb_sel` value (-1 or +1) so that the [Add](https://wiki.gnuradio.org/index.php?title=Add "Add") block will either subtract or add the Q product from the I product. The result is a USB only or LSB only signal.


### Common Audio Output
  * The [Selector](https://wiki.gnuradio.org/index.php?title=Selector "Selector") block chooses the stream from the active filter method.
  * To produce a volume control, the [QT GUI Range](https://wiki.gnuradio.org/index.php?title=QT_GUI_Range "QT GUI Range") block labeled "Volume" sets a variable named 'volume' which is used in the [Multiply Const](https://wiki.gnuradio.org/index.php?title=Multiply_Const "Multiply Const") block. The name **Multiply Const** block seems a little confusing because the _Constant_ can be a variable, but it is to distinguish it from a [Multiply](https://wiki.gnuradio.org/index.php?title=Multiply "Multiply") block which multiplies two (or more) input streams together. The initial volume is set to -60db for your ear protection.. See "Caution" below!
  * The [Audio Sink](https://wiki.gnuradio.org/index.php?title=Audio_Sink "Audio Sink") is the computer speaker or headphones.


## Testing
**CAUTION!** To avoid loud feedback, use headphones or a microphone/headset instead of the computer speaker. 
To start the program, click the "Execute" icon or press F6. A screen will open showing the Mic gain, Volume, sideband selections, Time, Frequency, and Waterfall displays. Both transmit and receive will be in Upper Sideband mode. Initial settings use the filter method for receiving.
Speaking into the microphone, you should hear yourself clearly. Selecting Lower Sideband for both transmit and receive should also work well. Note the change of the frequencies on both the Frequency and Waterfall plots.
For the filter method, selecting Upper Sideband on transmit and Lower Sideband on receive (or vice versa) will produce almost no sound. For the Weaver method, there will be no apparent change until you tune off-frequency. At that point, tuning higher or lower will depend on which sideband is chosen, but will seem natural for those who use SSB on-the-air.
To terminate the test, click the 'X' in the upper right-hand corner of the "SSB_demo_2" screen.
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Simulation_example:_Single_Sideband_transceiver&oldid=15555](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Single_Sideband_transceiver&oldid=15555)"
[Categories](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Tutorials](https://wiki.gnuradio.org/index.php?title=Category:Tutorials "Category:Tutorials")
  * [Tested With 3.10](https://wiki.gnuradio.org/index.php?title=Category:Tested_With_3.10 "Category:Tested With 3.10")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Simulation+example%3A+Single+Sideband+transceiver "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Single_Sideband_transceiver "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Simulation_example:_Single_Sideband_transceiver&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Single_Sideband_transceiver)
  * [View source](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Single_Sideband_transceiver&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Single_Sideband_transceiver&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Simulation_example:_Single_Sideband_transceiver "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Simulation_example:_Single_Sideband_transceiver "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Single_Sideband_transceiver&oldid=15555 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Single_Sideband_transceiver&action=info "More information about this page")


  * This page was last edited on 11 December 2025, at 17:15.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


