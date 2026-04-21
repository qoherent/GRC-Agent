# Variables in Flowgraphs
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs#searchInput)  
|  **Beginner Tutorials** Introducing GNU Radio 
  1. [What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "What Is GNU Radio")
  2. [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR")
  3. [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph")

Flowgraph Fundamentals 
  1. [Python Variables in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "Python Variables in GRC")
  2. Variables in Flowgraphs
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
This tutorial describes how to use variables in a flowgraph. The flowgraph from a previous tutorial ([Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph")) is used as a starting point for this tutorial. Please complete the [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph") tutorial beforehand. 
The previous tutorial, [Python Variables in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "Python Variables in GRC"), describes how GRC uses Python data types and how values are displayed in _Variable_ blocks. The next tutorial, [Runtime Updating Variables](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables "Runtime Updating Variables"), demonstrates how variables are updated while a flowgraph is running. 
## Basic Variables
GRC allows a user to interact with GNURadio flowgraphs, either ones they create from scratch interactively, or ones that are read from a _.grc_ file. 
When the GRC user uses the _play_ button to execute a flowgraph, GRC creates a _.py_ Python file that contains the flowgraph's code. 
Python code can have variables, and a GNURadio flowgraph can have variables created by the _Variable_ block. 
Every new flowgraph starts with the _samp_rate_ variable: 
[![](https://wiki.gnuradio.org/images/thumb/3/3a/VariableSampRate.png/600px-VariableSampRate.png)](https://wiki.gnuradio.org/index.php?title=File:VariableSampRate.png)
  
GNURadio blocks are implemented as functions. GNU Radio blocks take parameters which modify the behavior. All of the blocks in the flowgraph above use _samp_rate_ as a parameter. Create a new variable block by dragging and dropping it from the block library on the right: 
[![](https://wiki.gnuradio.org/images/thumb/9/94/NewVariableBlock.png/800px-NewVariableBlock.png)](https://wiki.gnuradio.org/index.php?title=File:NewVariableBlock.png)
  
Double-click the _variable_0_ block to view and modify the parameters. 
[![](https://wiki.gnuradio.org/images/thumb/e/ed/VariableProperties.png/500px-VariableProperties.png)](https://wiki.gnuradio.org/index.php?title=File:VariableProperties.png)
  
The _Id_ field is the name of the variable. The variable will be the frequency of the _Signal Source_ block. Edit the name to _frequency_. Now edit the value to _4000_. 
[![](https://wiki.gnuradio.org/images/thumb/3/3a/FrequencyVariable.png/500px-FrequencyVariable.png)](https://wiki.gnuradio.org/index.php?title=File:FrequencyVariable.png)
Click _OK_ to save. 
Double-click the _Signal Source_ block to modify the parameters: 
[![](https://wiki.gnuradio.org/images/thumb/c/ce/SignalSourceProperties.png/500px-SignalSourceProperties.png)](https://wiki.gnuradio.org/index.php?title=File:SignalSourceProperties.png)
  
The _Frequency_ is set to _1000_. Enter _frequency_ into the Frequency field to use the variable: 
[![](https://wiki.gnuradio.org/images/thumb/9/9c/SignalSourceFrequency.png/500px-SignalSourceFrequency.png)](https://wiki.gnuradio.org/index.php?title=File:SignalSourceFrequency.png)
  
Click _OK_ to save the properties. The _frequency_ variable and the value within the _Signal Source_ block are updated: 
[![](https://wiki.gnuradio.org/images/thumb/a/a3/FlowgraphWithFrequencyVariable.png/600px-FlowgraphWithFrequencyVariable.png)](https://wiki.gnuradio.org/index.php?title=File:FlowgraphWithFrequencyVariable.png)
  
Run the flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/7/76/FlowgraphNewFrequencyOutput.png/800px-FlowgraphNewFrequencyOutput.png)](https://wiki.gnuradio.org/index.php?title=File:FlowgraphNewFrequencyOutput.png)
  
The peak of the frequency response has moved to _4,000_ due to the variable change. 
## Dependent Variables
Variables can be dependent on one another. The _Id_ and _Value_ fields are converted into a line of Python in the following manner: 

```
Id = Value
```

The _frequency_ variable was modified to accept the value _4000_ , which is the same as a line of Python code: 

```
frequency = 4000
```

The _frequency_ variable can also be dependent on another variable. Edit _frequency_ to enter the value _samp_rate/3_ , which for _samp_rate = 32000_ will be a frequency of _10,667_. 
[![](https://wiki.gnuradio.org/images/thumb/7/7e/ExampleDependentVariable.png/500px-ExampleDependentVariable.png)](https://wiki.gnuradio.org/index.php?title=File:ExampleDependentVariable.png)
  
The change is displayed in the flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/f/ff/UpdatedFrequencyFlowgraph.png/600px-UpdatedFrequencyFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:UpdatedFrequencyFlowgraph.png)
  
Running the flowgraph shows the frequency is updated: 
[![](https://wiki.gnuradio.org/images/thumb/f/f1/FrequencySinkUpdatedFrequency.png/800px-FrequencySinkUpdatedFrequency.png)](https://wiki.gnuradio.org/index.php?title=File:FrequencySinkUpdatedFrequency.png)
  
The next tutorial, [Runtime Updating Variables](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables "Runtime Updating Variables"), demonstrates how variables are updated while a flowgraph is running. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs&oldid=12392](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs&oldid=12392)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Variables+in+Flowgraphs "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Variables_in_Flowgraphs&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs)
  * [View source](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Variables_in_Flowgraphs "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Variables_in_Flowgraphs "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs&oldid=12392 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs&action=info "More information about this page")


  * This page was last edited on 27 July 2022, at 15:21.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


