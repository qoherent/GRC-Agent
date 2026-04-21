# Runtime Updating Variables
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables#searchInput)  
|  **Beginner Tutorials** Introducing GNU Radio 
  1. [What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "What Is GNU Radio")
  2. [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR")
  3. [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph")

Flowgraph Fundamentals 
  1. [Python Variables in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "Python Variables in GRC")
  2. [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs")
  3. Runtime Updating Variables
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
This tutorial describes how to update variables while a flowgraph is running using QT GUI Widgets. 
Please review the previous tutorial, [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs"), for an introduction to variables. The next tutorial, [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types"), discusses data types and representing complex and real signals in GNU Radio. 
## QT GUI Range
The GNU Radio block library comes with QT GUI widgets. The widgets allow interaction and modification to a flowgraph while running. The _QT GUI Range_ widget creates a slider bar that can be used to update a variable. 
Search for _range_ in the block library: 
[![](https://wiki.gnuradio.org/images/thumb/b/b4/SearchQTGUIRange.png/700px-SearchQTGUIRange.png)](https://wiki.gnuradio.org/index.php?title=File:SearchQTGUIRange.png)
Drag and drop the _QT GUI Range_ block into the workspace: 
[![](https://wiki.gnuradio.org/images/thumb/f/ff/DragDropQTGUIRange.png/600px-DragDropQTGUIRange.png)](https://wiki.gnuradio.org/index.php?title=File:DragDropQTGUIRange.png)
  
The _QT GUI Range_ works like a variable block. The default parameters for the _QT GUI Range_ need to be set. Double-click on the _QT GUI Range_ block to edit the properties. The _QT GUI Range_ block will replace the _frequency_ variable, so first change the _Id_ field to _frequency_. 
The _Default Value_ is the value when the flowgraph starts. Set the _Default Value_ to 0. The _Start_ and _Stop_ are the start and stop values of the slider. Enter _-samp_rate/2_ as the start value and _samp_rate/2_ as the stop value. The _Step_ value is the resolution of the slider. In this example the _Step_ is set to 100 Hz: 
[![](https://wiki.gnuradio.org/images/thumb/d/d0/SetRangeProperties.png/500px-SetRangeProperties.png)](https://wiki.gnuradio.org/index.php?title=File:SetRangeProperties.png)
An error message is displayed: 

```
ID "frequency" is not unique.
```

The error message is displayed because there a variable block and the QT GUI Range are both using the name _frequency_. This problem will be addressed shortly. Click _OK_ to save the properties. 
Right click on the variable block and select _Disable_ , or press _D_ on the keyboard: 
[![](https://wiki.gnuradio.org/images/thumb/e/e8/DisableVariableBlock.png/700px-DisableVariableBlock.png)](https://wiki.gnuradio.org/index.php?title=File:DisableVariableBlock.png)
The block is now ignored and the error is resolved. 
[![](https://wiki.gnuradio.org/images/thumb/6/6f/VariableBlockDisabled.png/700px-VariableBlockDisabled.png)](https://wiki.gnuradio.org/index.php?title=File:VariableBlockDisabled.png)
  
Run the flowgraph by clicking the arrow or _Play_ button: 
[![](https://wiki.gnuradio.org/images/thumb/1/1a/RunFlowgraphButton.png/300px-RunFlowgraphButton.png)](https://wiki.gnuradio.org/index.php?title=File:RunFlowgraphButton.png)
  
The flowgraph starts with a frequency of 0, the default value entered into the _QT GUI Range_ block: 
[![](https://wiki.gnuradio.org/images/thumb/6/66/QTGUIRangeDefaultValue.png/700px-QTGUIRangeDefaultValue.png)](https://wiki.gnuradio.org/index.php?title=File:QTGUIRangeDefaultValue.png)
  
The frequency parameter can then be updated by: 
  1. Dragging the slider bar
  2. Entering a value
  3. Click up or down arrows


[![](https://wiki.gnuradio.org/images/thumb/5/5e/QTGUIRangeDragSlider.png/700px-QTGUIRangeDragSlider.png)](https://wiki.gnuradio.org/index.php?title=File:QTGUIRangeDragSlider.png)
The frequency has been updated to -5000 which is reflected in **orange** in the frequency spectrum plot. 
## QT GUI Chooser
The _QT GUI Chooser_ creates a drop-down menu of options for a variable. Navigate the block library: _Core_ , _GUI Widgets_ , _QT_ , and drag and drop _QT GUI Chooser_ into the workspace. 
[![](https://wiki.gnuradio.org/images/thumb/f/f5/QTGUIChooserLibraryBlock.png/700px-QTGUIChooserLibraryBlock.png)](https://wiki.gnuradio.org/index.php?title=File:QTGUIChooserLibraryBlock.png)
  
Update the default parameters for the _Chooser_ block. The _Chooser_ creates a list of options to select from while the flowgraph is running. In this example three frequencies are used: 0, 1000 and -2000. Update the following properties in the _Chooser_ block: 
  1. _Id_ : frequency
  2. _Default Option_ : 0
  3. _Option 0_ : 0
  4. _Label 0_ : Frequency: 0
  5. _Option 1_ : 1000
  6. _Label 1_ : Frequency: 1000
  7. _Option 2_ : -2000
  8. _Label 2_ : Frequency: -2000


The _Option_ field is the value of the variable, _Label_ is a text description that is displayed in the drop-down menu. The image shows an example of the _Option_ being highlighted in **orange** and the _Label_ being highlighted in **red** : 
[![](https://wiki.gnuradio.org/images/thumb/2/20/QTGUIChooserUpdateProperties.png/500px-QTGUIChooserUpdateProperties.png)](https://wiki.gnuradio.org/index.php?title=File:QTGUIChooserUpdateProperties.png)
  
Click _OK_ to save the parameters. Running the flowgraph will use the default value _Frequency: 0_ when starting. The drop-down box in the upper left hand corner shows that frequency _0_ has been selected. The time domain and frequency domain both show a signal with frequency _0_ : 
[![](https://wiki.gnuradio.org/images/thumb/4/4d/QTGUIChooserDefaultValue.png/700px-QTGUIChooserDefaultValue.png)](https://wiki.gnuradio.org/index.php?title=File:QTGUIChooserDefaultValue.png)
  
The values are selected by clicking on the drop-down menu. Click on _Frequency: 1000_ : 
[![](https://wiki.gnuradio.org/images/thumb/d/d3/SelectFromQTGUIChooser.png/700px-SelectFromQTGUIChooser.png)](https://wiki.gnuradio.org/index.php?title=File:SelectFromQTGUIChooser.png)
  
The updated frequency is seen in the frequency spectrum: 
[![](https://wiki.gnuradio.org/images/thumb/e/e9/QTGUIChooserOptionSelected.png/700px-QTGUIChooserOptionSelected.png)](https://wiki.gnuradio.org/index.php?title=File:QTGUIChooserOptionSelected.png)
  
The next tutorial, [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types"), discusses data types and representing complex and real signals in GNU Radio. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables&oldid=11936](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables&oldid=11936)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Runtime+Updating+Variables "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Runtime_Updating_Variables&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables)
  * [View source](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Runtime_Updating_Variables "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Runtime_Updating_Variables "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables&oldid=11936 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables&action=info "More information about this page")


  * This page was last edited on 14 March 2022, at 19:20.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


