# What Is GNU Radio
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio#searchInput)  
|  **Beginner Tutorials** Introducing GNU Radio 
  1. What is GNU Radio?
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
  3. [Sample Rate Change](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change "Sample Rate Change")
  4. [Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "Frequency Shifting")
  5. [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files")

SDR Hardware 
  1. [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver")
  2. [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver")
  3. [E310 FM Receiver](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver "E310 FM Receiver")

 |  
| --- |  
## Contents
  * [1 What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio#What_is_GNU_Radio?)
  * [2 Why would I want GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio#Why_would_I_want_GNU_Radio?)
  * [3 Digital Signal Processing](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio#Digital_Signal_Processing)
    * [3.1 A little signal theory](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio#A_little_signal_theory)
    * [3.2 Applying Digital Signal Processing to Radio Transmissions](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio#Applying_Digital_Signal_Processing_to_Radio_Transmissions)
  * [4 A modular, flowgraph based Approach to Digital Signal Processing](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio#A_modular,_flowgraph_based_Approach_to_Digital_Signal_Processing)


## What is GNU Radio?
[![](https://wiki.gnuradio.org/images/e/e1/Gnuradio_logo_glyphs_as_paths.png)](https://wiki.gnuradio.org/index.php?title=File:Gnuradio_logo_glyphs_as_paths.png)
GNU Radio is a free & open-source software development toolkit that provides signal processing blocks to implement software radios. It can be used with readily-available low-cost external RF hardware to create software-defined radios, or without hardware in a simulation-like environment. It is widely used in research, industry, academia, government, and hobbyist environments to support both wireless communications research and real-world radio systems. 
Below shows an example flowgraph within the GNU Radio Companion visual editor: 
[![](https://wiki.gnuradio.org/images/thumb/8/8d/FunCube_2_meter_NB_FM_fg.png/750px-FunCube_2_meter_NB_FM_fg.png)](https://wiki.gnuradio.org/index.php?title=File:FunCube_2_meter_NB_FM_fg.png)  

GNU Radio is a framework that enables users to design, simulate, and deploy highly capable real-world radio systems. It is a highly modular, "flowgraph"-oriented framework that comes with a comprehensive library of processing blocks that can be readily combined to make complex signal processing applications. GNU Radio has been used for a huge array of real-world radio applications, including audio processing, mobile communications, tracking satellites, radar systems, GSM networks, Digital Radio Mondiale, and much more - all in computer software. It is, by itself, not a solution to talk to any specific hardware. Nor does it provide out-of-the-box applications for specific radio communications standards (e.g., 802.11, ZigBee, LTE, etc.,), but it can be (and has been) used to develop implementations of basically any band-limited communication standard. 
## Why would I want GNU Radio?
Formerly, when developing radio communication devices, the engineer had to develop a specific circuit for detection of a specific signal class, design a specific integrated circuit that would be able to decode or encode that particular transmission and debug these using costly equipment. 
Software-Defined Radio (SDR) takes the analog signal processing and moves it, as far as physically and economically feasible, to processing the radio signal on a computer using algorithms in software. 
You can, of course, use your computer-connected radio device in a program you write from scratch, concatenating algorithms as you need them and moving data in and out yourself. But this quickly becomes cumbersome: Why are you re-implementing a standard filter? Why do you have to care how data moves between different processing blocks? Wouldn't it be better to use highly optimized and peer-reviewed implementations rather than writing things yourself? And how do you get your program to scale well on a multi-core architectures but also run well on an embedded device consuming but a few watts of power? Do you really want to write all the GUIs yourself? 
Enter GNU Radio: A framework dedicated to writing signal processing applications for commodity computers. GNU Radio wraps functionality in easy-to-use reusable blocks, offers excellent scalability, provides an extensive library of standard algorithms, and is heavily optimized for a large variety of common platforms. It also comes with a large set of examples to get you started. 
The remainder of this page provides a brief intro to DSP, feel free to skip to the next tutorial if you are already familiar with DSP. 
## Digital Signal Processing
As a software framework, GNU Radio works on digitized signals to generate communication functionality using general-purpose computers. 
### A little signal theory
Doing signal processing in software requires the signal to be digital. But what is a digital signal? 
To understand better, let's look at a common "signal" scenario: Recording voice for transmission using a cellphone. 
A person physically speaking creates a sound _signal_ - the signal, in this case, is comprised of waves of varying air pressure being generated by the vocal chords of a human. A signal is a time-varying physical quantity, like the air pressure. 
[![sound_vocal.png](https://wiki.gnuradio.org/images/4/47/Sound_vocal.png)](https://wiki.gnuradio.org/index.php?title=File:Sound_vocal.png "sound_vocal.png")
When the waves reach the microphone, it converts the varying pressure into an electrical signal, a variable voltage: 
[![p_to_u.png](https://wiki.gnuradio.org/images/6/63/P_to_u.png)](https://wiki.gnuradio.org/index.php?title=File:P_to_u.png "p_to_u.png")
Now that the signal is electrical, we can work with it. The audio signal, at this point, is _analog_ – a computer can't yet deal with it; for computational processing, a signal has to be _digital_ , which means two things:  

It can only be one of a limited number of values.
    The signal can vary over time, but for every instant, it only takes one value – and that value isn't from some "continuum" (like [−1.5;+1.5], but from some finite set (like {−1.50,−1.49,…,+1.49,+1.50}). 

It only exists for a discrete set of points in time
    The signal isn't defined for just any point in time – the points in time are separate, and countable. You can say "this is the first point in time for which the signal takes a specific value, this is the second point in time…".
[![cont_to_digital.png](https://wiki.gnuradio.org/images/e/e2/Cont_to_digital.png)](https://wiki.gnuradio.org/index.php?title=File:Cont_to_digital.png "cont_to_digital.png")
This _digital signal_ can thus be represented by a sequence of numbers, called _samples_. A fixed time interval between samples leads to a signal _sampling rate_. 
The process of taking a physical quantity (voltage) and converting it to digital samples is done by an _Analog-to-Digital Converter_ (ADC). The complementary device, a _Digital-to-Analog Converter_ (DAC), takes numbers from a digital computer and converts them to an analog signal. 
Now that we have a sequence of numbers, our computer can do anything with it. It might, for example, apply digital filters, compress it, recognize speech, or transmit the signal using a digital link. 
### Applying Digital Signal Processing to Radio Transmissions
The same principles as for sounds can be applied to radio waves: 
A signal, here electromagnetic waves, can be converted into a varying voltage using an antenna. 
[![antenna.png](https://wiki.gnuradio.org/images/1/13/Antenna.png)](https://wiki.gnuradio.org/index.php?title=File:Antenna.png "antenna.png")
This electrical signal is then on a _carrier frequency_ , which is usually several Mega- or even Gigahertz. 
Different types of receivers (e.g. Superheterodyne Receiver, Direct Conversion, Low Intermediate Frequency Receivers), which can be acquired commercially as dedicated software radio peripherals, are already available to users (e.g. amateur radio receivers connected to sound cards) or can be obtained when re-purposing cheaply available consumer digital TV receivers (the notorious [RTL-SDR](https://www.rtl-sdr.com/about-rtl-sdr/) project). 
## A modular, flowgraph based Approach to Digital Signal Processing
To process digital signals, it is straight-forward to think of the individual processing stages (filtering, correction, analysis, detection...) as processing blocks, which can be connected using simple flow-indicating arrows: 
[![twoblocks_arrow.png](https://wiki.gnuradio.org/images/c/c5/Twoblocks_arrow.png)](https://wiki.gnuradio.org/index.php?title=File:Twoblocks_arrow.png "twoblocks_arrow.png")
When building a signal processing application, one will build up a complete graph of blocks. Such a graph is called flowgraph in GNU Radio. 
[![example_flowgraph.png](https://wiki.gnuradio.org/images/b/b3/Example_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Example_flowgraph.png "example_flowgraph.png")
GNU Radio is a framework to develop these processing blocks and create flowgraphs, which comprise radio processing applications. 
As a GNU Radio user, you can combine existing blocks into a high-level flowgraph that does something as complex as receiving digitally modulated signals and GNU Radio will automatically move the signal data between these and cause processing of the data when it is ready for processing. 
GNU Radio comes with a large set of existing blocks. An index to all of them can be found in [Block Docs](https://wiki.gnuradio.org/index.php/Category:Block_Docs). Just to give you but a small excerpt of what's available in a standard installation, here's some of the most popular block categories and a few of their members: 
  * Waveform Generators Expand
    * Constant Source
    * Noise Source
    * Signal Source (e.g. Sine, Square, Saw Tooth)


  * Modulators Expand
    * AM Demod
    * Continuous Phase Modulation
    * PSK Mod / Demod
    * GFSK Mod / Demod
    * GMSK Mod / Demod
    * QAM Mod / Demod
    * WBFM Receive
    * NBFM Receive


  * Instrumentation (i.e., GUIs) Expand
    * Constellation Sink
    * Frequency Sink
    * Histogram Sink
    * Number Sink
    * Time Raster Sink
    * Time Sink
    * Waterfall Sink


  * Math Operators Expand
    * Abs
    * Add
    * Complex Conjugate
    * Divide
    * Integrate
    * Log10
    * Multiply
    * RMS
    * Subtract


  * Channel Models Expand
    * Channel Model
    * Fading Model
    * Dynamic Channel Model
    * Frequency Selective Fading Model


  * Filters Expand
    * Band Pass / Reject Filter
    * Low / High Pass Filter
    * IIR Filter
    * Generic Filterbank
    * Hilbert
    * Decimating FIR Filter
    * Root Raised Cosine Filter
    * FFT Filter


  * Fourier Analysis Expand
    * FFT
    * Log Power FFT
    * Goertzel (Resamplers)
    * Fractional Resampler
    * Polyphase Arbitrary Resampler
    * Rational Resampler (Synchronizers)
    * Clock Recovery MM
    * Correlate and Sync
    * Costas Loop
    * FLL Band-Edge
    * PLL Freq Det
    * PN Correlator
    * Polyphase Clock Sync


Using these blocks, many standard tasks, like normalizing signals, synchronization, measurements, and visualization can be done by just connecting the appropriate block to your signal processing flow graph. 
Also, you can write your own blocks, that either combine existing blocks with some intelligence to provide new functionality together with some logic, or you can develop your own block that operates on the input data and outputs data. 
Thus, GNU Radio is mainly a framework for the development of signal processing blocks and their interaction. It comes with an extensive standard library of blocks, and there are a lot of systems available that a developer might build upon. However, GNU Radio itself is not software that is ready to do something specific -- it's the user's job to build something useful out of it, though it already comes with a lot of useful working examples. Think of it as a set of building blocks. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio&oldid=12690](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio&oldid=12690)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=What+Is+GNU+Radio "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:What_Is_GNU_Radio&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio)
  * [View source](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/What_Is_GNU_Radio "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/What_Is_GNU_Radio "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio&oldid=12690 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio&action=info "More information about this page")
  * [Expand all](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "Expand all collapsible elements on the current page")


  * This page was last edited on 4 December 2022, at 13:17.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


