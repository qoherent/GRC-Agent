# Low Pass Filter Example
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example#searchInput)  
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
  1. Low Pass Filter Example
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
This tutorial describes how to use a low-pass filter in GNU Radio. 
The previous tutorial, [Python Block Tags](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags "Python Block Tags"), describes how to read and write tags in a Python block. The next tutorial, [Designing Filter Taps](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps "Designing Filter Taps"), describes how to design a set of low-pass filter taps and apply them against a signal. 
## Contents
  * [1 Creating the Flowgraph](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example#Creating_the_Flowgraph)
  * [2 Run the Flowgraph](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example#Run_the_Flowgraph)
  * [3 The Impulse Response](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example#The_Impulse_Response)
  * [4 Noise Instead of Signal](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example#Noise_Instead_of_Signal)
  * [5 Next Steps](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example#Next_Steps)


## Creating the Flowgraph
Begin by adding the following blocks to the GRC work space: 
  1. Signal Source
  2. Low Pass Filter
  3. Throttle
  4. QT GUI Frequency Sink
  5. QT GUI Range


Connect the blocks in the following manner: 
[![](https://wiki.gnuradio.org/images/thumb/9/90/LPFTutorialFlowgraphStart.png/700px-LPFTutorialFlowgraphStart.png)](https://wiki.gnuradio.org/index.php?title=File:LPFTutorialFlowgraphStart.png)
The _QT GUI Range_ block is used to control the frequency of the _Signal Source_ block. Double-click the _QT GUI Range_ block and edit the properties: 
  * Id: _frequency_
  * Default Value: _0_
  * Start: _-samp_rate/2_
  * Stop: _samp_rate/2_


[![](https://wiki.gnuradio.org/images/thumb/9/9d/SetQTGUIRangeValuesFrequency.png/500px-SetQTGUIRangeValuesFrequency.png)](https://wiki.gnuradio.org/index.php?title=File:SetQTGUIRangeValuesFrequency.png)
Click _OK_ to save. 
Double-click the _Signal Source_ block and enter _frequency_ from the _QT GUI Range_ variable: 
[![](https://wiki.gnuradio.org/images/thumb/3/30/EditSignalSourceFrequency.png/500px-EditSignalSourceFrequency.png)](https://wiki.gnuradio.org/index.php?title=File:EditSignalSourceFrequency.png)
Click _OK_ to save. The flowgraph looks like the following image. Notice that the _Low Pass Filter_ has a _Cutoff Freq_ and _Transition Width_ of 0: 
[![](https://wiki.gnuradio.org/images/thumb/9/9e/FlowgraphWithZeroCutoffFrequency.png/700px-FlowgraphWithZeroCutoffFrequency.png)](https://wiki.gnuradio.org/index.php?title=File:FlowgraphWithZeroCutoffFrequency.png)
  
Double-click the _Low Pass Filter_ block and edit the properties: 
  * Cutoff freq: _samp_rate/4_
  * Transition Width: _samp_rate/8_


[![](https://wiki.gnuradio.org/images/thumb/f/fb/SetLowPassFilterProperties.png/500px-SetLowPassFilterProperties.png)](https://wiki.gnuradio.org/index.php?title=File:SetLowPassFilterProperties.png)
  
The flowgraph is complete and looks like the following: 
[![](https://wiki.gnuradio.org/images/thumb/6/6e/CompleteLPFFlowgraph.png/700px-CompleteLPFFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:CompleteLPFFlowgraph.png)
  

## Run the Flowgraph
The flowgraph is complete! Run the flowgraph. The _QT GUI Frequency Sink_ appears with a _frequency_ slider bar: 
[![](https://wiki.gnuradio.org/images/thumb/d/d9/RunLPFFlowgraph.png/700px-RunLPFFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:RunLPFFlowgraph.png)
  
Scroll-wheel-click on the _QT GUI Frequency_ window and select _Max Hold_ : 
[![](https://wiki.gnuradio.org/images/thumb/5/5b/SelectMaxHold.png/700px-SelectMaxHold.png)](https://wiki.gnuradio.org/index.php?title=File:SelectMaxHold.png)
  
The _Max Hold_ option retains and displays the maximum value at each frequency until the the flowgraph is closed. Clicking through multiple values of the _frequency_ slider bar at the top shows the low pass filter response: 
[![](https://wiki.gnuradio.org/images/thumb/c/cf/LPFMaxHoldDisplay.png/700px-LPFMaxHoldDisplay.png)](https://wiki.gnuradio.org/index.php?title=File:LPFMaxHoldDisplay.png)
## The Impulse Response
The impulse response of a filter shows the entire response of that filter. In digital signal processing, it's possible to see the impulse response by doing just that, feeding an impulse to the filter and viewing its output. 
To view the impulse response, make the following adjustments to the flowgraph above: 
  * Change the "Signal Source" block to a "Vector Source" block. NOTE: The "Range" block is no longer necessary, and can be deleted.
  * Add a "Variable" block.


The flowgraph will appear as follows: 
[![](https://wiki.gnuradio.org/images/thumb/5/54/LPF-filter-with-vector-source-flowgraph.png/700px-LPF-filter-with-vector-source-flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:LPF-filter-with-vector-source-flowgraph.png)
Make the following changes to the different blocks: 
new Variable block 
  * _ID_ : **N**
  * _Value_ : **1024**


Vector Source block 
  * _Vector_ : **(N,)+(0,)*int(N-1)**


Low Pass Filter block 
  * _Cutoff Freq_ : **samp_rate/8**
  * _Transition Width_ : **samp_rate/16**


[![](https://wiki.gnuradio.org/images/thumb/d/dc/LPF-properties-annotated.png/700px-LPF-properties-annotated.png)](https://wiki.gnuradio.org/index.php?title=File:LPF-properties-annotated.png)
QT GUI Frequency Sink block 
  * _FFT Size_ : **N**
  * _Window Type_ : **Rectangular**


[![](https://wiki.gnuradio.org/images/thumb/c/cd/Frequency-sink-properties-annotated.png/700px-Frequency-sink-properties-annotated.png)](https://wiki.gnuradio.org/index.php?title=File:Frequency-sink-properties-annotated.png)
Running the flowgraph shows the following spectrum: 
[![](https://wiki.gnuradio.org/images/thumb/6/61/Filter-impulse-response-spectrum.png/700px-Filter-impulse-response-spectrum.png)](https://wiki.gnuradio.org/index.php?title=File:Filter-impulse-response-spectrum.png)
The output of the _Vector Source_ block is a single impulse followed by a lot of zeros. This addition of the extra zeros is called _zeropadding_. The impulse is convolved in the _Low Pass Filter_. The output of the filter passes into the _QT GUI Frequency Sink_ block, which creates the spectral display. Because of the zeropadding, the spectral trace is a smooth curve showing the full spectrum of the filter. Note that the frequency sink is set to use a _Window Type_ of _Rectangular_ since the input is essentially self-windowed. Any other type of window in the _QT GUI Frequency Sink_ block will create a display that will be greatly reduced in amplitude. 
You can change the _Cutoff Freq_ , _Transition Width_ , and _Window_ in the properties for the _Low Pass Filter_ block in order to see how it affects the output spectrum. 
## Noise Instead of Signal
Lastly, try replacing the Signal Source or Vector Source with a Noise Source (or Fast Noise Source, they do the same thing), and note how the output changes. 
## Next Steps
The next tutorial, [Designing Filter Taps](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps "Designing Filter Taps"), describes how to design a set of low-pass filter taps and apply them against a signal. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example&oldid=14503](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example&oldid=14503)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Low+Pass+Filter+Example "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Low_Pass_Filter_Example&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example)
  * [View source](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Low_Pass_Filter_Example "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Low_Pass_Filter_Example "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example&oldid=14503 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example&action=info "More information about this page")


  * This page was last edited on 14 July 2024, at 01:25.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


