# Frequency Shifting
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting#searchInput)  
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
  3. [Sample Rate Change](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change "Sample Rate Change")
  4. Frequency Shifting
  5. [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files")

SDR Hardware 
  1. [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver")
  2. [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver")
  3. [E310 FM Receiver](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver "E310 FM Receiver")

 |  
| --- |  
This tutorial describes how to perform frequency shifting, causing the frequency of a signal to change. 
Frequency shifting is useful in several scenarios, including: 
  * avoiding the LO feedthrough signal present on many SDRs
  * tuning within an IQ file, where its not possible to tune using a hardware-based tuner
  * tuning at frequency resolutions that are not possible with the available hardware-based tuners


The previous tutorial, [Sample Rate Change](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change "Sample Rate Change"), describes how to both increase and decrease the sampling rate. The next tutorial, [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files"), describes how to read and write radio waveform captures as binary files. 
* * *
## Contents
  * [1 Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting#Frequency_Shifting)
  * [2 Build Example Signal](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting#Build_Example_Signal)
  * [3 Create Complex Sinusoid](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting#Create_Complex_Sinusoid)
  * [4 Perform Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting#Perform_Frequency_Shifting)


## Frequency Shifting
Frequency shifting is the process of changing the position of a signal within the frequency domain. Equivalently, it can be stated that frequency shifting is the process of changing the center frequency of a signal. Frequency shifting can be implemented many different ways, although this tutorial will focus on the simple method of multiplication by a complex sinusoid. 
Multiplying a signal by a complex sinusoid with frequency _f_ Hz will translate or shift the center frequency of the signal by _f_ Hz. For example, to frequency shift a signal by 1 MHz a complex signal must be generated with frequency 1 MHz, and then multiplied against the desired signal in order to frequency shift it. 
## Build Example Signal
First an example signal needs to be built. A simple signal of filtered noise is created. Drag in the following blocks and connect them: 
  * Noise Source
  * Throttle
  * Low Pass Filter
  * QT GUI Frequency Sink


[![](https://wiki.gnuradio.org/images/f/f5/Frequency_shifting_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_flowgraph.png)
  
The flowgraph is an offline simulation to the choice of sampling rate is somewhat arbitrary, but a sample rate of 10 MHz is chosen to realistic number. 
[![](https://wiki.gnuradio.org/images/6/61/Frequency_shifting_samp_rate_properties.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_samp_rate_properties.png)
  
The low-pass filter properties are updated to define the cutoff frequency and transition width: 
  * Cutoff Freq: _samp_rate/8_
  * Transition Width: _samp_rate/16_


[![](https://wiki.gnuradio.org/images/e/ee/Frequency_shifting_low_pass_properties.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_low_pass_properties.png)
  
Running the flowgraph then displays a simulated signal: 
[![](https://wiki.gnuradio.org/images/e/ef/Frequency_shifting_example_signal.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_example_signal.png)
  
The signal has not been shifted yet and therefore has a center frequency of 0 Hz. 
## Create Complex Sinusoid
A complex sinusoid is added to the flowgraph which will be used later to perform the frequency shifting. Add the following blocks and connect them to the flowgraph: 
  * Variable
  * Signal Source


[![](https://wiki.gnuradio.org/images/b/b6/Frequency_shifting_flowgraph_sinusoid.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_flowgraph_sinusoid.png)
  
The flowgraph is an offline simulation to the choice of sampling rate is somewhat arbitrary, but a sample rate of 10 MHz is chosen to be a realistic number. 
[![](https://wiki.gnuradio.org/images/7/70/Frequency_shifting_new_center_frequency_properties.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_new_center_frequency_properties.png)
  
The variable _new_center_frequency_ is then used for the frequency in the signal source block: 
[![](https://wiki.gnuradio.org/images/5/50/Frequency_shifting_signal_source_properties.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_signal_source_properties.png)
  
Increase the number of ports on the **QT GUI Frequency Sink** : 
[![](https://wiki.gnuradio.org/images/9/93/Frequency_shifting_frequency_sink_properties.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_frequency_sink_properties.png)
  
Now run the flowgraph: 
[![](https://wiki.gnuradio.org/images/7/7a/Frequency_shifting_run_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_run_flowgraph.png)
  
You can now see the new complex sinusoid that has been created and displayed in **red** : 
[![](https://wiki.gnuradio.org/images/e/e7/Frequency_shifting_signal_and_complex_sinusoid.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_signal_and_complex_sinusoid.png)
  
The frequency shifting process will apply the blue signal against the **red** complex sinusoid, centering it at 1 MHz. 
## Perform Frequency Shifting
Add the **Multiply** block into the flowgraph and connect it such that it accepts the outputs from **Low Pass Filter** and **Signal Source** : 
[![](https://wiki.gnuradio.org/images/3/32/Frequency_shifting_flowgraph_multiply.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_flowgraph_multiply.png)
  
The multiply block now performs the frequency shifting, moving the center frequency of the signal up to 1 MHz. Running the flowgraph shows the input signal centered at 0 Hz and the frequency shifted version at 1 MHz: 
[![](https://wiki.gnuradio.org/images/3/3b/Frequency_shifting_centered_1MHz.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_centered_1MHz.png)
  
The frequency shifted value can be positive or negative. Update the _new_center_frequency_ variable to be -3 MHz: 
[![](https://wiki.gnuradio.org/images/7/75/Frequency_shifting_update_new_center_frequency.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_update_new_center_frequency.png)
  
Running the flowgraph now shows the signal centered at -3 MHz: 
[![](https://wiki.gnuradio.org/images/1/13/Frequency_shifting_neg_3MHz.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_neg_3MHz.png)
  
A **QT GUI Range** block can be used to change the center frequency in real time. Right click on the _new_center_frequency_ and select _Disable_ : 
[![](https://wiki.gnuradio.org/images/c/c7/Frequency_shifting_disable_variable_block.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_disable_variable_block.png)
  
The block will now be grayed out. 
Add a **QT GUI Range** block to the flowgraph: 
[![](https://wiki.gnuradio.org/images/f/f5/Frequency_shifting_qt_gui_range_block.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_qt_gui_range_block.png)
  
Open the QT GUI Range block and update the following properties: 
  * ID: _new_center_frequency_
  * Default Value: _0_
  * Start: _-samp_rate/2_
  * Stop: _samp_rate/2_


[![](https://wiki.gnuradio.org/images/c/c8/Frequency_shifting_qt_gui_range_properties.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_qt_gui_range_properties.png)
  
Save the properties and run the flowgraph. The QT pop up window will now display a slider bar at the top which can be clicked and slid around to change the frequency and therefore move the frequency shifted signal. The text box can also be modified to set a specific center frequency: 
[![](https://wiki.gnuradio.org/images/1/15/Frequency_shifting_slider_bar.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency_shifting_slider_bar.png)
The next tutorial, [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files"), describes how to read and write radio waveform captures as binary files. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Frequency_Shifting&oldid=14504](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting&oldid=14504)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Frequency+Shifting "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Frequency_Shifting&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting)
  * [View source](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Frequency_Shifting "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Frequency_Shifting "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting&oldid=14504 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting&action=info "More information about this page")


  * This page was last edited on 14 July 2024, at 13:41.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


