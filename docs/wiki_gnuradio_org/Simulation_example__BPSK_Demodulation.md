# Simulation example: BPSK Demodulation
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_BPSK_Demodulation#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_BPSK_Demodulation#searchInput)
This tutorial is a follow-on to the [QPSK_Mod_and_Demod](https://wiki.gnuradio.org/index.php?title=QPSK_Mod_and_Demod "QPSK Mod and Demod") tutorial to present the use of BPSK rather than QPSK. **It is imperative that all of the prerequisites are studied before doing this one.** Only sections which differ from the QPSK tutorial are discussed in detail. 
The difference between QPSK and BPSK is the number of bits per symbol. QPSK uses 2 bit symbols; BPSK uses 1 bit symbols. In both cases, the Constellation Modulator block uses all 8 input bits. Note that the Constellation Object is different between QPSK and BPSK. 
## Contents
  * [1 Prerequisites](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_BPSK_Demodulation#Prerequisites)
  * [2 Transmitting a Signal](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_BPSK_Demodulation#Transmitting_a_Signal)
  * [3 Adding Channel Impairments](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_BPSK_Demodulation#Adding_Channel_Impairments)
  * [4 Recovering Timing](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_BPSK_Demodulation#Recovering_Timing)
  * [5 Equalizers](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_BPSK_Demodulation#Equalizers)
  * [6 Phase and Fine Frequency Correction](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_BPSK_Demodulation#Phase_and_Fine_Frequency_Correction)
  * [7 Decoding](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_BPSK_Demodulation#Decoding)


## Prerequisites
  * The student should study each of the sections under the "Flowgraph Fundamentals" heading in [Tutorials](https://wiki.gnuradio.org/index.php?title=Tutorials "Tutorials") before attempting to do this tutorial.
  * [QPSK_Mod_and_Demod](https://wiki.gnuradio.org/index.php?title=QPSK_Mod_and_Demod "QPSK Mod and Demod")


## Transmitting a Signal
The first stage is transmitting the BPSK signal. We generate a stream of bits and modulate it onto a complex constellation. To do this, we use the [Constellation Modulator block](https://wiki.gnuradio.org/index.php?title=Constellation_Modulator "Constellation Modulator"), which uses a [Constellation Object](https://wiki.gnuradio.org/index.php?title=Constellation_Object "Constellation Object") and other settings to control the transmitted signal. 
The constellation object allows us to determine how the symbols are coded. The modulator block can then use this modulation scheme with or without differential encoding. The constellation modulator expects packed bytes, so we have a random source generator providing bytes with values 0 - 255. 
When dealing with the number of samples per symbol, we want to keep this value as small as possible (minimum value of 2). Generally, we can use this value to help us match the desired bit rate with the sample rate of the hardware device we'll be using. Since we're using simulation, the samples per symbol is only important in making sure we match this rate throughout the flowgraph. We'll use 4 here, which is greater than what we need, but useful to visualize the signal in the different domains. 
Finally, we set the excess bandwidth value. The constellation modulator uses a root raised cosine (RRC) pulse shaping filter, which gives us a single parameter to adjust the roll-off factor of the filter, often known mathematically as 'alpha'. The [bpsk_stage1.grc](https://wiki.gnuradio.org/images/9/9d/Bpsk_stage1.grc "Bpsk stage1.grc") flowgraph is shown below. Note: clicking the link will _download_ the GRC file. 
[![](https://wiki.gnuradio.org/images/3/32/Bpsk_stage1_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_stage1_fg.png)
In the constellation plot, we see the effects of the [up-sampling](https://en.wikipedia.org/wiki/Upsampling) (generating 4 samples per symbol) and filtering process. Note that all of the points lie along the In-phase axis. The RRC filter adds intentional self-interference, known as inter-symbol interference (ISI). ISI is bad for a received signal because it blurs the symbols together. We'll look into this in-depth during the timing recovery section. Right now, let's just see what we're doing to the signal. If you are just looking at the transmitted signals from this graph, then you should see that the frequency plot is showing a signal with a nice shape to it and that rolls-off into the noise. If we didn't put a shaping filter on the signal, we would be transmitting square waves that produce a lot of energy in the adjacent channels. By reducing the out-of-band emissions, our signal now stays nicely within our channel's bandwidth. 
[![](https://wiki.gnuradio.org/images/thumb/2/21/Bpsk_stage1_out.png/800px-Bpsk_stage1_out.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_stage1_out.png)
On the receive side, we get rid of the ISI by using another RRC filter. Basically, what we've done is used a filter on the transmitter, the RRC filter, that creates the ISI but controls the bandwidth and then another RRC filter at the receiver. When we convolve the two RRC filters, we get a [raised cosine filter](https://en.wikipedia.org/wiki/Raised-cosine_filter). The output of the receive-side RRC filter is a raised cosine shaped signal with minimized ISI. 
## Adding Channel Impairments
Adding channel impairments is the same as described in the QPSK tutorial section [Channel Impairments](https://wiki.gnuradio.org/index.php?title=QPSK_Mod_and_Demod#Channel_Impairments "QPSK Mod and Demod"). 
## Recovering Timing
Recovering timing is the same as described in the QPSK tutorial section [Symbol Sync](https://wiki.gnuradio.org/index.php?title=QPSK_Mod_and_Demod#Symbol_Sync "QPSK Mod and Demod"). 
## Equalizers
An equalizer has been left out of this tutorial to simplify the final flowgraph. 
## Phase and Fine Frequency Correction
Phase and Fine Frequency Correction is the same as described in the QPSK tutorial section [Phase and Frequency Correction](https://wiki.gnuradio.org/index.php?title=QPSK_Mod_and_Demod#Phase_and_Frequency_Correction "QPSK Mod and Demod"). 
Here is the BPSK output screen: 
[![](https://wiki.gnuradio.org/images/thumb/0/0e/Bpsk_stage5_out.png/655px-Bpsk_stage5_out.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_stage5_out.png)
## Decoding
Now that the hard part is done, we get to decode the signal. Using the [Media:Bpsk_stage6_ver2.grc](https://wiki.gnuradio.org/images/0/05/Bpsk_stage6_ver2.grc "Bpsk stage6 ver2.grc") example flowgraph below, we insert a [Constellation Decoder](https://wiki.gnuradio.org/index.php?title=Constellation_Decoder "Constellation Decoder") after the Costas loop, but our work is not quite done. At this point, we get our symbols 0 and 1 because this is the size of our alphabet in a BPSK scheme. But how do we know for sure that we have the same mapping of symbols to constellation points that we did when we transmitted? Notice in our discussion above that nothing we did had any knowledge of the transmitted symbol-to-constellation mapping, which means we might have an ambiguity of 180 degrees in the constellation. Luckily, we avoided this problem by transmitting [_differential_ symbols](https://en.wikipedia.org/wiki/Differential_coding). We didn't actually transmit the constellation itself, we transmitted the difference between symbols of the constellation by setting the Differential setting in the Constellation Modulator block to 'Yes'. So now we undo that. 
[![](https://wiki.gnuradio.org/images/thumb/5/5a/Bpsk_stage6_ver2_fg.jpg/800px-Bpsk_stage6_ver2_fg.jpg)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_stage6_ver2_fg.jpg)
The flowgraph uses the [Differential Decoder](https://wiki.gnuradio.org/index.php?title=Differential_Decoder "Differential Decoder") block to translate the differential coded symbols back to their original symbols based on the phase transitions, not the absolute phase itself. Now we have the original bit stream! 
But how do we know that it's the original bit stream? To verify that, we'll compare it to the input bit stream, which we can do because this is a simulation and we have access to the transmitted data. But of course, the transmitter produced _packed bits_ , so we use the unpack bit block to unpack from 8-bits per byte to 1-bit per byte. We then convert these streams to floating point values of 0.0 and 1.0 simply because our time sinks only accept float and complex values. Comparing these two directly would show us... nothing. Why? Because the receiver chain has many blocks and filters that delay the signal, so the received signal is some number of bits behind. To compensate, we have to delay the transmitted bits by the same amount using the [Delay](https://wiki.gnuradio.org/index.php?title=Delay "Delay") block. Then you can adjust the delay to find the correct value and see how the bits synchronize. Also you can subtract one signal from the other to see when they are synchronized because the output will be 0. Adding noise and other channel affects then can be seen easily as bit errors whenever this signal is not 0. 
[![](https://wiki.gnuradio.org/images/thumb/0/01/Bpsk_stage6_ver2-out.png/800px-Bpsk_stage6_ver2-out.png)](https://wiki.gnuradio.org/index.php?title=File:Bpsk_stage6_ver2-out.png)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation&oldid=15478](https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation&oldid=15478)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Guided Tutorials](https://wiki.gnuradio.org/index.php?title=Category:Guided_Tutorials "Category:Guided Tutorials")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Simulation+example%3A+BPSK+Demodulation "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Simulation_example:_BPSK_Demodulation "Discussion about the content page \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation)
  * [View source](https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Simulation_example:_BPSK_Demodulation "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Simulation_example:_BPSK_Demodulation "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation&oldid=15478 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation&action=info "More information about this page")


  * This page was last edited on 29 November 2025, at 03:25.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


