# Hier Blocks and Parameters
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters#searchInput)  
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
  8. Hier Blocks and Parameters

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
This tutorial describes how to create a hierarchical block, or _Hier block_ , in GRC. 
The previous tutorial, [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors"), describes the differences between _Streams_ and _Vectors_. The next tutorial, [Creating Your First Block](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block "Creating Your First Block"), describes how to use the _Embedded Python Block_ to create a signal processing block in GNU Radio. 
  

## Contents
  * [1 Creating the Initial Flowgraph](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters#Creating_the_Initial_Flowgraph)
  * [2 Create The Hier Block](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters#Create_The_Hier_Block)
  * [3 Variables vs Parameters](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters#Variables_vs_Parameters)
  * [4 Input and Output Ports](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters#Input_and_Output_Ports)
  * [5 Generate the Hier Block Code](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters#Generate_the_Hier_Block_Code)
  * [6 Using the Hier Block](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters#Using_the_Hier_Block)
  * [7 Deleting a Hier Block](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters#Deleting_a_Hier_Block)


## Creating the Initial Flowgraph
A _hier block_ is used as a wrapper to simplify multiple GNU Radio blocks into a single block. The example _hier block_ will be a frequency shifter block which multiplies a _Signal Source_ against an input signal. 
[![](https://wiki.gnuradio.org/images/thumb/3/30/FrequencyShifterBlock.png/500px-FrequencyShifterBlock.png)](https://wiki.gnuradio.org/index.php?title=File:FrequencyShifterBlock.png)
  
The first step is creating the flowgraph. Drag and drop the following blocks into the workspace: 
  1. _Signal Source_
  2. _Multiply_
  3. _Noise Source_
  4. _Low Pass Filter_
  5. _Throttle_
  6. _QT GUI Frequency Sink_
  7. _QT GUI Range_


Connect the blocks: 
[![](https://wiki.gnuradio.org/images/thumb/8/88/StartingFlowgraphHierBlock.png/700px-StartingFlowgraphHierBlock.png)](https://wiki.gnuradio.org/index.php?title=File:StartingFlowgraphHierBlock.png)
Update the _QT GUI Range_ properties: 
  * Id: _frequency_
  * Default Value: _0_
  * Start: _-samp_rate/2_
  * Stop: _samp_rate/2_


Update the _Low Pass Filter_ properties: 
  * Cutoff Freq (Hz): _samp_rate/4_
  * Transition Width (Hz): _samp_rate/8_


## Create The Hier Block
Click and drag in the workspace window to select the _Signal Source_ and _Multiply_ blocks including the connection between them: 
[![](https://wiki.gnuradio.org/images/thumb/e/ed/ClickAndDragSelect.png/700px-ClickAndDragSelect.png)](https://wiki.gnuradio.org/index.php?title=File:ClickAndDragSelect.png)
  
Right-click on one of the highlighted blocks and select _More > Create Hier_: 
[![](https://wiki.gnuradio.org/images/thumb/6/6d/ClickCreateHier.png/700px-ClickCreateHier.png)](https://wiki.gnuradio.org/index.php?title=File:ClickCreateHier.png)
  
A flowgraph is created in a new GRC tab: 
[![](https://wiki.gnuradio.org/images/thumb/f/f7/NewHierBlock.png/500px-NewHierBlock.png)](https://wiki.gnuradio.org/index.php?title=File:NewHierBlock.png)
  
Double-click the _Options_ block and edit the properties: 
  * Id: _FrequencyShifter_
  * Title: _Frequency Shifter Block_
  * Generate Options: _Hier Block_


[![](https://wiki.gnuradio.org/images/thumb/c/c6/OptionsSelectHierBlock.png/500px-OptionsSelectHierBlock.png)](https://wiki.gnuradio.org/index.php?title=File:OptionsSelectHierBlock.png)
The remaining properties will then change, showing the _Category_ : 
[![](https://wiki.gnuradio.org/images/thumb/2/2d/ShowGRCHierBlocksCategory.png/500px-ShowGRCHierBlocksCategory.png)](https://wiki.gnuradio.org/index.php?title=File:ShowGRCHierBlocksCategory.png)
The _Category_ is where the block can be found in the block library on the right hand of GRC. The hier block will be located under _GRC Hier Blocks_ , instead of _Core_ where the rest of the GNU Radio blocks are located. 
Save the flowgraph. 
## Variables vs Parameters
A _variable_ is different than a _parameter_ in GNU Radio. A _parameter_ creates an interface for the _hier block_ to accept a value from an external source, whereas a _variable_ only exists internally to the _hier block_ : 
[![](https://wiki.gnuradio.org/images/thumb/d/d5/HierBlockParameterVariable.png/500px-HierBlockParameterVariable.png)](https://wiki.gnuradio.org/index.php?title=File:HierBlockParameterVariable.png)
  
For example, the _samp_rate_ variable can only be accessed from within the _hier block_ : 
[![](https://wiki.gnuradio.org/images/thumb/2/25/HierBlockWithVariable.png/500px-HierBlockWithVariable.png)](https://wiki.gnuradio.org/index.php?title=File:HierBlockWithVariable.png)
  
The _samp_rate_ needs to be converted to a parameter so it can be updated from another block in the larger flowgraph. Delete the _samp_rate_ variable and add a _Parameter_ block into the GRC workspace: 
[![](https://wiki.gnuradio.org/images/thumb/6/60/AddParameterToHierBlock.png/500px-AddParameterToHierBlock.png)](https://wiki.gnuradio.org/index.php?title=File:AddParameterToHierBlock.png)
  
Edit the _Parameter_ properties: 
  * Id: _samp_rate_
  * Label: _Sample Rate_
  * Type: _Float_


[![](https://wiki.gnuradio.org/images/thumb/3/3e/EditParameterProperties.png/500px-EditParameterProperties.png)](https://wiki.gnuradio.org/index.php?title=File:EditParameterProperties.png)
  
Add a second _Parameter_ : 
  * Id: _frequency_
  * Label: _Frequency_
  * Type: _float_


[![](https://wiki.gnuradio.org/images/thumb/f/f1/EditFrequencyParameterProperties.png/500px-EditFrequencyParameterProperties.png)](https://wiki.gnuradio.org/index.php?title=File:EditFrequencyParameterProperties.png)
Add the _frequency_ parameter to the Signal Source _Frequency_ property: 
[![](https://wiki.gnuradio.org/images/thumb/e/e8/AddFrequencyToSignalSourceProperties.png/500px-AddFrequencyToSignalSourceProperties.png)](https://wiki.gnuradio.org/index.php?title=File:AddFrequencyToSignalSourceProperties.png)
The flowgraph should look like: 
[![](https://wiki.gnuradio.org/images/thumb/c/cb/HierBlockSampRateFrequencyParameters.png/500px-HierBlockSampRateFrequencyParameters.png)](https://wiki.gnuradio.org/index.php?title=File:HierBlockSampRateFrequencyParameters.png)
## Input and Output Ports
A _pad_ is used to specify input and output ports on a hier block. Add a _Pad Source_ and _Pad Sink_ to the flowgraph to act as the _in_ and _out_ ports: 
[![](https://wiki.gnuradio.org/images/thumb/5/58/AddPadSourcePadSink.png/500px-AddPadSourcePadSink.png)](https://wiki.gnuradio.org/index.php?title=File:AddPadSourcePadSink.png)
  

## Generate the Hier Block Code
Click _Generate the flow graph_ to create the _hier block_ source code: 
[![](https://wiki.gnuradio.org/images/thumb/7/7e/ClickGenerateFlowgraph.png/250px-ClickGenerateFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:ClickGenerateFlowgraph.png)
  
A Python _.py_ file and YAML _.yml_ file will be created. For GNU Radio v3.8 the files will be created in your home directory: 

```
/home/$USER/.grc_gnuradio/
```

[![](https://wiki.gnuradio.org/images/thumb/2/2f/HierBlockPyYml.png/500px-HierBlockPyYml.png)](https://wiki.gnuradio.org/index.php?title=File:HierBlockPyYml.png)
For GNU Radio v3.10, the files will be created in the directory where the _.grc_ file is saved. Please create the _.grc_gnuradio_ directory and copy the _.py_ and _.yml_ files there: 

```
$ mkdir /home/$USER/.grc_gnuradio
$ cp FrequencyShifter.block.yml /home/$USER/.grc_gnuradio/
$ cp FrequencyShifter.py /home/$USER/.grc_gnuradio/ 
```

GRC needs to update the internal list of the blocks before the _Frequency Shifter_ block can be used in a flowgraph. Click the _Reload Blocks_ button: 
[![](https://wiki.gnuradio.org/images/thumb/1/15/ClickReloadBlocks.png/250px-ClickReloadBlocks.png)](https://wiki.gnuradio.org/index.php?title=File:ClickReloadBlocks.png)
  
There is a new category _GRC Hier Blocks_ in the block library below _Core_ , and the _Frequency Shifter Block_ can be used in flowgraphs: 
[![](https://wiki.gnuradio.org/images/thumb/c/c8/CoreCategoryOnly.png/300px-CoreCategoryOnly.png)](https://wiki.gnuradio.org/index.php?title=File:CoreCategoryOnly.png)
[![](https://wiki.gnuradio.org/images/thumb/e/e4/GRCHierBlocksUpdated.png/300px-GRCHierBlocksUpdated.png)](https://wiki.gnuradio.org/index.php?title=File:GRCHierBlocksUpdated.png)
## Using the Hier Block
The hier block can now be used in a flowgraph. Return the starting flowgraph and delete the _Signal Source_ and _Multiply_ blocks: 
[![](https://wiki.gnuradio.org/images/thumb/2/24/DeleteSignalSourceMultiplyBlock.png/700px-DeleteSignalSourceMultiplyBlock.png)](https://wiki.gnuradio.org/index.php?title=File:DeleteSignalSourceMultiplyBlock.png)
  
Add the _Frequency Shifter Block_ to the workspace and connect it to the rest of the flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/2/2d/ConnectFrequencyShifterBlock.png/700px-ConnectFrequencyShifterBlock.png)](https://wiki.gnuradio.org/index.php?title=File:ConnectFrequencyShifterBlock.png)
  
Edit the _Frequency Shifter Block_ properties by adding the _samp_rate_ and _frequency_ variables: 
[![](https://wiki.gnuradio.org/images/thumb/2/24/EditFrequencyShifterProperties.png/500px-EditFrequencyShifterProperties.png)](https://wiki.gnuradio.org/index.php?title=File:EditFrequencyShifterProperties.png)
  
Running the flowgraph will bring up the _QT GUI Frequency Sink_ window with the _QT QUI Range_ slider: 
[![](https://wiki.gnuradio.org/images/thumb/4/48/HierBlockFreqSink.png/500px-HierBlockFreqSink.png)](https://wiki.gnuradio.org/index.php?title=File:HierBlockFreqSink.png)
  
Dragging the _frequency_ slider will pass the value through the _Frequency Shifter Block_ parameter causing the signal's center frequency to be modified: 
[![](https://wiki.gnuradio.org/images/thumb/e/e6/HierBlockFrequencyShift.png/500px-HierBlockFrequencyShift.png)](https://wiki.gnuradio.org/index.php?title=File:HierBlockFrequencyShift.png)
## Deleting a Hier Block
A hier block can be cleared from GRC memory by removing the files from `/home/$USER/.grc_gnuradio`. 
In a terminal, move to the `.grc_gnuradio` directory: 

```
cd /home/$USER/.grc_gnuradio
```

Then remove the files. 
Warning! The `rm` command cannot be undone! 
For GNU Radio v3.8 the delete command is: 

```
rm FrequencyShifter.py FrequencyShifter.py.block.yml
```

[![](https://wiki.gnuradio.org/images/thumb/3/38/DeleteHierBlockCommand.png/700px-DeleteHierBlockCommand.png)](https://wiki.gnuradio.org/index.php?title=File:DeleteHierBlockCommand.png)
For GNU Radio v3.10 the delete command is: 

```
rm FrequencyShifter.py FrequencyShifter.block.yml
```

Click the _Reload Blocks_ button to update GRC's memory of blocks, clearing it of the _Frequency Shifter_ block: 
[![](https://wiki.gnuradio.org/images/1/15/ClickReloadBlocks.png)](https://wiki.gnuradio.org/index.php?title=File:ClickReloadBlocks.png)
  
The _GRC Hier Blocks_ category is deleted and only the _Core_ blocks remain: 
[![](https://wiki.gnuradio.org/images/thumb/c/c8/CoreCategoryOnly.png/350px-CoreCategoryOnly.png)](https://wiki.gnuradio.org/index.php?title=File:CoreCategoryOnly.png)
  
The next tutorial, [Creating Your First Block](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block "Creating Your First Block"), describes how to use the _Embedded Python Block_ to create a signal processing block in GNU Radio. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters&oldid=13128](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters&oldid=13128)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Hier+Blocks+and+Parameters "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Hier_Blocks_and_Parameters&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters)
  * [View source](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Hier_Blocks_and_Parameters "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Hier_Blocks_and_Parameters "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters&oldid=13128 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters&action=info "More information about this page")


  * This page was last edited on 18 May 2023, at 16:08.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


