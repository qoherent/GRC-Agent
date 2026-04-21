# B200-B205mini FM Receiver
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver#searchInput)  
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
  4. [Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "Frequency Shifting")
  5. [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files")

SDR Hardware 
  1. [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver")
  2. B200-B205mini FM Receiver
  3. [E310 FM Receiver](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver "E310 FM Receiver")

 |  
| --- |  
This tutorial describes how to receive broadcast commercial radio stations transmitting Frequency Modulated (FM) signals using the Ettus Research B200/B205 mini receiver. 
The previous tutorial, [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver"), describes how to build a broadcast FM receiver using an RTL-SDR receiver. 
The following hardware is needed for this tutorial: 
  * B200/B205mini Receiver
  * VHF Antenna


It is likely (but not guaranteed) that the Ettus Research B210 will also work for this tutorial with no other modifications. 
Please connect the antenna to the B200/B205mini, and plug the B200/B205 mini into the USB port on your computer. 
## Contents
  * [1 Start a New Flowgraph](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver#Start_a_New_Flowgraph)
  * [2 Configure the USRP](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver#Configure_the_USRP)
  * [3 Add Time & Frequency Plots](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver#Add_Time_&_Frequency_Plots)
  * [4 FM Demodulator](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver#FM_Demodulator)
  * [5 Diagnosing Overrun Problems](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver#Diagnosing_Overrun_Problems)


## Start a New Flowgraph
For this, you can reference [the "Your First Flowgraph" tutorial](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph"). This will step you through starting Gnu Radio Companion for the first time. If you already have opened Gnu Radio Companion and have created flowgraphs, in this case, you can select "File -> New -> QT GUI" (the default selection) to start a new flowgraph. There's also an icon in the upper, left that will do the same thing. Once you've opened this new flowgraph, follow the steps in the "Your First Flowgraph" link to setup the "Options" block and save the file. 
## Configure the USRP
Start by dragging in the **UHD: USRP Source** block into the flowgraph. UHD is a library for communicating with the family of USRP radio receivers and the USRP Source simplifies and abstracts communication with all of the USRP radio receivers using a single block. 
[![](https://wiki.gnuradio.org/images/3/31/B200mini_FM_Receiver_add_usrp_block.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_add_usrp_block.png)
Open the properties for the **UHD: USRP Source** block. In the _Device Address_ window, enter _type=b200_. Change the _Sync_ to _PC Clock_. 
[![](https://wiki.gnuradio.org/images/thumb/6/6d/UHD-USRP-source-properties-annotated.jpg/500px-UHD-USRP-source-properties-annotated.jpg)](https://wiki.gnuradio.org/index.php?title=File:UHD-USRP-source-properties-annotated.jpg)
NOTE: This block will most likely work even without these changes; however, with these modifications, you're less likely to see overflows. 
Navigate to _RF Options_ and then enter _freq_ as the Center Frequency. Enable AGC. 
[![](https://wiki.gnuradio.org/images/8/85/B200mini_FM_Receiver_usrp_freq.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_usrp_freq.png)
Next, change the _samp_rate_ to 2 MHz, which will be the sampling rate for the USRP. 
[![](https://wiki.gnuradio.org/images/5/5b/B200mini_FM_Receiver_change_samp_rate.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_change_samp_rate.png)
This tutorial uses the frequency allocations within the United State of America, therefore you will need to modify them according to the allocation for your country. Within the USA, the smallest frequency of a radio station is 87.9 MHz and the largest frequency is 107.9 MHz [[1]](https://en.wikipedia.org/wiki/FM_broadcasting_in_the_United_States), and each channel is separated by 200 kHz. A **QT GUI Range** block will be used to define different radio station frequencies for the USRP. Drag in a **QT GUI Range** block, open the properties and give it the name freq and the start frequency of 87.9 MHz, the stop frequency of 107.9 MHz and a step of 200 kHz: 
[![](https://wiki.gnuradio.org/images/b/b1/B200mini_FM_Receiver_qt_gui_range_freq.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_qt_gui_range_freq.png)
The flowgraph should now look like the following. Note that the USRP block displays a property in the device address of _type=b200_ , a sync of _PC Clock_ , an updated sampling rate of 2 MHz, a center frequency of 87.9 MHz, and an enabled AGC: 
[![](https://wiki.gnuradio.org/images/thumb/4/47/UHD-USRP-Source-flowgraph-block-annotated.jpg/500px-UHD-USRP-Source-flowgraph-block-annotated.jpg)](https://wiki.gnuradio.org/index.php?title=File:UHD-USRP-Source-flowgraph-block-annotated.jpg)
## Add Time & Frequency Plots
Drag and drop a **QT GUI Time Sink** and **QT GUI Frequency Sink** block into the flowgraph. Open the properties of the **QT GUI Time Sink** and enable autoscaling: 
[![](https://wiki.gnuradio.org/images/9/93/B200mini_FM_Receiver_time_sink_autoscale.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_time_sink_autoscale.png)
Open the properties of the **QT GUI Freq Sink** and enter _freq_ as the Center Frequency: 
[![](https://wiki.gnuradio.org/images/7/79/B200mini_FM_Receiver_freq_sink_freq.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_freq_sink_freq.png)
The flowgraph should now look like the following: 
[![](https://wiki.gnuradio.org/images/8/8b/B200mini_FM_Receiver_basic_scanner_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_basic_scanner_flowgraph.png)
Run the flowgraph. A display window will show the time domain and frequency domain plots. The spectrum can be scanned by sliding the bar at the top of the screen or by entering a frequency manually. 
[![](https://wiki.gnuradio.org/images/thumb/e/ef/B200mini_FM_Receiver_basic_scanner_output.png/500px-B200mini_FM_Receiver_basic_scanner_output.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_basic_scanner_output.png)
  

## FM Demodulator
Drag in an **Audio Sink** block. Open the properties. Note there are only a couple of options to select for the audio sampling rate. Select 48 kHz. 
[![](https://wiki.gnuradio.org/images/3/37/B200mini_FM_Receiver_audio_sink_samp_rate.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_audio_sink_samp_rate.png)
  
The flowgraph should now look like the following: 
[![](https://wiki.gnuradio.org/images/f/f8/B200mini_FM_Receiver_flowgraph_audio_sink.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_flowgraph_audio_sink.png)
  
The question is how to get from the output of the USRP which is complex IQ sampled at 2 MHz to the input of the **Audio Sink** block which requires real samples at a sampling rate of 48 kHz? The rest of this tutorial will work backwards from the **Audio Sink** and establishing blocks and connections towards the output of the USRP. 
The next block that is needed is the an FM demodulator. Drag in the **WBFM Receive** block, which takes complex IQ as an input, demodulates the FM thereby producing real output samples and also performs a decimation. 
[![](https://wiki.gnuradio.org/images/f/fa/B200mini_FM_Receiver_flowgraph_wbfm_receive.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_flowgraph_wbfm_receive.png)
  
Recall from earlier in the tutorial that FM broadcast channels allocated 200 kHz, therefore we want to process as much of that bandwidth as possible with the FM demodulator. The **WBFM Receive** block can perform a decimation from a larger input sampling rate to the required Audio Sink input of 48 kHz. The decimation factor must be an integer, and 4*48 kHz = 192 kHz which is close to the total bandwidth of the frequency allocation. 
Open the **WBFM Receive** properties and enter in the quadrature rate of 192 kHz and an audio decimation of 4. Note that the quadrature rate must be evenly divisible by the audio decimation factor, and that the audio decimation must be an integer. 
[![](https://wiki.gnuradio.org/images/3/36/B200mini_FM_Receiver_wbfm_receive_properties.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_wbfm_receive_properties.png)
  
The flowgraph should now look like the following: 
[![](https://wiki.gnuradio.org/images/a/a8/B200mini_FM_Receiver_flowgraph_wbfm_set_properties.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_flowgraph_wbfm_set_properties.png)
  
A sample rate change is needed to convert from the USRP output of 2 MHz to the WBFM input of 192 kHz. The required sampling rate change can be simplified as 192000/2000000 = 192/2000 = 12/125, a rational ratio. Therefore the **Rational Resampler** block can be used to implement the sample rate change. 
Drag in the **Rational Resampler** block and open the properties. Enter 12 for the interpolation and 125 for the decimation: 
[![](https://wiki.gnuradio.org/images/c/cc/B200mini_FM_Receiver_rational_resampler_properties.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_rational_resampler_properties.png)
  
The flowgraph is now complete and should look like the following. 
[![](https://wiki.gnuradio.org/images/d/da/B200mini_FM_Receiver_flowgraph_rational_resampler.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_flowgraph_rational_resampler.png)
  
Run the flowgraph. The same GUI window that was displayed previously will appear but now audio should be playing through your computer. You can drag the bar at the top of the screen to tune to different channels. 
[![](https://wiki.gnuradio.org/images/thumb/f/f5/B200mini_FM_Receiver_final_output.png/500px-B200mini_FM_Receiver_final_output.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_final_output.png)
  

## Diagnosing Overrun Problems
When you run your flowgraph if you get a string of “OOOOOOOO” or messages about overflows then you have probably entered a sample rate wrong somewhere along the way. Double check all of the sample rate values and interpolation and decimation rate changes. 
[![](https://wiki.gnuradio.org/images/b/ba/B200mini_FM_Receiver_sample_rate_overflows.png)](https://wiki.gnuradio.org/index.php?title=File:B200mini_FM_Receiver_sample_rate_overflows.png)
The previous tutorial, [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver"), describes how to build a broadcast FM receiver using an RTL-SDR receiver. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver&oldid=15418](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver&oldid=15418)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=B200-B205mini+FM+Receiver "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:B200-B205mini_FM_Receiver&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver)
  * [View source](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/B200-B205mini_FM_Receiver "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/B200-B205mini_FM_Receiver "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver&oldid=15418 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver&action=info "More information about this page")


  * This page was last edited on 7 October 2025, at 18:51.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


