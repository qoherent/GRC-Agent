# Streams and Vectors
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors#searchInput)  
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
  7. Streams and Vectors
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
This tutorial describes the differences between a _Stream_ and a _Vector_. 
The previous tutorial, [Packing Bits](https://wiki.gnuradio.org/index.php?title=Packing_Bits "Packing Bits"), describes how to pack a stream of bits into the _byte_ or _char_ data type, and then unpack them back into a stream of bits. The next tutorial, [Hier Blocks and Parameters](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters "Hier Blocks and Parameters"), demonstrates how to create a _Hierarchical Block_ and using _Parameters_. 
## Contents
  * [1 Streams](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors#Streams)
  * [2 Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors#Vectors)
  * [3 Streams to Vector Flowgraph Example](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors#Streams_to_Vector_Flowgraph_Example)
  * [4 Vector to Streams Flowgraph Example](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors#Vector_to_Streams_Flowgraph_Example)


## Streams
Blocks in GNU Radio can be connected using either _streams_ or _vectors_. A _stream_ carries 1 sample for each time instance. A _stream_ produces serialized data. A _stream_ must have a data type, such as _Float 32_ or _Byte_. 
The _Signal Source_ block produces a _Complex Float 32_ stream. The output of the block at each time instance contains 1 complex sample: 
[![](https://wiki.gnuradio.org/images/thumb/f/f4/SignalSourceStreamExample.png/700px-SignalSourceStreamExample.png)](https://wiki.gnuradio.org/index.php?title=File:SignalSourceStreamExample.png)
The figure shows there is a single complex sample at each time instance. 
## Vectors
_Vectors_ carry multiple samples per time instance. _Vectors_ represent data in parallel. A _stream_ represents a scalar at each time instance. A _vector_ represents an array at each time instance. 
GRC uses lighter colors to represent _streams_ and darker colors to represent _vector_ outputs: 
[![](https://wiki.gnuradio.org/images/thumb/f/f0/StreamVectorDarkerColors.png/600px-StreamVectorDarkerColors.png)](https://wiki.gnuradio.org/index.php?title=File:StreamVectorDarkerColors.png)
## Streams to Vector Flowgraph Example
The following example describes how to convert a stream to a vector and back to a stream. Two complex sinusoid streams are converted to a 2-element vector, displayed, and then converted back to their two independent streams. 
Add two _Signal Source_ blocks to the workspace: 
[![](https://wiki.gnuradio.org/images/thumb/3/31/TwoSignalSourceBlocks.png/700px-TwoSignalSourceBlocks.png)](https://wiki.gnuradio.org/index.php?title=File:TwoSignalSourceBlocks.png)
  
Edit the parameters of the second _Signal Source_ to have a frequency of 100 and amplitude of 0.1 to distinguish it visually from the first _Signal Source_ : 
[![](https://wiki.gnuradio.org/images/thumb/d/d9/ChangeFrequencyAmplitudeSignalSource.png/500px-ChangeFrequencyAmplitudeSignalSource.png)](https://wiki.gnuradio.org/index.php?title=File:ChangeFrequencyAmplitudeSignalSource.png)
Click _OK_ to accept the parameters. 
Search for the _Streams to Vector_ block, drag it into the workspace and connect it to the _Signal Source_ blocks: 
[![](https://wiki.gnuradio.org/images/thumb/3/35/ConnectStreamsToVector.png/700px-ConnectStreamsToVector.png)](https://wiki.gnuradio.org/index.php?title=File:ConnectStreamsToVector.png)
The _Streams to Vector_ block acts as an interleaver. The _Streams to Vector_ block takes a sample from the _in0_ port and places it into the first element in the output _vector_. The _Streams to Vector_ block takes a sample from the _in1_ port and places it into the second element in the output _vector_. The _Streams to Vector_ block combines two serial _stream_ inputs into a two-dimensional _vector_ output. 
Search for the _Vector to Stream_ block and add three _QT GUI Time Sink_ blocks to the flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/8/8a/InterleavedSignalSourceWithTimeSinks.png/700px-InterleavedSignalSourceWithTimeSinks.png)](https://wiki.gnuradio.org/index.php?title=File:InterleavedSignalSourceWithTimeSinks.png)
The _Vector to Stream_ block will serialize the vector into a stream. The samples at the output of _Vector to Stream_ will be interleaved. 
  
Edit the titles for the three _QT GUI Time Sink_ blocks so the can be distinguished from one another. First, edit the block connected to the _Signal Source_ with frequency 1000 and amplitude 1 to have the title _Signal Source A_ : 
[![](https://wiki.gnuradio.org/images/thumb/e/e7/TimeSinkAProperty.png/500px-TimeSinkAProperty.png)](https://wiki.gnuradio.org/index.php?title=File:TimeSinkAProperty.png)
[![](https://wiki.gnuradio.org/images/thumb/3/30/SignalSourceATimeSink.png/700px-SignalSourceATimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:SignalSourceATimeSink.png)
  
Now edit the _QT GUI Time Sink_ connected to the second _Signal Source_ with frequency 100 and Amplitude 0.1 to have the title _Signal Source B_ : 
[![](https://wiki.gnuradio.org/images/thumb/b/bd/TimeSinkBProperty.png/500px-TimeSinkBProperty.png)](https://wiki.gnuradio.org/index.php?title=File:TimeSinkBProperty.png)
[![](https://wiki.gnuradio.org/images/thumb/f/fc/SignalSourceBTimeSink.png/700px-SignalSourceBTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:SignalSourceBTimeSink.png)
  
Finally, edit the _QT GUI Time Sink_ connected to the _Vector to Stream_ block to have the title _Interleaved Signal Sources_ : 
[![](https://wiki.gnuradio.org/images/8/8a/TimeSinkInterleavedProperty.png)](https://wiki.gnuradio.org/index.php?title=File:TimeSinkInterleavedProperty.png)
[![](https://wiki.gnuradio.org/images/thumb/a/a9/InterleavedSignalSourceTimeSink.png/700px-InterleavedSignalSourceTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:InterleavedSignalSourceTimeSink.png)
  
Running the flowgraph displays three time sinks, _Signal Source A_ , _Signal Source B_ , and _Interleaved Signal Sources_. The _Interleaved Signal Sources_ time sink displays interleaved samples from _Signal Source A_ and _Signal Source B_ : 
[![](https://wiki.gnuradio.org/images/thumb/2/21/TimeSinkSignalSources.png/700px-TimeSinkSignalSources.png)](https://wiki.gnuradio.org/index.php?title=File:TimeSinkSignalSources.png)
## Vector to Streams Flowgraph Example
The following example deinterleaves (or serializes) the vectorized data, converting it back into two streams. 
Search for the _Vector to Streams_ block, add it to the workspace, and connect it to the _Streams to Vector_ block: 
[![](https://wiki.gnuradio.org/images/thumb/3/32/AddVectorToStreamsBlock.png/700px-AddVectorToStreamsBlock.png)](https://wiki.gnuradio.org/index.php?title=File:AddVectorToStreamsBlock.png)
  
The _Vector to Streams_ block deserializes vector samples and converts them into streams, performing the inverse operation to the _Streams to Vector_ block. 
Right-click and delete the arrows connecting the two _Signal Source_ blocks to the _QT GUI Time Sink_ blocks: 
[![](https://wiki.gnuradio.org/images/thumb/f/fe/DeleteTimeSinks.png/700px-DeleteTimeSinks.png)](https://wiki.gnuradio.org/index.php?title=File:DeleteTimeSinks.png)
  
Move and reconnect the two _QT GUI Time Sink_ blocks to the _Vector to Streams_ outputs: 
[![](https://wiki.gnuradio.org/images/thumb/2/24/ReconnectedTimeSinks.png/700px-ReconnectedTimeSinks.png)](https://wiki.gnuradio.org/index.php?title=File:ReconnectedTimeSinks.png)
  
Run the flowgraph. The vectorized samples have been separated back into two streams: 
[![](https://wiki.gnuradio.org/images/thumb/0/0a/DeserializedTimeSink.png/700px-DeserializedTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:DeserializedTimeSink.png)
  
The next tutorial, [Hier Blocks and Parameters](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters "Hier Blocks and Parameters"), demonstrates how to create a _Hierarchical Block_ and use _Parameters_. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors&oldid=12854](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors&oldid=12854)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Streams+and+Vectors "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Streams_and_Vectors&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors)
  * [View source](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Streams_and_Vectors "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Streams_and_Vectors "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors&oldid=12854 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors&action=info "More information about this page")


  * This page was last edited on 17 January 2023, at 10:18.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


