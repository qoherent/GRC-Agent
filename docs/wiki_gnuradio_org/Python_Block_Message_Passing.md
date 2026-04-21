# Python Block Message Passing
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#searchInput)  
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
  3. Python Block Message Passing
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
This tutorial describes how to read and write messages using the _Embedded Python Block_. 
The previous tutorial, [Python Block with Vectors](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors "Python Block with Vectors"), demonstrates how to write an _Embedded Python Block_ with vector inputs and outputs. The next tutorial, [Python Block Tags](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags "Python Block Tags"), describes how to read and write tags in a Python block. 
## Contents
  * [1 Message Overview](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#Message_Overview)
  * [2 Flowgraph Overview](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#Flowgraph_Overview)
  * [3 Multiplexer: Defining The Block](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#Multiplexer:_Defining_The_Block)
  * [4 Multiplexer: Defining Message Input Port](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#Multiplexer:_Defining_Message_Input_Port)
  * [5 Multiplexer: Creating Message Handler](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#Multiplexer:_Creating_Message_Handler)
  * [6 Multiplexer: Using a Message in _work()_](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#Multiplexer:_Using_a_Message_in_work\(\))
  * [7 Selector Control: Defining The Block](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#Selector_Control:_Defining_The_Block)
  * [8 Selector Control: Defining Message Output Port](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#Selector_Control:_Defining_Message_Output_Port)
  * [9 Selector Control: Sending a message in _work()_](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#Selector_Control:_Sending_a_message_in_work\(\))
  * [10 Final Flowgraph](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing#Final_Flowgraph)


## Message Overview
Messages are an asynchronous way to send information between blocks. Messages are good at conveying control data, maintaining a consistent state across blocks and providing some forms of non-data feedback to blocks in a flowgraph. 
Messages have a couple of unique properties: 
  * There is no sample-clock based guarantee when messages will arrive
  * Messages are not associated with a specific sample like a tag
  * Message input and output ports do not have to be connected in GRC
  * Message ports use the [Polymorphic Type (PMT)](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\) "Polymorphic Types \(PMTs\)")


Message ports are denoted by a grey color and their connections are distinguished by dashed lines: 
[![](https://wiki.gnuradio.org/images/thumb/e/ee/MessageBlockExample.png/500px-MessageBlockExample.png)](https://wiki.gnuradio.org/index.php?title=File:MessageBlockExample.png)
More information on message passing with PMTs can be found here: [Message Passing](https://wiki.gnuradio.org/index.php?title=Message_Passing "Message Passing")
## Flowgraph Overview
The following flowgraph demonstrates how to: 
  * Add message sending and receiving ports to Python blocks
  * Send messages
  * Receive and handle messages
  * Adapt block behavior in the _work()_ function based on a received messages


Two custom _Embedded Python Blocks_ are to be created to: 
  * Select, or [multiplex](https://en.wikipedia.org/wiki/Multiplexer), one of two input signals based on a received message
  * Count the number of samples and send a message to the multiplexing block to switch inputs


This tutorial assumes you have already created at least one _Embedded Python Block_. If not, please complete the tutorial [Creating Your First Block](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block "Creating Your First Block") before moving on. 
Start by adding the following blocks to the flowgraph and connecting them: 
  * _Noise Source_
  * _Signal Source_
  * _Python Block_
  * _Throttle_
  * _QT GUI Time Sink_


[![](https://wiki.gnuradio.org/images/thumb/5/52/StartingFlowgraphMessagePassing.png/800px-StartingFlowgraphMessagePassing.png)](https://wiki.gnuradio.org/index.php?title=File:StartingFlowgraphMessagePassing.png)
## Multiplexer: Defining The Block
Double-click the _Embedded Python Block_ and open the source in the editor: 
[![](https://wiki.gnuradio.org/images/thumb/0/0b/EditMultiplexerProperties.png/700px-EditMultiplexerProperties.png)](https://wiki.gnuradio.org/index.php?title=File:EditMultiplexerProperties.png)
  
The _example_param_ is not needed, so remove the variable _example_param_ from the signature of the ___init__()_ function: 

```
def __init__(self):  # only default arguments here
```

and delete the line: 

```
self.example_param = example_param
```

Change the name of the block to _Multiplexer_ : 

```
name='Multiplexer',
```

Add a second input to the block: 

```
in_sig=[np.complex64, np.complex64],
```

Delete the multiplication by _example_param_ : 

```
output_items[0][:] = input_items[0]
```

When all of these changes are made, the code should look as follows: 
[![](https://wiki.gnuradio.org/images/thumb/4/43/AddSecondInputEmbeddedPythonBlock.png/800px-AddSecondInputEmbeddedPythonBlock.png)](https://wiki.gnuradio.org/index.php?title=File:AddSecondInputEmbeddedPythonBlock.png)
  
Recall that Python requires the [proper indentation](https://www.w3schools.com/python/gloss_python_indentation.asp). By default, the _Embedded Python Block_ uses indentation of in multiples of 4 spaces. Mixing tabs and spaces raises a syntax error: 
[![](https://wiki.gnuradio.org/images/thumb/2/2e/TabsSpacesError.png/500px-TabsSpacesError.png)](https://wiki.gnuradio.org/index.php?title=File:TabsSpacesError.png)
  
Save the code and return back to GRC. Note how the name of the block has changed and the block now has two inputs. Connect the _Noise Source_ and _Signal Source_ to the two inputs: 
[![](https://wiki.gnuradio.org/images/thumb/1/17/MultiplexerWithTwoInputs.png/800px-MultiplexerWithTwoInputs.png)](https://wiki.gnuradio.org/index.php?title=File:MultiplexerWithTwoInputs.png)
## Multiplexer: Defining Message Input Port
Return to the code editor. An input message port needs to be added. Create a variable to store the message port name: 

```
self.selectPortName = 'selectPort'
```

Add a line to create, or _register_ , the message input port: 

```
self.message_port_register_in(pmt.intern(self.selectPortName))
```

Add a line to connect the input port with a message handler. 

```
self.set_msg_handler(pmt.intern(self.selectPortName), self.handle_msg)
```

[![](https://wiki.gnuradio.org/images/thumb/1/19/AddMessageHandler.png/700px-AddMessageHandler.png)](https://wiki.gnuradio.org/index.php?title=File:AddMessageHandler.png)
  
Save the code. Notice that errors are listed in the properties of the _Embedded Python Block_ : 
[![](https://wiki.gnuradio.org/images/thumb/0/0f/CodeEditorErrorExample.png/500px-CodeEditorErrorExample.png)](https://wiki.gnuradio.org/index.php?title=File:CodeEditorErrorExample.png)
  
This error says that the _pmt_ library needs to be imported. Return to the code editor and add the proper _import_ statement: 

```
import pmt
```

[![](https://wiki.gnuradio.org/images/thumb/2/26/ImportPMT.png/600px-ImportPMT.png)](https://wiki.gnuradio.org/index.php?title=File:ImportPMT.png)
## Multiplexer: Creating Message Handler
A message handler is the function which is called when a message is received. 
The message handler function has to be defined. This message handler switches between the two input ports based on the received message. The received message is a Boolean that is True or False. Define a new variable under ___init__()_ which is the input selector, 

```
self.selector = True
```

Define the _handle_msg()_ function: 

```
def handle_msg(self, msg):
    self.selector = pmt.to_bool(msg)
```

The function _pmt.to_bool()_ takes the message PMT and then converts the data type into Python's Boolean data type. PMTs are used in message passing to abstract data types. For example, messages can be used to send and receive strings, floats, integers and even lists. More information about PMTs can be found on the [Polymorphic Types (PMTs) wiki page](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\) "Polymorphic Types \(PMTs\)"). 
[![](https://wiki.gnuradio.org/images/thumb/e/ee/AddSelectorAttribute.png/700px-AddSelectorAttribute.png)](https://wiki.gnuradio.org/index.php?title=File:AddSelectorAttribute.png)
## Multiplexer: Using a Message in _work()_
The external interface of the multiplexer is complete. The block's _work()_ function is modified to add the multiplexing operation. Add the following code to the _work()_ function: 

```
if (self.selector):
    output_items[0][:] = input_items[0]
else:
    output_items[0][:] = input_items[1]
```

[![](https://wiki.gnuradio.org/images/thumb/f/f4/MultiplexerWorkFunction.png/700px-MultiplexerWorkFunction.png)](https://wiki.gnuradio.org/index.php?title=File:MultiplexerWorkFunction.png)
  
The multiplexer block selects port _0_ if _self.selector = True_ and port _1_ if _self.selector = False_. The default value of _self.selector_ is defined in the ___init__()_ function. 
Save the code (CTRL+S) and return to GRC. The _Multiplexer_ block has a message port _selectPort_ : 
[![](https://wiki.gnuradio.org/images/thumb/8/8d/CompletedMultiplexerBlock.png/800px-CompletedMultiplexerBlock.png)](https://wiki.gnuradio.org/index.php?title=File:CompletedMultiplexerBlock.png)
  
Run the flowgraph to make sure everything is correct before moving on. As mentioned in the introduction, a message port does not have to be connected for a flowgraph to run. Because the default value of _self.selector_ is _True_ , the multiplexer's _work()_ function will select port _0_ and send that to the output. The _QT GUI Time Sink_ displays noise: 
[![](https://wiki.gnuradio.org/images/thumb/3/36/MultiplexerNoiseInput.png/700px-MultiplexerNoiseInput.png)](https://wiki.gnuradio.org/index.php?title=File:MultiplexerNoiseInput.png)
## Selector Control: Defining The Block
Another _Embedded Python Block_ is used to count the number of samples it has received and then send a control message to the multiplexer block in order to toggle the selector. 
Start by adding a new _Python Block_ to the flowgraph, in between the _Multiplexer_ and _Throttle_. 
Warning! Drag and drop a _NEW_ _Python Block_ from the block library! Do not copy and paste the existing _Multiplexer_ block, it only creates a second copy of that block. 
[![](https://wiki.gnuradio.org/images/thumb/e/e6/AddPythonBlockToFlowgraph.png/800px-AddPythonBlockToFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:AddPythonBlockToFlowgraph.png)
  
Edit the code for the _Embedded Python Block_. Change the parameter _example_param_ in the ___init__()_ function: 

```
def __init__(self, Num_Samples_To_Count=128):
```

Change the name of the block: 

```
name='Selector Control',
```

Store the _Num_Samples_To_Count_ as a private variable: 

```
self.Num_Samples_To_Count = Num_Samples_To_Count
```

Remove the _example_param_ multiplication in the _work()_ function: 
[![](https://wiki.gnuradio.org/images/thumb/6/6a/ReplaceExampleParamWithNumSamples.png/700px-ReplaceExampleParamWithNumSamples.png)](https://wiki.gnuradio.org/index.php?title=File:ReplaceExampleParamWithNumSamples.png)
## Selector Control: Defining Message Output Port
Import the _pmt_ library: 

```
import pmt
```

Create a variable (_self.portName_) in the ___init__()_ function containing the name of the output port as a string, _messageOutput_ : 

```
self.portName = 'messageOutput'
```

  
A message port is created, or _registered_ , by adding the following line in the ___init__()_ function: 

```
self.message_port_register_out(pmt.intern(self.portName))
```

[![](https://wiki.gnuradio.org/images/thumb/5/53/AddControlSelectorMessageOutput.png/800px-AddControlSelectorMessageOutput.png)](https://wiki.gnuradio.org/index.php?title=File:AddControlSelectorMessageOutput.png)
  
Save the code and return to GRC. The _Selector Control_ block has a message output port: 
[![](https://wiki.gnuradio.org/images/thumb/6/61/MessageOutputSelectorControlBlock.png/800px-MessageOutputSelectorControlBlock.png)](https://wiki.gnuradio.org/index.php?title=File:MessageOutputSelectorControlBlock.png)
## Selector Control: Sending a message in _work()_
A message handler does not need to be defined for an output port. However, the _work()_ function needs to be modified to create the logic for sending messages. 
Start by creating two variables in ___init__()_ : 

```
self.state = True
self.counter = 0
```

Add the line to increase the number of counted samples for each call to _work()_ : 

```
self.counter = self.counter + len(output_items[0])
```

[![](https://wiki.gnuradio.org/images/thumb/5/55/DefineCounterInBlock.png/800px-DefineCounterInBlock.png)](https://wiki.gnuradio.org/index.php?title=File:DefineCounterInBlock.png)
  
Add the logic to send a message once the counter is exceeded: 

```
if (self.counter > self.Num_Samples_To_Count):
    PMT_msg = pmt.from_bool(self.state)
    self.message_port_pub(pmt.intern(self.portName), PMT_msg)
    self.state = not(self.state)
    self.counter = 0
```

The logic translates the Python Boolean data type of _self.state_ into a PMT using the _pmt.from_bool()_ function call and then sends, or _publishes_ , the message on the output message port. The _self.state_ variable is toggled to its opposite value and the counter is reset. 
[![](https://wiki.gnuradio.org/images/thumb/a/aa/CompletedSelectorControlBlock.png/800px-CompletedSelectorControlBlock.png)](https://wiki.gnuradio.org/index.php?title=File:CompletedSelectorControlBlock.png)
  
Save the code and return to GRC. Enter _32000_ for _Num_Samples_To_Count_ in the properties of the _Selector Control_ block: 
[![](https://wiki.gnuradio.org/images/thumb/a/af/SelectorControlProperties.png/500px-SelectorControlProperties.png)](https://wiki.gnuradio.org/index.php?title=File:SelectorControlProperties.png)
  
Add the _Message Debug_ block and connect the _messageOutput_ port to the "print" input port. Running the flowgraph shows the messages are being sent at a rate of once a second, alternating between _#t_ (True) and _#f_ (False): 
[![](https://wiki.gnuradio.org/images/thumb/e/e4/MessageDebugExample.png/800px-MessageDebugExample.png)](https://wiki.gnuradio.org/index.php?title=File:MessageDebugExample.png)
However, the output message port from _Selector Control_ is not yet connected to the input message port of _Multiplexer_ so the _QT GUI Time Sink_ only shows noise. 
## Final Flowgraph
Connect the output message port of _Selector Control_ to the input message port of _Multiplexer_. Notice that the dashed line travels behind the two blocks and can be difficult to see: 
[![](https://wiki.gnuradio.org/images/thumb/b/bd/ConnectMessagePorts.png/800px-ConnectMessagePorts.png)](https://wiki.gnuradio.org/index.php?title=File:ConnectMessagePorts.png)
_Virtual Sinks_ and _Virtual Sources_ can be used to clean up some of the connections and make the flowgraph easier to understand. Click on the dashed line and delete it. Drag and drop a _Virtual Sink_ and _Virtual Source_ into the workspace. Change the _Stream ID_ to _message_ for both the _Virtual Sink_ and _Virtual Source_ , and then connect them in the flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/f/fe/MessageVirtualSourceSink.png/800px-MessageVirtualSourceSink.png)](https://wiki.gnuradio.org/index.php?title=File:MessageVirtualSourceSink.png)
  
Run the flowgraph. The _QT GUI Time Sink_ shows an alternating output between the _Noise Source_ and _Signal Source_ : 
[![](https://wiki.gnuradio.org/images/thumb/9/9b/AlternatingOutputTimeSink.png/800px-AlternatingOutputTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:AlternatingOutputTimeSink.png)
The next tutorial, [Python Block Tags](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags "Python Block Tags"), describes how to read and write tags in a Python block. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing&oldid=14493](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing&oldid=14493)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Python+Block+Message+Passing "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Python_Block_Message_Passing&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing)
  * [View source](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Python_Block_Message_Passing "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Python_Block_Message_Passing "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing&oldid=14493 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing&action=info "More information about this page")


  * This page was last edited on 11 July 2024, at 15:35.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


