# Guided Tutorial Hardware Considerations
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations#searchInput)
## Contents
  * [1 Introduction](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations#Introduction)
  * [2 What Will I Need?](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations#What_Will_I_Need?)
  * [3 Creating a Software Radio Spectrum Analyzer](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations#Creating_a_Software_Radio_Spectrum_Analyzer)
    * [3.1 Setting Parameters](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations#Setting_Parameters)
    * [3.2 Tuning and Using the Spectrum Analyzer](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations#Tuning_and_Using_the_Spectrum_Analyzer)
  * [4 Hardware Considerations](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations#Hardware_Considerations)
  * [5 Building an FM Receiver](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations#Building_an_FM_Receiver)


## Introduction
One of the more basic (and also incredibly useful) things you can do in GNU Radio with a receiver is to create a software radio spectrum analyzer. This is also a great first step because it will verify that your hardware has basic functionality. 
## What Will I Need?
There is a large and growing number of SDRs that have GNU Radio support. They span from very cheap ($20) receivers like the RTL-SDR to very high-performance tens-of-thousands-of-dollars systems. Examples of four popular devices are presented below. Other devices are listed in [Hardware](https://wiki.gnuradio.org/index.php?title=Hardware "Hardware"). 
## Creating a Software Radio Spectrum Analyzer
This example uses an [USRP Source](https://wiki.gnuradio.org/index.php?title=USRP_Source "USRP Source") block, so it should work for almost all USRP SDRs, such as the [USRP B series](https://kb.ettus.com/B200/B210/B200mini/B205mini) which have a continuous frequency coverage from 70 MHz to 6 GHz and a maximum sample rate of 56 MHz. 
In order to use UHD blocks, you must have UHD installed, although most methods of installing GNU Radio come with UHD. See [InstallingGR](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR") for more info. 
Using gnuradio-companion (GRC), build the following flowgraph.  

[![](https://wiki.gnuradio.org/images/1/17/HW_tutorial_fg.png)](https://wiki.gnuradio.org/index.php?title=File:HW_tutorial_fg.png)
### Setting Parameters
The USRP Source parameters are set as shown below. 
Note that we want the Automatic Gain Control (AGC) to be disabled, because we will be browsing spectrum that often will be empty, and it's tough to tell when a signal is present or not when the AGC is constantly adjusting the gain so that the received samples are always at a constant level. You can think of AGC like something automatically adjusting the volume knob. AGC being enabled is better suited for applications when you know a signal exists on the frequency you are tuned to. 
[![](https://wiki.gnuradio.org/images/thumb/6/6b/HW_tutorial_p1.png/400px-HW_tutorial_p1.png)](https://wiki.gnuradio.org/index.php?title=File:HW_tutorial_p1.png) [![](https://wiki.gnuradio.org/images/thumb/8/85/HW_tutorial_p2.png/400px-HW_tutorial_p2.png)](https://wiki.gnuradio.org/index.php?title=File:HW_tutorial_p2.png)
### Tuning and Using the Spectrum Analyzer
Set the following parameters in the [QT GUI Sink block](https://wiki.gnuradio.org/index.php?title=QT_GUI_Sink "QT GUI Sink"): 
  * Center Frequency (Hz): tuning
  * Bandwidth (Hz): samp_rate
  * Show RF Freq: Yes
  * Show Msg Ports: True


The analyzer can be tuned with the Frequency control widget. If you check the "Display RF Frequencies" box in the run-time GUI window, then the scale on the 'Frequency Display' and 'Waterfall Display' tabs will show the actual frequencies with the tuned frequency in the center. Keep in mind that the IQ samples coming from the SDR are at baseband, i.e. at 0 Hz, the signal was down-converted to baseband before it was sampled by the Analog to Digital Converter (ADC). 
With the message output of the QT GUI Sink block (freq) connected to the message input (command) of the USRP Source, you can double click the display on the Frequency or Waterfall screens and it also will tune to the selected frequency. This is useful for centering on a signal. Before you click, the frequency will be displayed with the cursor. 
You can adjust the Sample Rate to see more or less spectrum at a time (because these SDRs use quadrature sampling, the amount of bandwidth we see at once is equal to the sample rate). This trace shows four FM stations. 
[![](https://wiki.gnuradio.org/images/thumb/b/b3/HW_tutorial_freq.png/800px-HW_tutorial_freq.png)](https://wiki.gnuradio.org/index.php?title=File:HW_tutorial_freq.png)
## Hardware Considerations
Setting the sample rate involves several factors to consider. 
  * The various hardware devices have limits on what sample rates they can deliver. Some, such as the FunCube Pro+, have a fixed sample rate of 192kHz. Setting the flowgraph sample rate must be within the limitations of the device.
  * The computer hardware and operating system you are using will set limitations on the data throughput, such as: 
    * USB2 vs USB3
    * processor speed
    * number of CPU cores
  * If you are using a USRP, data overruns are indicated by the letter 'O' displayed on the terminal screen. These are because the input data stream is producing data faster than the computer can consume it, so it could be due to a USB bottleneck, or the flowgraph is trying to do too much with those samples, or the CPU is not powerful enough, etc. Adjusting the sample rate and/or the input buffer size (where available) may alleviate the problem.


## Building an FM Receiver
Now that you have a tested input device, you can build an FM Receiver with it. See the following flowgraphs. 
  * [USRP FM Receiver flowgraph](https://wiki.gnuradio.org/index.php?title=File:USRP_FM_fg.png "File:USRP FM fg.png")
  * [PlutoSDR FM Receiver flowgraph](https://wiki.gnuradio.org/index.php?title=File:Pluto_FM_fg.png "File:Pluto FM fg.png")
  * [FunCubePro+ FM Receiver flowgraph](https://wiki.gnuradio.org/index.php?title=File:Broadcast_FM_fg.png "File:Broadcast FM fg.png")
  * [RTL-SDR FM Receiver flowgraph](https://wiki.gnuradio.org/index.php?title=File:RTLSDR_receive_fg.png "File:RTLSDR receive fg.png")


For audio considerations, see [Audio_Sink](https://wiki.gnuradio.org/index.php?title=Audio_Sink "Audio Sink"). 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations&oldid=14668](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations&oldid=14668)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Guided+Tutorial+Hardware+Considerations "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Guided_Tutorial_Hardware_Considerations&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations)
  * [View source](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Guided_Tutorial_Hardware_Considerations "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Guided_Tutorial_Hardware_Considerations "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations&oldid=14668 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Hardware_Considerations&action=info "More information about this page")


  * This page was last edited on 6 March 2025, at 11:45.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


