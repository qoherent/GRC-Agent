# Python Block Tags
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags#searchInput)  
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
  4. Python Block Tags

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
This tutorial demonstrates how to create two _Embedded Python Blocks_ for detecting when the input signal crosses the threshold and writing a tag for it and then reading the tag in a separate block and updating the output with the time since the last detection. 
The previous tutorial, [Python Block Message Passing](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing "Python Block Message Passing") demonstrates how to send and receive messages using the _Embedded Python Block_. The next tutorial, [Low Pass Filter Example](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example "Low Pass Filter Example"), demonstrates how to use filtering blocks in GNU Radio. 
## Contents
  * [1 Tags Overview](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags#Tags_Overview)
  * [2 Creating Test Signal](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags#Creating_Test_Signal)
  * [3 Threshold Detector: Defining the Block](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags#Threshold_Detector:_Defining_the_Block)
  * [4 Threshold Detector: Writing Tags](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags#Threshold_Detector:_Writing_Tags)
  * [5 Detection Counter: Defining the Block](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags#Detection_Counter:_Defining_the_Block)
  * [6 Detection Counter: Reading Tags](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags#Detection_Counter:_Reading_Tags)
  * [7 Tag Propagation](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags#Tag_Propagation)


## Tags Overview
Tags are a way to convey information alongside digitized RF samples in a time-synchronous fashion. Tags are particularly useful when downstream blocks need to know upon which sample the receiver was tuned to a new frequency, or for including timestamps with specific samples. 
Where [messages](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing "Python Block Message Passing") convey information in an asynchronous fashion with no clock-based time guarantee, tags are information which are associated with specific RF samples. Tags ride alongside digitized RF samples in data streams and vectors, including _Complex Float 32_ , _Float 32_ , _Byte_ and all of the other formats. 
Tags are added using the line: 

```
self.add_item_tag(outputPortNumber, absoluteIndex, key, value)
```

The _outputPortNumber_ determines which output stream the tag is added to. The _absoluteIndex_ is the sample index the tag is added to. The flowgraph counts each sample and the first sample produced is at absolute sample index _0_. The _key_ is a PMT type containing the name of the variable to be stored and _value_ is another PMT type that contains the information to be stored. 
[![](https://wiki.gnuradio.org/images/thumb/6/68/AddItemTag.png/700px-AddItemTag.png)](https://wiki.gnuradio.org/index.php?title=File:AddItemTag.png)
Reading tags can be done with the function: 

```
tagTuple = self.get_tags_in_window(inputPortNumber, relativeIndexStart, relativeIndexStop))
```

Reading tags in a _window_ reads them based on the _relative index_ within the current _input_items_ vector. The simplest way to get all of the tags corresponding to the current _input_items_ samples is with the function call: 

```
tagTuple = self.get_tags_in_window(inputPortNumber, 0, len(input_items[inputPortNumber])))
```

[![](https://wiki.gnuradio.org/images/thumb/a/a3/GetTagsInWindow.png/900px-GetTagsInWindow.png)](https://wiki.gnuradio.org/index.php?title=File:GetTagsInWindow.png)
  
More information about tags can be found here: [Stream Tags](https://wiki.gnuradio.org/index.php?title=Stream_Tags "Stream Tags")
## Creating Test Signal
A test signal is needed. Drag in the blocks for the input signal: 
  * _GLFSR Source_
  * _Repeat_
  * _Multiply Const_
  * _Add Const_
  * _Single Pole IIR Filter_
  * _Throttle_
  * _QT GUI Time Sink_


Change the following parameters: 
  * _GLFSR Source_ , Degree: 32
  * _Repeat_ , Interpolation: 128
  * _Multiply Const_ , Constant: 0.5
  * _Add Const_ , Constant: 0.5
  * _Single Pole IIR Filter_ , Alpha: 0.05
  * _QT GUI Time Sink_
    * Number of Points: 2048
    * Autoscale: Yes
  * _samp_rate_ Variable, Value: 3200


Change all of the blocks to be _Float_ input and output. Connect them all according to the following flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/8/89/TestSignalFlowgraph.png/900px-TestSignalFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:TestSignalFlowgraph.png)
Run the flowgraph. A pseudo-randomized sequence of filtered _0_ s and _1_ s is generated: 
[![](https://wiki.gnuradio.org/images/thumb/0/0e/TestSignalTimeSink.png/700px-TestSignalTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:TestSignalTimeSink.png)
## Threshold Detector: Defining the Block
Drag in a _Python Block_ and double-click it to edit the source code. Recall that _Embedded Python Blocks_ use indentation in multiples of 4 spaces. 
Change the _example_param_ variable name and add a new parameter _report_period_ : 

```
def __init__(self, threshold=1.0, report_period=128):
```

Update the block name: 

```
name='Threshold Detector',
```

Change the input and output types to _Float_ : 

```
in_sig=[np.float32],
out_sig=[np.float32]
```

Change the variable name from _self.example_param_ : 

```
self.threshold = threshold
self.report_period = report_period
```

Remove the multiplication by _self.example_param_ : 

```
output_items[0][:] = input_items[0]
```

The code looks like the following: 
[![](https://wiki.gnuradio.org/images/thumb/3/37/DefineBlockThresholdDetector.png/800px-DefineBlockThresholdDetector.png)](https://wiki.gnuradio.org/index.php?title=File:DefineBlockThresholdDetector.png)
Save the code (CTRL + S) and return to GRC. The block looks like the following: 
[![](https://wiki.gnuradio.org/images/8/88/ThresholdDetectorBlockDefined.png)](https://wiki.gnuradio.org/index.php?title=File:ThresholdDetectorBlockDefined.png)
If the block did not update properly there may be a problem with the Python syntax. Double-click the _Embedded Python Block_ to view any potential syntax errors. The following image gives an example of where the synax errors are located: 
[![](https://wiki.gnuradio.org/images/thumb/a/a1/ThresholdDetectorPythonError.png/500px-ThresholdDetectorPythonError.png)](https://wiki.gnuradio.org/index.php?title=File:ThresholdDetectorPythonError.png)
Add a _Virtual Sink_ and _Virtual Source_ block to the flowgraph. Change the following block properties: 
  * _Threshold Detector_
    * Threshold: 0.75
    * Report Period: 128
  * _Virtual Sink_ , Stream ID: signal
  * _Virtual Source_ , Stream ID: signal
  * _QT GUI Time Sink_ , name: "Threshold Detector"


Connect the blocks according to the flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/1/17/ThresholdDetectorConnected.png/900px-ThresholdDetectorConnected.png)](https://wiki.gnuradio.org/index.php?title=File:ThresholdDetectorConnected.png)
## Threshold Detector: Writing Tags
The internals of the _Threshold Detector_ need to be written. Recall that _Embedded Python Blocks_ use indentation in multiples of 4 spaces. 
Import the _pmt_ library: 

```
import pmt
```

Add two new variables, _self.timer_ and _self.readyForTag_ under the ___init__()_ function: 

```
self.timer = 0
self.readyForTag = True
```

[![](https://wiki.gnuradio.org/images/thumb/1/10/AddTwoVariables.png/700px-AddTwoVariables.png)](https://wiki.gnuradio.org/index.php?title=File:AddTwoVariables.png)
The _work()_ function needs to be modified. Create a for loop to iterate through all of the input samples: 

```
for index in range(len(input_items[0])):
```

Three sections of code need to be written. The first block writes the amplitude level into a tag named _detect_ once the threshold is met or exceeded. The tag is only written if the _self.readyForTag_ state variable is True. Once a tag is written the state variable _self.readyForTag_ is set to False. 

```
# write the tag
if (input_items[0][index] >= self.threshold and self.readyForTag == True):
    # define the key as 'detect'
    key = pmt.intern('detect')
    # get the detection value
    value = pmt.from_float(np.round(float(input_items[0][index]),2))
    # tag index to be written
    writeIndex = self.nitems_written(0) + index
    # add the tag object (key, value pair)
    self.add_item_tag(0, writeIndex, key, value )
    # tag has been written, set state
    self.readyForTag = False
```

The next block of code is used to run the timer. The timer increases by 1 for each input sample as long as _self.readyForTag_ is False: 

```
# increase the timer by 1
if (self.readyForTag == False):
    self.timer = self.timer + 1
```

The third block of code controls the state variable _self.readyForTag_. Once _self.timer_ reaches the maximum value the timer is reset and the state variable _self.readyForTag_ is set to True: 

```
# set flag to write 
if (self.timer >= self.report_period):
    # reset timer
    self.timer = 0
    # reset state once timer hits max value
    self.readyForTag = True
```

[![](https://wiki.gnuradio.org/images/thumb/7/7b/WriteDetectionTags.png/700px-WriteDetectionTags.png)](https://wiki.gnuradio.org/index.php?title=File:WriteDetectionTags.png)
  
Run the flowgraph. Tags are displayed in the _QT GUI Time Sink_ : 
[![](https://wiki.gnuradio.org/images/thumb/9/9b/TimeSinkTags.png/700px-TimeSinkTags.png)](https://wiki.gnuradio.org/index.php?title=File:TimeSinkTags.png)
## Detection Counter: Defining the Block
A new _Embedded Python Block_ is created to read the tags, count the number of samples since the last tag, and produce that number as an output. 
Drag and drop a _new_ _Python Block_ into the GRC workspace. Do _not_ copy and paste the existing python block, it only creates a second copy of _Threshold Detector_. 
Double-click on the _Embedded Python Block_ and edit the code. 
Remove the _self.example_param_ parameter: 

```
def __init__(self):
```

Change the name: 

```
name='Detection Counter',
```

Make the input and output ports _Floats_ : 

```
in_sig=[np.float32],
out_sig=[np.float32]
```

Remove the line _self.example_param = example_param_ and the multiplication by _self.example_param_ : 

```
output_items[0][:] = input_items[0]
```

[![](https://wiki.gnuradio.org/images/thumb/f/fe/DefineBlockDetectionCounter.png/700px-DefineBlockDetectionCounter.png)](https://wiki.gnuradio.org/index.php?title=File:DefineBlockDetectionCounter.png)
  
Save the code (CTRL + S). Add another _QT GUI Time Sink_ and change the properties: 
  * _QT GUI Time Sink_
    * Name: "Detection Counter"
    * Number of Points: 2048
    * Autoscale: Yes


  
Connect the _Detection Counter_ block after the _Threshold Detection_. 
[![](https://wiki.gnuradio.org/images/thumb/6/67/AddedDetectionCounterToFlowgraph.png/900px-AddedDetectionCounterToFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:AddedDetectionCounterToFlowgraph.png)
## Detection Counter: Reading Tags
The _Detection Counter_ block needs to be modified to read the tags. 
Import the _pmt_ library: 

```
import pmt
```

Add a new variable under ___init__()_ : 

```
self.samplesSinceDetection = 0
```

[![](https://wiki.gnuradio.org/images/thumb/2/2e/AddVariableDetectionCounter.png/700px-AddVariableDetectionCounter.png)](https://wiki.gnuradio.org/index.php?title=File:AddVariableDetectionCounter.png)
Modify the _work()_ function to read the tags: 

```
# get all tags associated with input_items[0]
tagTuple = self.get_tags_in_window(0, 0, len(input_items[0]))
```

Loop through all of the tags, calculate the relative offset of those with the key equal to _detect_ and store it in a list: 

```
# declare a list
relativeOffsetList = []

# loop through all 'detect' tags and store their relative offset
for tag in tagTuple:
    if (pmt.to_python(tag.key) == 'detect'):
        relativeOffsetList.append( tag.offset - self.nitems_read(0) )
```

Sort the offsets from lowest to highest: 

```
# sort list of relative offsets
relativeOffsetList.sort()
```

Loop through all of the output samples: 

```
# loop through all output samples
for index in range(len(output_items[0])):
```

Produce an output sample with the current count of samples since the last _detect_ tag: 

```
# output is now samples since detection counter
output_items[0][index] = self.samplesSinceDetection
```

If the current output sample index is greater than or equal to the index of the current _detect_ tag, then remove the offset value from the list and reset the sample counter _self.samplesSinceDetection._ Otherwise, increase the sample counter by 1. 

```
# make sure the list is not-empty, and if the current input sample
# is greater than or equal to the next 
if (len(relativeOffsetList) > 0 and index >= relativeOffsetList[0]):
    # clear the offset
    relativeOffsetList.pop(0)
    # reset the output counter
    self.samplesSinceDetection = 0
else:
    # a detect tag has not been seen, so continue to increase
    # the output counter
    self.samplesSinceDetection = self.samplesSinceDetection + 1
```

Remove the output assignment: 

```
output_items[0][:] = input_items[0]
```

The work function looks like: 
[![](https://wiki.gnuradio.org/images/thumb/0/0f/DetectionCounterWorkFunction.png/700px-DetectionCounterWorkFunction.png)](https://wiki.gnuradio.org/index.php?title=File:DetectionCounterWorkFunction.png)
  
Save the code (CTRL + S). Run the flowgraph. The output looks like the following: 
[![](https://wiki.gnuradio.org/images/thumb/0/07/DetectionCounterTimeSink.png/700px-DetectionCounterTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:DetectionCounterTimeSink.png)
  
Notice that all of the tags from the input of _Detection Counter_ are automatically conveyed to its output. 
  

## Tag Propagation
By default all input tags are propagated to all output tags. It can be useful to reduce or completely remove tags from certain streams. The _Tag Gate_ block does exactly that. Connect the _Tag Gate_ after the _Detection Counter_ block: 
[![](https://wiki.gnuradio.org/images/thumb/7/7a/TagGateFlowgraph.png/900px-TagGateFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:TagGateFlowgraph.png)
  
Run the flowgraph. The tags are removed from the _QT GUI Time Sink_ : 
[![](https://wiki.gnuradio.org/images/thumb/e/e8/TagGateTimeSink.png/700px-TagGateTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:TagGateTimeSink.png)
  

The next tutorial, [Low Pass Filter Example](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example "Low Pass Filter Example"), demonstrates how to use filtering blocks in GNU Radio. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Python_Block_Tags&oldid=12879](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags&oldid=12879)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Python+Block+Tags "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Python_Block_Tags&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags)
  * [View source](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Python_Block_Tags "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Python_Block_Tags "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags&oldid=12879 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags&action=info "More information about this page")


  * This page was last edited on 19 January 2023, at 16:30.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


