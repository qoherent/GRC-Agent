# Sample Rate Change
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change#searchInput)  
|  **Beginner Tutorials** Introducing GNU Radio 
  1. [What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "What Is GNU Radio")
  2. [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR")
  3. [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph")

Flowgraph Fundamentals 
  1. [Python Variables in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "Python Variables in GRC")
  2. [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs")
  3. [Runtime Updating Variables](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables "Runtime Updating Variables")
  4. [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types")
  5. [Converting Data Types](https://wiki.gnuradio.org/index.php?title=Converting_Data_Types "Converting Data Types")
  6. [Packing Bits](https://wiki.gnuradio.org/index.php?title=Packing_Bits "Packing Bits")
  7. [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors")
  8. [Hier Blocks and Parameters](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters "Hier Blocks and Parameters")

Creating and Modifying Python Blocks 
  1. [Creating Your First Block](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block "Creating Your First Block")
  2. [Python Block With Vectors](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors "Python Block with Vectors")
  3. [Python Block Message Passing](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing "Python Block Message Passing")
  4. [Python Block Tags](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags "Python Block Tags")

DSP Blocks 
  1. [Low Pass Filter Example](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example "Low Pass Filter Example")
  2. [Designing Filter Taps](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps "Designing Filter Taps")
  3. Sample Rate Change
  4. [Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "Frequency Shifting")
  5. [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files")

SDR Hardware 
  1. [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver")
  2. [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver")
  3. [E310 FM Receiver](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver "E310 FM Receiver")

 |  
| --- |  
This tutorial describes how to implement sample rate change within GNU Radio. 
The previous tutorial, [Designing Filter Taps](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps "Designing Filter Taps"), demonstrates how to design filter taps and use them in signal processing blocks. Please complete the [Designing Filter Taps](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps "Designing Filter Taps") tutorial before completing this one. The next tutorial, [Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "Frequency Shifting"), describes how to apply a frequency shift to a signal both mathematically and with DSP blocks. 
## Interpolation
Interpolation is the process of increasing the sampling rate and thus the available bandwidth. This example demonstrates how to increase the sampling rate using the _Interpolating FIR Filter_ block. 
Start by adding the following blocks to the flowgraph and connect them: 
  1. Two _Variable_ blocks
  2. _Low-Pass Filter Taps_
  3. _QT GUI Range_
  4. _Signal Source_
  5. _Interpolating FIR Filter_
  6. _Throttle_
  7. _QT GUI Frequency Sink_


[![](https://wiki.gnuradio.org/images/thumb/5/51/InterpolationFlowgraphStart.png/700px-InterpolationFlowgraphStart.png)](https://wiki.gnuradio.org/index.php?title=File:InterpolationFlowgraphStart.png)
  
Edit the first of the two new variable blocks: 
  * Id: _interpolation_rate_
  * Value: _4_


Edit the second of the two new variable blocks: 
  * Id: _samp_rate_interpolated_
  * Value: _samp_rate*interpolation_rate_


Edit the properties of the _Low-Pass Filter Taps_ block: 
  * Id: _lowPassTaps_
  * Sample Rate (Hz): _samp_rate_interpolated_
  * Cutoff Freq (Hz): _samp_rate_interpolated/(interpolation_rate*2)_
  * Transition Width (Hz): _samp_rate_interpolated/(interpolation_rate*4)_


[![](https://wiki.gnuradio.org/images/thumb/e/e7/EditLowPassTapsProperties.png/500px-EditLowPassTapsProperties.png)](https://wiki.gnuradio.org/index.php?title=File:EditLowPassTapsProperties.png)
  
Edit the properties of the _QT GUI Range_ block: 
  * Id: _frequency_
  * Default Value: _0_
  * Start: _-samp_rate/2_
  * Stop: _samp_rate/2_


Edit the property of the _Signal Source_ : 
  * Frequency: _frequency_


Edit the properties of the _Interpolating FIR Filter_ block: 
  * Interpolation: _interpolation_rate_
  * Taps: _lowPassTaps_


[![](https://wiki.gnuradio.org/images/thumb/8/85/InterpolatingFIRFilterProperties.png/500px-InterpolatingFIRFilterProperties.png)](https://wiki.gnuradio.org/index.php?title=File:InterpolatingFIRFilterProperties.png)
  
The _Interpolating FIR Filter_ increases the sampling rate from 32 kHz to 128 kHz, a factor of 4 due to the _interpolation_rate_ variable. Make a note of this by editing the _Comment_ field under the _Advanced_ tab: 
[![](https://wiki.gnuradio.org/images/thumb/2/2a/AddCommentToBlock.png/500px-AddCommentToBlock.png)](https://wiki.gnuradio.org/index.php?title=File:AddCommentToBlock.png)
  
The comment is then displayed as a visual reminder in GRC: 
[![](https://wiki.gnuradio.org/images/thumb/c/c6/SampleRateBlockComment.png/700px-SampleRateBlockComment.png)](https://wiki.gnuradio.org/index.php?title=File:SampleRateBlockComment.png)
  
Edit the _Throttle_ property: 
  * Sample Rate: _samp_rate_interpolated_


Edit the _QT GUI Frequency Sink_ property: 
  * Bandwidth (Hz): _samp_rate_interpolated_


The flowgraph looks like the following: 
[![](https://wiki.gnuradio.org/images/thumb/7/71/InterpolationFinalFlowgraph.png/700px-InterpolationFinalFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:InterpolationFinalFlowgraph.png)
  
Running the flowgraph shows the following _QT GUI Frequency Sink_ : 
[![](https://wiki.gnuradio.org/images/thumb/5/57/RunInterpolationFlowgraph.png/500px-RunInterpolationFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:RunInterpolationFlowgraph.png)
  
The four peaks come from the interpolation operation. Scroll-wheel-click on the window and enable _Max Hold_ : 
[![](https://wiki.gnuradio.org/images/thumb/4/48/InterpolationClickMaxHold.png/500px-InterpolationClickMaxHold.png)](https://wiki.gnuradio.org/index.php?title=File:InterpolationClickMaxHold.png)
  
Drag the frequency slider to show how the four peaks change in frequency, creating an outline of the frequency response of the _Interpolating FIR Filter_ block. The interpolation has increased the sampling rate by a factor of 4, with the low-pass filter taps attenuating the spectral images to minimize distortion. 
[![](https://wiki.gnuradio.org/images/thumb/9/9b/InterpolationMaxHoldOutline.png/500px-InterpolationMaxHoldOutline.png)](https://wiki.gnuradio.org/index.php?title=File:InterpolationMaxHoldOutline.png)
## Decimation (Source hardware example)
Where interpolation increases the sample rate, decimation decreases the sample rate and available bandwidth. 
The following discussion is based on the flowgraph of a RadioTeleTYpe (RTTY) receiver. It can be found at [RTTY_receive.grc](https://raw.githubusercontent.com/duggabe/gr-RTTY-basics/master/RTTY_rcv/RTTY_receive.grc)
[![](https://wiki.gnuradio.org/images/thumb/8/8e/RTTY_rcv.png/800px-RTTY_rcv.png)](https://wiki.gnuradio.org/index.php?title=File:RTTY_rcv.png)
Frequency shift keying (FSK) tones are input to the microphone jack of the computer which has a sample rate of 48 kHz. That data is fed to a [Frequency Xlating FIR Filter](https://wiki.gnuradio.org/index.php?title=Frequency_Xlating_FIR_Filter "Frequency Xlating FIR Filter") which shifts the tones above and below the center frequency. It also decimates (divides) the sample rate by 50, producing an output sample rate of 960. 
The [Quadrature Demod](https://wiki.gnuradio.org/index.php?title=Quadrature_Demod "Quadrature Demod") produces a signal which is positive or negative depending on whether the tone is above or below the center frequency. 
The RTTY symbol time is, by definition, exactly 22 ms. yielding the familiar 45 baud (1/0.022 rounded). To get an integer number of samples per symbol, a sample rate of 500 was chosen, producing 11 samples per symbol time. (500 samples/sec * 0.022 seconds = 11 samples). 
The output of the Quadrature Demod block has a sample rate of 960; the desired sample rate is 500. The [Rational Resampler](https://wiki.gnuradio.org/index.php?title=Rational_Resampler "Rational Resampler") interpolates (multiplies) the sample rate by 500 and decimates (divides) it by 960 to produce an output sample rate of 500. 
The [Binary Slicer](https://wiki.gnuradio.org/index.php?title=Binary_Slicer "Binary Slicer") produces an output of +1 for inputs greater than zero, and 0 for inputs less than zero. 
The 'Terminal Display Sink' is an [Embedded Python Block](https://wiki.gnuradio.org/index.php?title=Embedded_Python_Block "Embedded Python Block") which reads the input stream of 1's and 0's, synchronizes on the start bit, creates a Baudot character from the five data bits, converts Baudot to UTF-8, and outputs the characters to a [ZMQ PUSH Message Sink](https://wiki.gnuradio.org/index.php?title=ZMQ_PUSH_Message_Sink "ZMQ PUSH Message Sink"). 
The [gr-webserver](https://github.com/duggabe/) package can receive the messages from the message sink and display them on a browser screen. 
The next tutorial, [Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "Frequency Shifting"), describes how to apply a frequency shift to a signal both mathematically and with DSP blocks. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change&oldid=14442](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change&oldid=14442)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Sample+Rate+Change "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Sample_Rate_Change&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change)
  * [View source](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Sample_Rate_Change "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Sample_Rate_Change "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change&oldid=14442 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change&action=info "More information about this page")


  * This page was last edited on 12 June 2024, at 22:16.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


