# Signal Data Types
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types#searchInput)  
|  **Beginner Tutorials** Introducing GNU Radio 
  1. [What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "What Is GNU Radio")
  2. [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR")
  3. [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph")

Flowgraph Fundamentals 
  1. [Python Variables in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "Python Variables in GRC")
  2. [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs")
  3. [Runtime Updating Variables](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables "Runtime Updating Variables")
  4. Signal Data Types
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
This tutorial describes the data types which can be used to represent signals. 
The starting flowgraph from [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph") is used in this section, please complete the tutorial before proceeding. The next tutorial, [Converting Data Types](https://wiki.gnuradio.org/index.php?title=Converting_Data_Types "Converting Data Types"), shows how to convert between different data types. 
## Data Types
Every input and output port on a block will have a data type associated with it. The data type is identified by the color of the input and output port. The GNU Radio data types can be found by opening GNU Radio Companion (GRC) and clicking _Help: Types_ : 
[![](https://wiki.gnuradio.org/images/f/ff/GRCDataTypesHelp.png)](https://wiki.gnuradio.org/index.php?title=File:GRCDataTypesHelp.png)
A window displays the data types and their associated colors: 
[![](https://wiki.gnuradio.org/images/7/7c/Types.png)](https://wiki.gnuradio.org/index.php?title=File:Types.png)
These colors correspond to the _input and output ports_ for blocks in GRC. 
The most common data types in GNU Radio blocks are _Complex Float 32_ in **blue** and _Float 32_ in **orange**. Additional colors include the _Integer 16_ (or _short_) data type in **yellow** and the _Integer 8_ (or _char_) data type in **purple**. 
[![](https://wiki.gnuradio.org/images/4/49/ExamplePortColors.png)](https://wiki.gnuradio.org/index.php?title=File:ExamplePortColors.png)
## Complex Data Type
The following flowgraph uses the _Complex Float 32_ data type, which uses a pair of 32-bit floats to represent the real and imaginary portions of a complex sample. 
[![](https://wiki.gnuradio.org/images/thumb/5/58/FlowgraphWithComplexDataTypes.png/700px-FlowgraphWithComplexDataTypes.png)](https://wiki.gnuradio.org/index.php?title=File:FlowgraphWithComplexDataTypes.png)
  
Running the flowgraph shows the complex signal plotted in the time domain, where _Signal 1_ is the real component and _Signal 2_ is the imaginary component of the complex signal: 
[![](https://wiki.gnuradio.org/images/thumb/8/8c/FlowgraphTimeSinkComplex.png/700px-FlowgraphTimeSinkComplex.png)](https://wiki.gnuradio.org/index.php?title=File:FlowgraphTimeSinkComplex.png)
Each complex sample is therefore 64 bits: a 32-bit float for the real component, and a 32-bit float for the imaginary component. 
## Float Data Type
Many GNU Radio blocks support multiple data types. The data type of the _Signal Source_ block can be changed by double-clicking it and selecting from the _Output Type_ drop-down menu: 
[![](https://wiki.gnuradio.org/images/thumb/0/07/SignalSourceDataTypes.png/500px-SignalSourceDataTypes.png)](https://wiki.gnuradio.org/index.php?title=File:SignalSourceDataTypes.png)
Selecting the _float_ data type will have the _Signal Source_ block create a real sinusoid, represented by the **orange** output port. Note the arrow connecting _Signal Source_ to _Throttle_ is **red** , indicating a data type mismatch error: 
[![](https://wiki.gnuradio.org/images/thumb/d/d6/RealToComplexConnectionError.png/700px-RealToComplexConnectionError.png)](https://wiki.gnuradio.org/index.php?title=File:RealToComplexConnectionError.png)
  
The error is resolved by converting all of the other blocks to the **orange** _Float_ data type. Clicking on the block selects it, highlighting it in **light blue**. Data types may be changed by pressing _UP_ or _DOWN_ on the keyboard: 
[![](https://wiki.gnuradio.org/images/thumb/f/f6/BlockSelected.png/300px-BlockSelected.png)](https://wiki.gnuradio.org/index.php?title=File:BlockSelected.png)
  
The flowgraph is complete after all data types have been converted to _Float_ : 
[![](https://wiki.gnuradio.org/images/thumb/7/72/FlowgraphWithRealDataTypes.png/700px-FlowgraphWithRealDataTypes.png)](https://wiki.gnuradio.org/index.php?title=File:FlowgraphWithRealDataTypes.png)
  
The _Signal Source_ block creates a real output, which is displayed as the only signal in the time domain: 
[![](https://wiki.gnuradio.org/images/thumb/4/43/RealSignalTimeSink.png/700px-RealSignalTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:RealSignalTimeSink.png)
  
The next tutorial, [Converting Data Types](https://wiki.gnuradio.org/index.php?title=Converting_Data_Types "Converting Data Types"), shows how to convert between different data types. Eventually we will learn about vector streams, as part of the [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors") tutorial. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Signal_Data_Types&oldid=13583](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types&oldid=13583)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Signal+Data+Types "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Signal_Data_Types&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types)
  * [View source](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Signal_Data_Types "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Signal_Data_Types "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types&oldid=13583 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types&action=info "More information about this page")


  * This page was last edited on 6 November 2023, at 17:19.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


