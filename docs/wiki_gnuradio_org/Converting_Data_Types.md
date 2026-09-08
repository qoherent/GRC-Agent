# Converting Data Types


|  **Beginner Tutorials** Introducing GNU Radio
  1. [What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "What Is GNU Radio")
  2. [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR")
  3. [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph")

Flowgraph Fundamentals
  1. [Python Variables in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "Python Variables in GRC")
  2. [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs")
  3. [Runtime Updating Variables](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables "Runtime Updating Variables")
  4. [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types")
  5. Converting Data Types
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
This tutorial demonstrates how to convert between data types.
The previous tutorial, [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types"), presents an introduction to the _Complex Float 32_ and _Float 32_ data types for representing digitized signals. The next tutorial, [Packing Bits](https://wiki.gnuradio.org/index.php?title=Packing_Bits "Packing Bits"), describes how to pack and unpack up to 8 bits into the _char_ or _byte_ data type.
## Char/Byte Data Type
The _Char_ or _Byte_ data type is another useful data type for representing binary data. The _Byte_ data type is represented by the **purple** color in GRC, labeled _Integer 8_ :
[](https://wiki.gnuradio.org/index.php?title=File:Types.png)
Search for the _Random Source_ block and drag it into the workspace:
[](https://wiki.gnuradio.org/index.php?title=File:SearchRandomSourceBlock.png)

The block defaults to the **green** _Integer 32_ data type. Double-click the block to open the properties and modify the data type to _byte_ :
[](https://wiki.gnuradio.org/index.php?title=File:SelectByteDataType.png)

The _Random Source_ is now converted to the **purple** _Char_ or _Byte_ data type.
[](https://wiki.gnuradio.org/index.php?title=File:RandomSourceByteOutput.png)
## Converting _Byte_ to _Float 32_
The default parameters of the _Random Source_ will randomly generate values of 0 and 1. Add the _QT GUI Time Sink_ and the _Throttle_ block into the workspace and connect the blocks:
[](https://wiki.gnuradio.org/index.php?title=File:ConnectionErrorCharToComplex.png)

The red arrow between the _Random Source_ and _Throttle_ blocks shows a data type error that needs to be fixed. Double-click the _Throttle_ block and change the data type to _byte_ :
[](https://wiki.gnuradio.org/index.php?title=File:ChangeThrottleDataType.png)

A new red arrow now shows there is a data type connection between the _Throttle_ and the _QT GUI Time Sink_ :
[](https://wiki.gnuradio.org/index.php?title=File:ConnectionErrorThrottleTimeSink.png)

The _QT GUI Time Sink_ does not have a char data type. Select _Float_ :
[](https://wiki.gnuradio.org/index.php?title=File:TimeSinkDataTypeOptions.png)

The GNU Radio block library comes with a variety of data type converters listed under _Type Converters_. Search for the _Char to Float_ block, drag it into the workspace, and connect it into the flowgraph:
[](https://wiki.gnuradio.org/index.php?title=File:SearchCharToFloatBlock.png)

All of the red errors have disappeared. Press the _Play_ button to start the flowgraph:
[](https://wiki.gnuradio.org/index.php?title=File:RunFlowgraphButton.png)

The _QT GUI Time Sink_ will now display the data from the _Random Source_ block which is randomized 0's and 1's:
[](https://wiki.gnuradio.org/index.php?title=File:RandomSourceTimeSinkOutput.png)
The next tutorial, [Packing Bits](https://wiki.gnuradio.org/index.php?title=Packing_Bits "Packing Bits"), describes how to pack and unpack up to 8 bits into the _char_ or _byte_ data type.
