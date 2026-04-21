# Packing Bits
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Packing_Bits#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Packing_Bits#searchInput)  
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
  6. Packing Bits
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
This tutorial describes how to pack bits into a byte using the _Pack K Bits_ block, and how to unpack a byte into bits, using the _Unpack K Bits_ block. 
The previous tutorial, [Converting Data Types](https://wiki.gnuradio.org/index.php?title=Converting_Data_Types "Converting Data Types"), describes the _char_ or _byte_ data type and how to convert between data types. The next tutorial, [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors"), describes the differences between streams and vectors and how to use them in flowgraphs. 
## Contents
  * [1 Starting the Packing Bits Flowgraph](https://wiki.gnuradio.org/index.php?title=Packing_Bits#Starting_the_Packing_Bits_Flowgraph)
  * [2 Explaining the _Packing K Bits_ Block](https://wiki.gnuradio.org/index.php?title=Packing_Bits#Explaining_the_Packing_K_Bits_Block)
  * [3 Finishing the _Pack K Bits_ Flowgraph](https://wiki.gnuradio.org/index.php?title=Packing_Bits#Finishing_the_Pack_K_Bits_Flowgraph)
  * [4 Unpacking Bits](https://wiki.gnuradio.org/index.php?title=Packing_Bits#Unpacking_Bits)


## Starting the Packing Bits Flowgraph
Packing bits into a _byte_ is useful in representing binary data (as opposed to digitized RF samples) as well as when using the modulator blocks: _Constellation Modulator_ , _GFSK Mod_ and _OFDM Transmitter_. Create a new flowgraph and add the _Random Source_ block to the workspace: 
[![](https://wiki.gnuradio.org/images/thumb/7/73/AddRandomSourceToWorkspace.png/400px-AddRandomSourceToWorkspace.png)](https://wiki.gnuradio.org/index.php?title=File:AddRandomSourceToWorkspace.png)
  
Click on _Random Source_. It is selected when outlined in **light blue** : 
[![](https://wiki.gnuradio.org/images/thumb/4/42/SelectRandomSourceBlock.png/400px-SelectRandomSourceBlock.png)](https://wiki.gnuradio.org/index.php?title=File:SelectRandomSourceBlock.png)
  
Press the _UP_ or _DOWN_ keys to cycle through the different data types until the _byte_ data type is selected, denoted by the **purple** output port color: 
[![](https://wiki.gnuradio.org/images/thumb/f/f5/ChangeRandomSourceToByte.png/400px-ChangeRandomSourceToByte.png)](https://wiki.gnuradio.org/index.php?title=File:ChangeRandomSourceToByte.png)
  
The random source generates bytes with a minimum value of _Minimum_ up to a maximum value of _Maximum-1_. In this case, _Minimum = 0_ and _Maximum = 2_ , so it will create binary _0_ and _1_. A _Pack K Bits_ block is used to parallelize, or _pack_ , multiple bits into a single byte to represent larger binary values. 
Add the _Throttle_ , _Pack K Bits_ , _Char to Float_ , and _QT GUI Histogram Sink_ blocks to the flowgraph and connect them: 
[![](https://wiki.gnuradio.org/images/thumb/7/7a/PackBitsStartingFlowgraph.png/800px-PackBitsStartingFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:PackBitsStartingFlowgraph.png)
## Explaining the _Packing K Bits_ Block
The _Pack K Bits_ block takes _K_ bits and places them into a byte by filling the least significant bit (LSB) first. 
For this example _K=4_. The _Random Source_ will generate bit _B 0_ first. This will be received by _Pack K Bits_ and then stored in the LSB:          [0 0 0 0 0 0 0 B0]
The second bit generated by _Pack K Bits_ is _B 1_, which is then stored by _Pack K Bits_ according to:          [0 0 0 0 0 0 B1 B0]
Following this trend, the next bits _B 2_ and _B 3_ will then be stored as:          [0 0 0 0 B3 B2 B1 B0]
The following image demonstrates how the block works: 
[![](https://wiki.gnuradio.org/images/thumb/6/67/PackingBitsExample.png/700px-PackingBitsExample.png)](https://wiki.gnuradio.org/index.php?title=File:PackingBitsExample.png)
  
Because _K=4_ bits have been packed, the byte _0000B 3B2B1B0_ will be produced as an output and a new byte will be started. The output value of the byte in decimal (base-10) is:           = (B3*23) + (B2*22) + (B1*21) + (B0*20)     = (B3*8) + (B2*4) + (B1*2) + (B0)
For example, if: 
  * _B 0=0_
  * _B 1=1_
  * _B 2=0_
  * _B 3=1_


the byte would be represented by _00001010_ and the decimal value is: 

```
8 + 0 + 2 + 0 = 10
```

## Finishing the _Pack K Bits_ Flowgraph
Edit the properties of _Pack K Bits_ : 
  * K: _4_


[![](https://wiki.gnuradio.org/images/thumb/1/11/Pack4Bits.png/500px-Pack4Bits.png)](https://wiki.gnuradio.org/index.php?title=File:Pack4Bits.png)
  
Four bits produces numbers from 0 to _2 4-1=15_. Edit the top _QT GUI Histogram Sink_ properties and change the following: 
  * Title: _4 Bits_
  * Number of Bins: _1024_
  * Max x-axis: _16_


[![](https://wiki.gnuradio.org/images/thumb/d/d7/HistogramSink4Bits.png/500px-HistogramSink4Bits.png)](https://wiki.gnuradio.org/index.php?title=File:HistogramSink4Bits.png)
Four bits produces numbers from 0 to _2 4-1=15_. Edit the bottom _QT GUI Histogram Sink_ properties and change the following: 
  * Title: _1 Bit_
  * Number of Bins: _1024_
  * Max x-axis: _16_


[![](https://wiki.gnuradio.org/images/thumb/0/01/HistogramSink1Bit.png/500px-HistogramSink1Bit.png)](https://wiki.gnuradio.org/index.php?title=File:HistogramSink1Bit.png)
The flowgraph should look like the following: 
[![](https://wiki.gnuradio.org/images/thumb/7/7e/PackBitsFinalFlowgraph.png/800px-PackBitsFinalFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:PackBitsFinalFlowgraph.png)
  
Run the flowgraph. The _1 Bit_ histogram shows values of _0_ and _1_ , while the _4 Bits_ histogram shows values from _0_ to _15_ : 
[![](https://wiki.gnuradio.org/images/thumb/7/74/Histogram4Bits1Bit.png/500px-Histogram4Bits1Bit.png)](https://wiki.gnuradio.org/index.php?title=File:Histogram4Bits1Bit.png)
## Unpacking Bits
Unpacking serializes a _byte_ into a string of bits. Add the _Unpack K Bits_ block to the workspace and connect it between the _Pack K Bits_ block and the _Char to Float_ block. Edit the _Unpack K Bits_ block properties and enter _K: 4_. 
[![](https://wiki.gnuradio.org/images/thumb/e/e7/UnpackBitsFlowgraph.png/800px-UnpackBitsFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:UnpackBitsFlowgraph.png)
  
Run the flowgraph. The _1 Bit_ Histogram shows the packed 4 bits are unpacked (serialized) back into values of _0_ and _1_ : 
[![](https://wiki.gnuradio.org/images/thumb/f/fe/HistogramUnpackBits.png/500px-HistogramUnpackBits.png)](https://wiki.gnuradio.org/index.php?title=File:HistogramUnpackBits.png)
  
The unpacking starts with the _LSB_ first and proceeds to the _most significant bit (MSB)_. From the previous example, the _Pack K Bits_ produced a byte with bits _0000B 3B2B1B0_. The _Unpack K Bits_ block produces an output with bit _B 0_ first, then _B 1_, _B 2_ and _B 3_ and the 4 remaining 4 zeros in the byte are ignored. 
The _Unpack K Bits_ block perfectly reverses the the operation by the _Pack K Bits_ input stream. This can be verified by adding a _QT GUI Time Sink_ block with two inputs: 
[![](https://wiki.gnuradio.org/images/thumb/6/67/TwoInputsTimeSink.png/500px-TwoInputsTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:TwoInputsTimeSink.png)
  
Connect the blocks: 
[![](https://wiki.gnuradio.org/images/thumb/8/8a/PackUnpackFlowgraph.png/800px-PackUnpackFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:PackUnpackFlowgraph.png)
  
The _QT QUI Time Sink_ shows the output: 
[![](https://wiki.gnuradio.org/images/thumb/1/1a/PackUnpackTimeSink.png/700px-PackUnpackTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:PackUnpackTimeSink.png)
  
Left-mouse click and drag over a smaller portion to zoom in: 
[![](https://wiki.gnuradio.org/images/thumb/e/e1/ClickAndDragTimeSink.png/700px-ClickAndDragTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:ClickAndDragTimeSink.png)
  
The two plots are perfectly overlapping. The input to the _Pack K Bits_ block is the exact same as the output of the _Unpack K Bits_ block. This demonstrates how the _Pack K Bits_ and _Unpack Bits_ perform perfect inverse operations. 
[![](https://wiki.gnuradio.org/images/thumb/9/99/PackUnpackTimeSinkZoom.png/700px-PackUnpackTimeSinkZoom.png)](https://wiki.gnuradio.org/index.php?title=File:PackUnpackTimeSinkZoom.png)
  

The next tutorial, [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors"), describes the differences between streams and vectors and how to use them in flowgraphs. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Packing_Bits&oldid=13574](https://wiki.gnuradio.org/index.php?title=Packing_Bits&oldid=13574)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Packing+Bits "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Packing_Bits "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Packing_Bits "Discussion about the content page \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Packing_Bits)
  * [View source](https://wiki.gnuradio.org/index.php?title=Packing_Bits&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Packing_Bits&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Packing_Bits "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Packing_Bits "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Packing_Bits&oldid=13574 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Packing_Bits&action=info "More information about this page")


  * This page was last edited on 3 November 2023, at 16:40.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


