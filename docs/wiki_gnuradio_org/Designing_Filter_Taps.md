# Designing Filter Taps
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps#searchInput)  
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
  2. Designing Filter Taps
  3. [Sample Rate Change](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change "Sample Rate Change")
  4. [Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "Frequency Shifting")
  5. [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files")

SDR Hardware 
  1. [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver")
  2. [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver")
  3. [E310 FM Receiver](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver "E310 FM Receiver")

 |  
| --- |  
This tutorial demonstrates how to create a list or array of filter taps and apply them within a low pass filtering block. 
This tutorial makes use of the flowgraph developed in the previous tutorial, [Low Pass Filter Example](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example "Low Pass Filter Example"), so please complete it before continuing. The next tutorial, [Sample Rate Change](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change "Sample Rate Change"), describes how to perform sample rate change in GNU Radio. 
## Designing the Filter Taps
Begin with the flowgraph from [Low Pass Filter Example](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example "Low Pass Filter Example") but replace the _Low Pass Filter_ with a _Frequency Xlating FIR Filter_ and drag in the _Low-Pass Filter Taps_ block: 
  
[![](https://wiki.gnuradio.org/images/thumb/4/45/FlowgraphFrequencyXlatingFilterStart.png/700px-FlowgraphFrequencyXlatingFilterStart.png)](https://wiki.gnuradio.org/index.php?title=File:FlowgraphFrequencyXlatingFilterStart.png)
  
The _Low-Pass Filter Taps_ block designs a set of filter taps that can be applied to filtering blocks. Filter taps may also be referred to as _weights_ or _coefficients._ The response and performance of the filter is dependent on the parameters entered by the user. Double-click the _Low-Pass Filter Taps_ block to open the properties. Edit the properties: 
  * Id: _lowPassFilterTaps_
  * Cutoff Freq (Hz): _samp_rate/4_
  * Transition Width (Hz): _samp_rate/8_


[![](https://wiki.gnuradio.org/images/thumb/b/b4/LowPassFilterTapsProperties.png/500px-LowPassFilterTapsProperties.png)](https://wiki.gnuradio.org/index.php?title=File:LowPassFilterTapsProperties.png)
The _Low-Pass Filter Taps_ block saves the filter taps in a list within the _lowPassFilterTaps_ variable. 
Double-click the _Frequency Xlating FIR Filter_ block to edit the properties. Enter _lowPassFilterTaps_ for _Taps_ and leave all of the other parameters the same. Hovering over the _lowPassFilterTaps_ variable displays information about the filter taps: 
[![](https://wiki.gnuradio.org/images/thumb/1/10/LowPassFilterTapsTuple.png/500px-LowPassFilterTapsTuple.png)](https://wiki.gnuradio.org/index.php?title=File:LowPassFilterTapsTuple.png)
  
The first couple of filter taps are displayed in a list. Block parameters in GNU Radio accept data objects such as tuples, arrays and lists. In some instances, blocks require their parameters to be of a specific data type. Save the properties and run the flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/4/4c/LowPassFilterTapsFreqSink.png/700px-LowPassFilterTapsFreqSink.png)](https://wiki.gnuradio.org/index.php?title=File:LowPassFilterTapsFreqSink.png)
  
Scroll-wheel-click and select _Max Hold_ , then slowly drag the _frequency_ slider across all of the values. The magnitude of the frequency response can then be seen through the outline: 
[![](https://wiki.gnuradio.org/images/thumb/a/a1/LowPassFilterTapsMaxHold.png/700px-LowPassFilterTapsMaxHold.png)](https://wiki.gnuradio.org/index.php?title=File:LowPassFilterTapsMaxHold.png)
## Entering Filter Taps Manually
Alternative methods can be used to design filter taps and then enter them manually as a Python variable. For example, the _Frequency Xlating FIR Filter_ block accepts filter taps as a NumPy array. To be able to access NumPy's functions and data types, it needs to be imported first. Add the _Import_ block to the GRC workspace: 
[![](https://wiki.gnuradio.org/images/thumb/3/31/AddImportBlock.png/700px-AddImportBlock.png)](https://wiki.gnuradio.org/index.php?title=File:AddImportBlock.png)
  
Double-click the block and add the import statement: 

```
import numpy as np
```

[![](https://wiki.gnuradio.org/images/thumb/7/72/ImportProperties.png/500px-ImportProperties.png)](https://wiki.gnuradio.org/index.php?title=File:ImportProperties.png)
  
A simple moving-average filter, or _boxcar_ , can be designed by setting all of filter taps to be the same. This can be done by using the NumPy _ones()_ function which returns a NumPy array of all ones with a specified length. Create a variable named _boxcarFilter_ with the _Value_ being: 

```
np.ones(8)/8
```

[![](https://wiki.gnuradio.org/images/thumb/d/d3/BoxcarFilterTaps.png/500px-BoxcarFilterTaps.png)](https://wiki.gnuradio.org/index.php?title=File:BoxcarFilterTaps.png)
  
Right-click on _Low-Pass Filter Taps_ and then _Disable_ : 
[![](https://wiki.gnuradio.org/images/thumb/8/87/DisableLowPassFilterTaps.png/700px-DisableLowPassFilterTaps.png)](https://wiki.gnuradio.org/index.php?title=File:DisableLowPassFilterTaps.png)
  
Then edit the properties of _Frequency Xlating FIR Filter_ and replace _lowPassTaps_ with _boxcarFilter_. The flowgraph looks like the following: 
[![](https://wiki.gnuradio.org/images/thumb/1/13/UpdateXlatingFilterTaps.png/700px-UpdateXlatingFilterTaps.png)](https://wiki.gnuradio.org/index.php?title=File:UpdateXlatingFilterTaps.png)
  
Run the flowgraph, select _Max Hold_ and then sweep the frequency slider. A different frequency response magnitude can be seen now that different filter taps are used: 
[![](https://wiki.gnuradio.org/images/thumb/1/1e/BoxcarFilterMaxHold.png/700px-BoxcarFilterMaxHold.png)](https://wiki.gnuradio.org/index.php?title=File:BoxcarFilterMaxHold.png)
  

## Real to Complex Filter
Many of the filtering blocks have options to select combinations of real or complex data types for the input and output, as well as real or complex filter weights. This example demonstrates one method of how to use complex filter weights to transform a real signal into a complex signal. Re-create the following flowgraph by deleting the _boxcarFilter_ variable and enabling again the _Low-pass Filter Taps_ block: 
[![](https://wiki.gnuradio.org/images/thumb/6/6a/DeleteBoxcarFilterVariable.png/700px-DeleteBoxcarFilterVariable.png)](https://wiki.gnuradio.org/index.php?title=File:DeleteBoxcarFilterVariable.png)
The _lowPassTaps_ are used as the basis for a complex band-pass filter. Create a variable _n_ with the _Value_

```
np.arange(0,len(lowPassTaps))
```

which produces an array of integers: 0, 1, 2, 3, ... up to the length of _lowPassTaps_ : 
[![](https://wiki.gnuradio.org/images/thumb/5/5b/NVariable.png/500px-NVariable.png)](https://wiki.gnuradio.org/index.php?title=File:NVariable.png)
Create the _frequencyShift_ variable with _Value_ : 

```
np.exp(2j*np.pi*0.25*n)
```

[![](https://wiki.gnuradio.org/images/thumb/6/6e/FrequencyShiftVariable.png/500px-FrequencyShiftVariable.png)](https://wiki.gnuradio.org/index.php?title=File:FrequencyShiftVariable.png)
which is a complex sinusoid with a frequency of 1/4th the sampling rate. The _frequencyShift_ variable changes the center frequency of _lowPassTaps_ from 0 to 1/4th the sampling rate. Create a variable _bandPassTaps_ with value: 

```
lowPassTaps*frequencyShift
```

[![](https://wiki.gnuradio.org/images/thumb/5/51/BandPassTapsVariable.png/500px-BandPassTapsVariable.png)](https://wiki.gnuradio.org/index.php?title=File:BandPassTapsVariable.png)
  
Double-click the _Frequency Xlating FIR Filter_ block to edit the properties. Click the drop-down menu for _Type_ and select _Real- >Complex(Complex Taps)_: 
[![](https://wiki.gnuradio.org/images/thumb/a/a8/SelectRealInputComplexOutput.png/500px-SelectRealInputComplexOutput.png)](https://wiki.gnuradio.org/index.php?title=File:SelectRealInputComplexOutput.png)
  
Replace _lowPassTaps_ with _bandPassTaps_ in the _Frequency Xlating Filter_. 
Edit the properties of the _Signal Source_ and convert it to a real signal. 
[![](https://wiki.gnuradio.org/images/thumb/c/cd/RealSignalSource.png/500px-RealSignalSource.png)](https://wiki.gnuradio.org/index.php?title=File:RealSignalSource.png)
The flowgraph looks like: 
[![](https://wiki.gnuradio.org/images/thumb/c/ce/RealToComplexFlowgraph.png/700px-RealToComplexFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:RealToComplexFlowgraph.png)
Run the flowgraph, turn on _Max Hold_ , and sweep the _frequency_ variable: 
  
[![](https://wiki.gnuradio.org/images/thumb/3/3d/RealToComplexMaxHold.png/700px-RealToComplexMaxHold.png)](https://wiki.gnuradio.org/index.php?title=File:RealToComplexMaxHold.png)
The magnitude of the frequency response shows the center frequency of the low-pass filter has been moved up to 1/4th the sampling rate, which is now a band-pass filter. The frequency response is now different between the positive and negative frequencies, which can be a property of complex filters (but not real filters). 
The next tutorial, [Sample Rate Change](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change "Sample Rate Change"), describes how to perform sample rate change in GNU Radio. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps&oldid=12889](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps&oldid=12889)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Designing+Filter+Taps "You are encouraged to log in; however, it is not mandatory \[o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps "View the content page \[c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Designing_Filter_Taps&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps)
  * [View source](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps&action=edit "This page is protected.
You can view its source \[e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps&action=history "Past revisions of this page \[h\]")


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
  * [Recent changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChanges "A list of recent changes in the wiki \[r\]")
  * [Random page](https://wiki.gnuradio.org/index.php?title=Special:Random "Load a random page \[x\]")
  * [Help](https://www.mediawiki.org/wiki/Special:MyLanguage/Help:Contents "The place to find out")


###  Tools
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Designing_Filter_Taps "A list of all wiki pages that link here \[j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Designing_Filter_Taps "Recent changes in pages linked from this page \[k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps&oldid=12889 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps&action=info "More information about this page")


  * This page was last edited on 20 January 2023, at 12:39.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


