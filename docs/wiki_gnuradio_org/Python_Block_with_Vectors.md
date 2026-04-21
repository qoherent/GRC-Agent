# Python Block with Vectors
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors#searchInput)  
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
  2. Python Block With Vectors
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
This tutorial describes how the _Python Embedded Block_ can be modified to accept vector inputs and outputs, and how the _input_items_ vector indexing is different between vectors and streams. 
The previous tutorial, [Creating Your First Block](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block "Creating Your First Block"), demonstrates how to create a Python block using the _Embedded Python Block_. The next tutorial, [Python Block Message Passing](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing "Python Block Message Passing") describes how to send and receive messages using the _Embedded Python Block_. 
## Contents
  * [1 Starting the Flowgraph](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors#Starting_the_Flowgraph)
  * [2 Accepting Vector Inputs and Outputs](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors#Accepting_Vector_Inputs_and_Outputs)
  * [3 Warning for Vector Length Mismatches](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors#Warning_for_Vector_Length_Mismatches)
  * [4 Indexing Streams](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors#Indexing_Streams)
  * [5 Indexing Vectors](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors#Indexing_Vectors)
  * [6 Creating Max Hold Function](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors#Creating_Max_Hold_Function)
  * [7 Multiple Vector Ports](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors#Multiple_Vector_Ports)


## Starting the Flowgraph
This tutorial uses vectors. Please complete the [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors") tutorial before moving on. 
Add the blocks to the flowgraph: 
  * Signal Source
  * Throttle
  * Stream to Vector
  * Embedded Python Block
  * Vector to Stream
  * QT GUI Time Sink
  * Virtual Sink
  * Virtual Source
  * Variable


Change the block properties: 
  * Signal Source 
    * Output Type: float
    * Frequency: 100
  * Variable 
    * Id: vectorLength
    * Value: 16
  * Stream to Vector, Num Items: vectorLength
  * Vector to Stream, Num Items: vectorLength
  * Virtual Sink, Stream Id: sinusoid
  * Virtual Source, Stream Id: sinusoid
  * QT GUI Time Sink 
    * Autoscale: Yes
    * Number of Inputs: 2


Connect the blocks according to the following flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/5/5f/PythonVectorStartingFlowgraph.png/800px-PythonVectorStartingFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:PythonVectorStartingFlowgraph.png)
## Accepting Vector Inputs and Outputs
The _Embedded Python Block_ needs to be modified to: 
  * accept vector inputs
  * produce vector outputs
  * change the data types to _float_


With respect to the [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors") tutorial, from a higher-level point of view, _Streams_ are typically just a special case of _Vectors_ , ones that have just one data element in parallel instead of multiple. This is why we can just change the `in_sig` and `out_sig` parameters to use vectors rather than streams. 
Since the case of streams is so common it has its own simple syntax, whereas the syntax for vectors is just a bit more complex. In particular, when using vectors, we must specify the vector's length along with the data type of the elements in the vectors. We do so using a tuple in Python. 
Double-click on the "Embedded Python Block" to edit the source code. 
Change _example_param_ in the function definition to _vectorSize_ : 

```
def __init__(self, vectorSize=16):
```

Change the _name_ : 

```
name='Max Hold Block',
```

Define the input and output vector signals using a tuple: 

```
in_sig=[(np.float32,vectorSize)],
out_sig=[(np.float32,vectorSize)]
```

Remove the _self.example_param = example_param_ line. 
Remove the multiplication by _self.example_param_ : 

```
output_items[0][:] = input_items[0]
```

The code should now look like the following: 
[![](https://wiki.gnuradio.org/images/thumb/9/99/PythonVectorDefineBlock.png/800px-PythonVectorDefineBlock.png)](https://wiki.gnuradio.org/index.php?title=File:PythonVectorDefineBlock.png)
  
Save. Connect the _Max Hold Block_ to the rest of the flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/8/87/PythonVectorConnectMaxHold.png/800px-PythonVectorConnectMaxHold.png)](https://wiki.gnuradio.org/index.php?title=File:PythonVectorConnectMaxHold.png)
## Warning for Vector Length Mismatches
The _Embedded Python Block_ has one difference that other [Out of Tree Modules](https://wiki.gnuradio.org/index.php?title=OutOfTreeModules "OutOfTreeModules") do not have. Before a flowgraph can be run, GRC checks to ensure all of the connected data types and vector sizes match. During this process the default value of _vectorSize_ in the ___init__()_ function, 

```
def __init__(self, vectorSize=16):
```

is used to define the size of the vectors for the input and output, 

```
in_sig=[(np.float32,vectorSize)],
out_sig=[(np.float32,vectorSize)]
```

when determining if the flowgraph is correct. In this case _vectorSize=16_. GRC assumes the input and output ports are vectors with length 16, _even if a different parameter is passed in through the block properties!_. The following image shows how a vector size of 128 is passed in as a parameter, which GRC does not catch as an error, but the flowgraph will crash once it's run: 
[![](https://wiki.gnuradio.org/images/thumb/5/56/MismatchVectorSizeOption1.png/800px-MismatchVectorSizeOption1.png)](https://wiki.gnuradio.org/index.php?title=File:MismatchVectorSizeOption1.png)
  


```
Traceback (most recent call last):
  File "/home/username/vectorinput.py", line 250, in <module>
    main()
  File "/home/username/vectorinput.py", line 226, in main
    tb = top_block_cls()
  File "/home/username/vectorinput.py", line 188, in __init__
    self.connect((self.blocks_stream_to_vector_0, 0), (self.epy_block_0, 0))
  File "/usr/lib/python3/dist-packages/gnuradio/gr/hier_block2.py", line 48, in wrapped
    func(self, src, src_port, dst, dst_port)
  File "/usr/lib/python3/dist-packages/gnuradio/gr/hier_block2.py", line 111, in connect
    self.primitive_connect(*args)
  File "/usr/lib/python3/dist-packages/gnuradio/gr/runtime_swig.py", line 4531, in primitive_connect
    return _runtime_swig.top_block_sptr_primitive_connect(self, *args)
RuntimeError: itemsize mismatch: stream_to_vector0:0 using 64, Embedded Python Block0:0 using 512
```

Alternatively, GRC will show an error if the default parameter does not match the vector size of the other blocks. In this case, the default vector length in the code is 128 but the passed-in parameter is 16: 
[![](https://wiki.gnuradio.org/images/thumb/1/11/MismatchVectorSizeOption2.png/800px-MismatchVectorSizeOption2.png)](https://wiki.gnuradio.org/index.php?title=File:MismatchVectorSizeOption2.png)
## Indexing Streams
For a stream, inputs and outputs can be indexed using both the port number and the sample index. 
Indexing based on the port number returns all of the input samples for a specific port. For example, 

```
input_items[0]
```

returns all input samples on port 0. 
The following line returns the 4th input sample on port 0: 

```
input_items[0][3]
```

The indexing for streams is generalized to: 

```
input_items[portIndex][sampleIndex]
output_items[portIndex][sampleIndex]
```

The image shows how to visualize indexing streams: 
[![](https://wiki.gnuradio.org/images/thumb/f/f2/IndexingStreams.png/500px-IndexingStreams.png)](https://wiki.gnuradio.org/index.php?title=File:IndexingStreams.png)
## Indexing Vectors
The input _input_items_ and output _output_items_ include an extra dimension when using vectors. 
Vectors add an additional dimension, represented as _vectorIndex_ below. The _input_items_ and _output_items_ are now three-dimensional arrays: 

```
input_items[portIndex][vectorIndex][sampleIndex]
output_items[portIndex][vectorIndex][sampleIndex]
```

Indexing based on the _portIndex_ returns a two-dimensional array of all vectors and samples, for example: 

```
input_items[portIndex]
output_items[portIndex]
```

Indexing based on _portIndex_ and _vectorIndex_ returns an single-dimensional array of samples, for example: 

```
input_items[portIndex][vectorIndex]
output_items[portIndex][vectorIndex]
```

Indexing based on _portIndex_ , _vectorIndex_ and _sampleIndex_ returns a single sample. 

```
input_items[portIndex][vectorIndex][sampleIndex]
output_items[portIndex][vectorIndex][sampleIndex]
```

A visual example vector indexing is given below: 
[![](https://wiki.gnuradio.org/images/thumb/b/b3/VectorIndexing.png/700px-VectorIndexing.png)](https://wiki.gnuradio.org/index.php?title=File:VectorIndexing.png)
## Creating Max Hold Function
The _work()_ function is modified to include the max hold function. Add a loop over all of the vectors in _input_items[0]_ : 

```
for vectorIndex in range(len(input_items[0])):
```

Calculate the max value of the vector: 

```
maxValue = np.max(input_items[0][vectorIndex])
```

Loop over each of the input samples: 

```
for sampleIndex in range(len(input_items[0][vectorIndex])):
```

Assign each output sample _maxValue_ : 

```
output_items[0][vectorIndex][sampleIndex] = maxValue
```

The code should look like the following: 
[![](https://wiki.gnuradio.org/images/thumb/2/20/MaxHoldWorkFunction.png/700px-MaxHoldWorkFunction.png)](https://wiki.gnuradio.org/index.php?title=File:MaxHoldWorkFunction.png)
  
Save the code (CTRL + S). Run the flowgraph. The output will shows a sinusoid and a sinusoid with a max-hold applied every 16 samples: 
[![](https://wiki.gnuradio.org/images/thumb/1/17/MaxHoldOutput.png/600px-MaxHoldOutput.png)](https://wiki.gnuradio.org/index.php?title=File:MaxHoldOutput.png)
## Multiple Vector Ports
The _Max Hold Block_ is modified to add a second vector input and output port. 
Add the following blocks to the workspace: 
  * Noise Source
  * Stream to Vector
  * Vector to Stream
  * Virtual Sink
  * Virtual Source
  * QT GUI Time Sink


Change the following block properties: 
  * Noise Source, Output Type: float
  * Stream to Vector, Num Items: vectorLength
  * Vector to Stream, Num Items: vectorLength
  * Virtual Sink, Stream Id: noise
  * Virtual Source, Stream Id: noise
  * QT GUI Time Sink 
    * Autoscale: Yes
    * Number of Inputs: 2


Connect the blocks: 
[![](https://wiki.gnuradio.org/images/thumb/7/7d/PythonVectorNoiseSource.png/800px-PythonVectorNoiseSource.png)](https://wiki.gnuradio.org/index.php?title=File:PythonVectorNoiseSource.png)
  
Edit the code for the _Max Hold Block_. Add a second vector input and output: 

```
in_sig=[(np.float32,vectorSize),(np.float32,vectorSize)],
out_sig=[(np.float32,vectorSize),(np.float32,vectorSize)]
```

[![](https://wiki.gnuradio.org/images/thumb/8/89/PythonBlockSecondVector.png/700px-PythonBlockSecondVector.png)](https://wiki.gnuradio.org/index.php?title=File:PythonBlockSecondVector.png)
The _work()_ function is modified to perform the max hold function over both input ports. 
Include an outer loop over all input ports: 

```
for portIndex in range(len(input_items)):
```

Change all indexing _[0]_ to _[portIndex]_ : 

```
for portIndex in range(len(input_items)):
    for vectorIndex in range(len(input_items[portIndex])):
        maxValue = np.max(input_items[portIndex][vectorIndex])
            for sampleIndex in range(len(input_items[portIndex][vectorIndex])):
                output_items[portIndex][vectorIndex][sampleIndex] = maxValue
```

The code should now look like the following: 
[![](https://wiki.gnuradio.org/images/thumb/6/68/PythonVectorFinalCode.png/700px-PythonVectorFinalCode.png)](https://wiki.gnuradio.org/index.php?title=File:PythonVectorFinalCode.png)
  
Save the code and connect the blocks: 
[![](https://wiki.gnuradio.org/images/thumb/a/ae/PythonVectorFinalFlowgraph.png/800px-PythonVectorFinalFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:PythonVectorFinalFlowgraph.png)
  
Run the flowgraph. Two max-hold outputs will now be generated, one for the noise source and one for the sinusoid: 
[![](https://wiki.gnuradio.org/images/thumb/5/52/TwoMaxHoldOutputs.png/700px-TwoMaxHoldOutputs.png)](https://wiki.gnuradio.org/index.php?title=File:TwoMaxHoldOutputs.png)
  
The next tutorial, [Python Block Message Passing](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing "Python Block Message Passing") describes how to send and receive messages using the _Embedded Python Block_. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors&oldid=14510](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors&oldid=14510)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Python+Block+with+Vectors "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Python_Block_with_Vectors&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors)
  * [View source](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Python_Block_with_Vectors "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Python_Block_with_Vectors "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors&oldid=14510 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors&action=info "More information about this page")


  * This page was last edited on 27 July 2024, at 12:45.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


