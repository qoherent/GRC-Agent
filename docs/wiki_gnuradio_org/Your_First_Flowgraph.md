# Your First Flowgraph


|  **Beginner Tutorials** Introducing GNU Radio
  1. [What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "What Is GNU Radio")
  2. [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR")
  3. Your First Flowgraph

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
This tutorial describes how to create and run your first flowgraph in GNU Radio.
This guide assumes GNU Radio is installed. The GNU Radio installation structures are here: [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR"). The next tutorial, [Python Variables in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "Python Variables in GRC"), describes how Python data types are used in GNU Radio Companion (GRC).
## Starting GNU Radio Companion
The GNU Radio Companion (GRC) is a visual editor for creating and running flowgraphs. GRC uses _.grc_ files which are then translated into Python _.py_ flowgraphs.
Open a terminal by pressing _CTRL_ + _ALT_ + _T_ or by right-clicking on the desktop and selecting _Open in Terminal_ :
[](https://wiki.gnuradio.org/index.php?title=File:OpenTerminal.png)
Type in the terminal:

```
$ gnuradio-companion &
```

The GRC window opens:
[](https://wiki.gnuradio.org/index.php?title=File:NewGRCFlowgraph.png)

Double click the _Options_ block and name the flowgraph by editing the _Id_ and _Title_ :
[](https://wiki.gnuradio.org/index.php?title=File:NameYourFlowgraph.png)
The _Id_ is the filename of the Python flowgraph. Name it _sineWaveFlowgraph_. The _Title_ is a description of the flowgraph. Click _OK_ to save the changes.
Click _File : Save_ to save the GRC Flowgraph.
[](https://wiki.gnuradio.org/index.php?title=File:GRCClickSave.png)
Enter _sineWaveGRC.grc_ as the name for the _.grc_ file to distinguish it from the Id.
[](https://wiki.gnuradio.org/index.php?title=File:EnterGRCName.png)
The GRC file is named and saved.
## Adding Blocks
Blocks are added to create the first flowgraph. GNU Radio comes with a library of signal processing blocks. The blocks can be browsed using the arrows on the right. Blocks may also be searched for using _CTRL + F_ or by selecting the magnifying glass (highlighted in red):
[](https://wiki.gnuradio.org/index.php?title=File:SearchBlockLibrary.png)
Search for the _Signal Source_ block and then drag and drop it into the GRC workspace:
[](https://wiki.gnuradio.org/index.php?title=File:SearchSignalSourceBlock.png)
Now search for _Throttle_ , _QT GUI Frequency Sink_ and _QT GUI Time Sink_. Drag and drop each of the blocks into the workspace. The flowgraph should like the following:
[](https://wiki.gnuradio.org/index.php?title=File:UnconnectedFlowgraph.png)
The _Signal Source_ block will create a complex sinusoid, _QT GUI Frequency Sink_ will display the magnitude of the frequency spectrum and _QT GUI Time Sink_ will display the time domain. The _Throttle_ block is used for flow control in the absence of radio hardware.
The blocks need to be connected. First click the output of _Signal Source_ (highlighted in **red**) and then click the input to the _Throttle_ (highlighted in **orange**).
[](https://wiki.gnuradio.org/index.php?title=File:MakeFirstConnection.png)
The _Signal Source_ block text changed from **red** to **black**. The **red** text means a block still has an input or output that needs to be connected before the flowgraph can be run. Connect the throttle output to the frequency sink and time sink:
[](https://wiki.gnuradio.org/index.php?title=File:ConnectedFlowgraph.png)
## Running The Flowgraph
Press the _Play_ button (highlighted in **red**) to run the flowgraph:
[](https://wiki.gnuradio.org/index.php?title=File:RunFlowgraphButton.png)
A new window displays the signal in the time domain and frequency domain:
[](https://wiki.gnuradio.org/index.php?title=File:FrequencySinkTimeSink.png)
Success! The flowgraph is running.
Open the file browser. There are two files. The first file is _sineWaveGRC.grc_ , containing the information for the display of the flowgraph in GRC. The second file is _sineWaveFlowgraph.py_ , containing the actual Python flowgraph code. The _Id_ in the Options block determines the name of the _.py_ file.
[](https://wiki.gnuradio.org/index.php?title=File:GRCandPy.png)
Continue onto the next tutorial, [Python Variables in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "Python Variables in GRC"), which describes how Python data types are used in GRC.
