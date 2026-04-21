# Reading and Writing Binary Files
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#searchInput)  
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
  4. [Python Block Tags](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags "Python Block Tags")

DSP Blocks 
  1. [Low Pass Filter Example](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example "Low Pass Filter Example")
  2. [Designing Filter Taps](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps "Designing Filter Taps")
  3. [Sample Rate Change](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change "Sample Rate Change")
  4. [Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "Frequency Shifting")
  5. Reading and Writing Binary Files

SDR Hardware 
  1. [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver")
  2. [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver")
  3. [E310 FM Receiver](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver "E310 FM Receiver")

 |  
| --- |  
Binary files allow RF information to be recorded for offline usage. Before continuing it is useful to refer to the tutorial [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types") which describes different data types in GNU Radio. 
The previous tutorial, [Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "Frequency Shifting"), describes how to change the frequency of a signal both mathematically and with DSP blocks. The next tutorial, [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver"), describes how to build a broadcast FM receiver using an RTL-SDR receiver. 
## Contents
  * [1 Binary Data File Formats for DSP](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Binary_Data_File_Formats_for_DSP)
    * [1.1 Data File Formats](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Data_File_Formats)
    * [1.2 Real and Complex Formats](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Real_and_Complex_Formats)
    * [1.3 Saving Samples as Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Saving_Samples_as_Binary_Files)
    * [1.4 Endianness](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Endianness)
  * [2 Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Writing_Binary_Files)
    * [2.1 Block Options for Data Types](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Block_Options_for_Data_Types)
    * [2.2 Filenames: Data Format](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Filenames:_Data_Format)
    * [2.3 Filenames: Recording Sample Rate](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Filenames:_Recording_Sample_Rate)
    * [2.4 Filenames: Using a Variable](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Filenames:_Using_a_Variable)
    * [2.5 Filenames: Timestamping](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Filenames:_Timestamping)
    * [2.6 Writing Complex 32-bit Floats](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Writing_Complex_32-bit_Floats)
    * [2.7 Writing Real 32-bit Floats](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Writing_Real_32-bit_Floats)
    * [2.8 Writing Complex 16-bit Integers](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Writing_Complex_16-bit_Integers)
    * [2.9 Writing Real 16-bit Integers](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Writing_Real_16-bit_Integers)
    * [2.10 File Append](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#File_Append)
  * [3 Reading Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Reading_Binary_Files)
    * [3.1 File Source Block](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#File_Source_Block)
    * [3.2 Reading Complex Float](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Reading_Complex_Float)
    * [3.3 Reading Real Float](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Reading_Real_Float)
    * [3.4 Reading Real Integers](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Reading_Real_Integers)
    * [3.5 Reading Complex Integers](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Reading_Complex_Integers)
    * [3.6 Continuous Playback from File](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Continuous_Playback_from_File)
    * [3.7 Diagnosing Errors: Wrong Type and Format](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Diagnosing_Errors:_Wrong_Type_and_Format)
    * [3.8 Diagnosing Errors: Endianness](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files#Diagnosing_Errors:_Endianness)


# Binary Data File Formats for DSP
## Data File Formats
Each binary file will have a specific data format, with the two most common being 32-bit floats and 16-bit integers. RF samples can be both positive and negative, and therefore all integers will be implied to be signed integers, for simplicity. The binary file will save all samples in the same format back to back. For example, a binary file of 16-bit integers will save sample 0 as 16 bits, then sample 1 as 16-bits, sample 2 as 16-bits, and so on. 

```
[ sample 0: 16 bit int ][ sample 1: 16 bit int ][ sample 2: 16 bit int ] ...

```

For example, a binary file of 32-bit floats will save sample 0 as 32 bits, then sample 1 as 32-bits, sample 2 as 32-bits, and so on. 

```
[ sample 0: 32 bit float ][ sample 1: 32 bit float ][ sample 2: 32 bit float ] ...

```

## Real and Complex Formats
RF samples can be either real or complex. When a real sample is saved to a binary file each sample is saved in order: sample 0, then sample 1, then sample 2, and so on. 

```
[ real sample 0 ][ real sample 1 ][ real sample 2 ] ...

```

A complex sample, I + jQ, has both a real component (I) and imaginary component (Q). The I and Q components of each sample will be interleaved when saved to a binary file. I of sample 0, then Q of sample 0, then I of sample 1, then Q of sample 1, then I of sample 2, then Q of sample 2, and so on. 

```
[ I sample 0 ][ Q sample 0 ][ I sample 1 ][ Q sample 1 ][ I sample 2 ][ Q sample 2 ] ...

```

## Saving Samples as Binary Files
The different types of sample representations and binary file formats can be mixed and matched: 
  * Real samples stored as 16-bit integers
  * Real samples stored as 32-bit floats
  * Complex samples stored as interleaved 16-bit integers
  * Complex samples stored as interleaved 32-bit floats


Real samples stored as 16-bit integers: 

```
[ sample 0: 16 bit int ][ sample 1: 16 bit int ][ sample 2: 16 bit int ] ...

```

Real samples stored as 32-bit floats: 

```
[ sample 0: 32 bit float ][ sample 1: 32 bit float ][ sample 2: 32 bit float ] ...

```

Complex samples stored as interleaved 16-bit integers: 

```
[ I sample 0: 16 bit int ][ Q sample 0: 16 bit int ][ I sample 1: 16 bit int ][ Q sample 1: 16 bit int ][ I sample 2: 16 bit int ][ Q sample 2: 16 bit int ] ...

```

Complex samples stored as interleaved 32-bit floats: 

```
[ I sample 0: 32 bit float ][ Q sample 0: 32 bit float ][ I sample 1: 32 bit float ][ Q sample 1: 32 bit float ][ I sample 2: 32 bit float ] [ Q sample 2: 32 bit float ] ...

```

## Endianness
Endianness describes the order of bytes within binary data. Big endian and little endian systems differ on the placement of the most significant byte and least significant byte. Little endian systems place the least significant byte in the smallest memory address and the most significant byte in the largest memory address. Big endian systems store the most significant byte in the smallest memory address and the least significant byte in the largest memory address. 
For example, consider the hexadecimal value 0xABCD0123. A big endian system would store the value into memory by: 

```
Data Value:      [ 0xAB ] [ 0xCD] [ 0x01 ] [ 0x23 ]
Memory Address:  [ 0x00 ] [ 0x01] [ 0x02 ] [ 0x03 ]

```

A little endian system stores the bytes in the reversed order into memory: 

```
Data Value:      [ 0x23 ] [ 0x01] [ 0xCD ] [ 0xAB ] [BR]
Memory Address:  [ 0x00 ] [ 0x01] [ 0x02 ] [ 0x03 ]

```

Converting between endianness is sometimes referred to as a “byte swap” operation. The [Endian Swap](https://wiki.gnuradio.org/index.php?title=Endian_Swap "Endian Swap") block performs this endian conversion. 
  
The **File Sink** block takes incoming samples and saves them to local storage. It is recommended to review the [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types"), [Binary Files for DSP](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP "Binary Files for DSP") tutorials and the [File Sink](https://wiki.gnuradio.org/index.php?title=File_Sink "File Sink") block page before continuing. 
# Writing Binary Files
## Block Options for Data Types
By default the **File Sink** block uses a 32-bit float format for saving interleaved I and Q: 
[![](https://wiki.gnuradio.org/images/1/18/Storing_binary_files_file_sink_complex_floats.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_file_sink_complex_floats.png)
  
Opening the block's properties, other formats can be selected from the drop down menu: 
[![](https://wiki.gnuradio.org/images/2/26/Storing_binary_files_file_sink_types_drop_down.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_file_sink_types_drop_down.png)
  
Another common type is _float_ , represented by **orange** , which writes real samples as 32-bit floats. 
[![](https://wiki.gnuradio.org/images/6/6a/Storing_binary_files_file_sink_real_floats.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_file_sink_real_floats.png)
  
Data may also be written as 16-bit integers using the short type represented by **yellow**. Both real and complex samples may be written with this type, which will be discussed later in this tutorial. 
[![](https://wiki.gnuradio.org/images/8/8f/Storing_binary_files_file_sink_short_ints.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_file_sink_short_ints.png)
  
The **File Sink** also has a _File_ parameter which needs to be defined. Click on the three dots: 
[![](https://wiki.gnuradio.org/images/7/74/Storing_binary_files_navigate_to_path.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_navigate_to_path.png)
  
On Ubuntu a window will appear which will allows navigation to different directories so the file can be saved. The file can be saved anywhere, including the home directory although for this example it is saved in _/opt/tutorials_ and the output filename is _binary_file_. 
[![](https://wiki.gnuradio.org/images/0/0d/Storing_binary_files_save_file.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_save_file.png)
  
The path can also be entered directly as a text string: 
[![](https://wiki.gnuradio.org/images/e/ee/Storing_binary_files_path_to_file_defined.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_path_to_file_defined.png)
  
Note that by default a file sink uses the _Overwrite_ function, meaning each time the flowgraph is run the binary file in that location will be replaced by all of the new samples. The _Append_ function is described later in this tutorial. 
## Filenames: Data Format
When writing binary files it is important to make a record of important metadata such as the sampling rate, binary format and others. This can be done with more complicated blocks such as [File Meta Sink](https://wiki.gnuradio.org/index.php?title=File_Meta_Sink "File Meta Sink") or [SigMF Sink](https://wiki.gnuradio.org/index.php?title=SigMF_Sink_\(Minimal\) "SigMF Sink \(Minimal\)"), however in the following examples the metadata will be stored in the filename of the binary file. 
It is good practice to have readable file names when saving samples to file. The first is to include the type of data in the filename. For example, a binary file of complex samples represented by 32-bit floats could be given the file extension _.complex_float_. This can be added to the **File Sink** properties: 
[![](https://wiki.gnuradio.org/images/b/b5/Storing_binary_files_file_extension_example.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_file_extension_example.png)
  
The following are suggested file extensions: 
  * Complex samples represented by 32-bit floats: _.complex_float_
  * Complex samples represented by 16-bit integers: _.complex_int_
  * Real samples represented by 32-bit floats: _.real_float_
  * Real samples represented by 16-bit integers: _.real_int_


## Filenames: Recording Sample Rate
It is also good practice to record the sampling rate in the filename. This can be automated through the use of a variable. 
First change the _samp_rate_ variable to 100 kHz (10*10**3): 
[![](https://wiki.gnuradio.org/images/f/f6/Storing_binary_files_change_samp_rate.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_change_samp_rate.png)
  
Add a new **Variable** block to the flowgraph and convert the _samp_rate_ number into a string. The number is converted into an integer before string conversion to make the text easier to display in the filename: 
[![](https://wiki.gnuradio.org/images/5/5b/Storing_binary_files_string_samp_rate.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_string_samp_rate.png)
  
The sample rate is included in the filename through string concatenation. Note that using the file navigator button to the right of the filename will overwrite any variable-based filenames you have entered. 
[![](https://wiki.gnuradio.org/images/b/bd/Storing_binary_files_samp_rate_filename.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_samp_rate_filename.png)
Since the _samp_rate_ variable was changed to 100,000 the file will be saved to: _/opt/tutorials/binary_file_100000Hz.complex_float_
Holding the cursor over the _File_ property will bring up summary information, including the string with all values substituted, which can be used to verify that the string has been formatted correctly before running the flowgraph: 
[![](https://wiki.gnuradio.org/images/b/b9/Storing_binary_files_highlight_filename.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_highlight_filename.png)
## Filenames: Using a Variable
The _File_ field can be simplified by using a variable. Copy the text into a new variable block and name it filename: 
[![](https://wiki.gnuradio.org/images/3/30/Storing_binary_files_variable_filename.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_variable_filename.png)
  
Now update the **File Sink** block and replace the long string with filename: 
[![](https://wiki.gnuradio.org/images/6/61/Storing_binary_files_file_sink_filename_variable.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_file_sink_filename_variable.png)
  
The **File Sink** will now use the _filename_ variable which can be changed and modified through other variables, and those changes will be incorporated when the flowgraph starts and the binary file is written. 
[![](https://wiki.gnuradio.org/images/d/db/Storing_binary_files_variable_filename_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_variable_filename_flowgraph.png)
## Filenames: Timestamping
It is also useful to timestamp binary files at the time they are written. Add the Import block to the flowgraph and import the time library: 
[![](https://wiki.gnuradio.org/images/3/31/Storing_binary_files_import_time.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_import_time.png)
  
Now create a new variable named timestamp: 
[![](https://wiki.gnuradio.org/images/f/ff/Storing_binary_files_timestamp_variable.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_timestamp_variable.png)
  
The int() is done to only take the integer part of the timestamp so it disregards any fractional seconds, and the str() converts the integer into a string so it can be concatenated to the filename. The timestamp is done in seconds since January 1, 1970 [[1]](https://en.wikipedia.org/wiki/Unix_time). 
[![](https://wiki.gnuradio.org/images/e/ea/Storing_binary_files_timestamp_variable_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_timestamp_variable_flowgraph.png)
  
Notice that the timestamp in the flowgraph above is evaluated at the time the properties block was closed, however the function call will determine the proper timestamp at run time. 
Now update the filename variable to include the timestamp through string concatenation: 
[![](https://wiki.gnuradio.org/images/2/22/Storing_binary_files_insert_timestamp_in_filename.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_insert_timestamp_in_filename.png)
Opening the Python flowgraph shows that the timestamp will be evaluated at run-time and therefore will have an accurate record of when the file was written: 
[![](https://wiki.gnuradio.org/images/f/f2/Storing_binary_files_timestamp_python_code.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_timestamp_python_code.png)
  
Navigate to the directory the files are stored in, which in this case is _/opt/tutorials_. The different filename formats can be seen for each of the examples: 
[![](https://wiki.gnuradio.org/images/7/77/Storing_binary_files_all_filenames.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_all_filenames.png)
## Writing Complex 32-bit Floats
Add a **Signal Source** block, connect it to the **File Sink** and run the flowgraph for a second or two and then stop the flowgraph. 
The flowgraph will now start running and will pop up a QT GUI window, but it is not populated because there are no plot blocks in the flowgraph. Data is continually being written to the file as the flowgraph is running. Close the QT GUI window to stop the flowgraph: 
[![](https://wiki.gnuradio.org/images/f/ff/Storing_binary_files_close_QT_GUI.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_close_QT_GUI.png)
Navigate to the directory where the file is stored. For this example, the file was saved to _/opt/tutorials_. 
On a Linux-based operating system the file size can be measured with the following terminal command: 

```
$ ls -lah

```

The following represents the output of the command: 

```
user@hostname:/opt/tutorials$ ls -lah
total 3.1G
drwxr-xr-x 2 user user 4.0K Date 12:41 .
drwxr-xr-x 5 root root 4.0K Date  18:15 ..
-rw-rw-r-- 1 user user 3.1G Date 12:48 binary_file_100000Hz_1712960752.complex_float

```

The last line shows that the file is 3.1 GB! Your exact file size may be different based on the speed of your CPU and how long you run your flowgraph. 
Digitized sample files grow quickly so be sure avoid filling up the memory storage or problems can arise. From the 3.1 GB, we can work backwards to determine approximately how many complex samples are in the file. Each complex sample writes the I as a 32-bit float and the Q as a 32-bit float, therefore each complex sample is 64 bits or 8 bytes. The ratio computing the ratio of file size to the size of each sample shows there are roughly 3.1 GB / 8 bytes = 387,500 complex samples written in the file. 
## Writing Real 32-bit Floats
A similar process is used to write real samples as 32-bit floats. First change the file extension for the _filename_ variable to _real_float_ : 
[![](https://wiki.gnuradio.org/images/d/de/Storing_binary_files_change_filename_real_float.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_change_filename_real_float.png)
  
The data type for the **File Sink** is changed to _float_ : 
[![](https://wiki.gnuradio.org/images/8/85/Storing_binary_files_select_float.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_select_float.png)
  
The **Signal Source** block is also changed to produce real floats: 
[![](https://wiki.gnuradio.org/images/f/f8/Storing_binary_files_signal_source_real.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_signal_source_real.png)
The flowgraph should now look like the following: 
[![](https://wiki.gnuradio.org/images/6/69/Storing_binary_files_storing_real_floats.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_storing_real_floats.png)
  
Running the flowgraph will write a brand new file consisting of real samples encoded as 32-bit floats to the specified location. 
## Writing Complex 16-bit Integers
Writing complex samples as 16-bit integers takes a couple extra steps. First change filename extension to _complex_int_ : 
[![](https://wiki.gnuradio.org/images/1/16/Storing_binary_files_change_filename_complex_int.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_change_filename_complex_int.png)
  
Then change the **Signal Source** block to _complex_ type. Then change the data type of the file sink to short: 
[![](https://wiki.gnuradio.org/images/0/05/Storing_binary_files_select_short.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_select_short.png)
  
Then add the **Complex to IShort** Block and connect it accordingly. 
[![](https://wiki.gnuradio.org/images/a/aa/Storing_binary_files_complex_to_ishort_scale_factor.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_complex_to_ishort_scale_factor.png)
  
The _IShort_ denotes the data type as interleaved 16-bit integers. Notice there is a scale factor parameter in the **Complex to IShort** block. 32-bit floating point numbers have a wider range of values they can represent than 16-bit integers, therefore the scale factor is needed to help perform the conversion. In this example, the **Signal Source** block generates a waveform with values between -1 and 1. However, 16-bit integers can only represent integer values from (-2^15)-1 to (2^15). Therefore the complex values need to be scaled to make full use of the dynamic range of the 16-bits. This is accomplished by setting the scale factor to 2^15: 
[![](https://wiki.gnuradio.org/images/b/b7/Storing_binary_files_ishort_scaling_value.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_ishort_scaling_value.png)
  
The flowgraph should now look like the following: 
[![](https://wiki.gnuradio.org/images/7/76/Storing_binary_files_complex_to_ishort_scale_factor_32k.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_complex_to_ishort_scale_factor_32k.png)
Running the flowgraph now writes the complex samples as interleaved I and Q, with I being written as a 16-bit integer and Q being written as a 16-bit integer. 
## Writing Real 16-bit Integers
Writing real samples as 16-bit integers is similar to the process of writing complex samples as 16-bit integers. The **File Sink** also uses the _short_ type, and add the **Float to Short** block. Note that the data type is _Short_ and not _IShort_ , because the real samples are not interleaved like the complex samples. 
First change the file extension to _real_int_ : 
[![](https://wiki.gnuradio.org/images/f/f2/Storing_binary_files_change_filename_real_int.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_change_filename_real_int.png)
Connect the following flowgraph: 
[![](https://wiki.gnuradio.org/images/8/85/Storing_binary_files_real_to_short_scaling_factor.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_real_to_short_scaling_factor.png)
  
The scale factor needs to be updated to 2^15: 
[![](https://wiki.gnuradio.org/images/6/63/Storing_binary_files_short_scaling_value.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_short_scaling_value.png)
  
The updated value is reflected in the flowgraph: 
[![](https://wiki.gnuradio.org/images/5/58/Storing_binary_files_storing_real_ints.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_storing_real_ints.png)
  
Running the flowgraph now saves the real samples as 16-bit integers to file. 
## File Append
The **File Sink** block has an option to _Append_ samples to the saved binary file. This means the existing binary file will not be overwritten, only added onto at the end of the file. This is selected by the _Append_ option from the _Append File_ option. 
[![](https://wiki.gnuradio.org/images/1/13/Storing_binary_files_select_append.png)](https://wiki.gnuradio.org/index.php?title=File:Storing_binary_files_select_append.png)
# Reading Binary Files
This tutorial describes how to read binary files using the **File Source** block along side how to diagnose potential errors. 
Please review the [Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Writing_Binary_Files "Writing Binary Files") tutorial before continuing. A series of binary files were created with different formats that will be needed for this tutorial: 
[![](https://wiki.gnuradio.org/images/b/be/Reading_binary_files_all_formats.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_all_formats.png)
  

## File Source Block
The **File Source** block reads from a binary file and then sends the samples to the output port. Drag the **File Source** block into a flowgraph. The block by default uses the complex data type (32-bit floats), represented by the **blue** output port: 
[![](https://wiki.gnuradio.org/images/0/04/Reading_binary_files_add_file_sink_block.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_add_file_sink_block.png)
  
Double clicking the **File Source** block brings up the properties and the ability to select different data types. 
[![](https://wiki.gnuradio.org/images/7/79/Reading_binary_files_file_source_data_types.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_file_source_data_types.png)
  
A binary file of real floating point data requires the float data type to be selected, which outputs real floating point samples, denoted by an **orange** output port. 
[![](https://wiki.gnuradio.org/images/b/bc/Reading_binary_files_file_sink_real_float.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_file_sink_real_float.png)
  
A binary file of 16-bit signed integers requires the short data type to be selected, which outputs 16-bit integers of either real or interleaved I and Q samples (more on this later in the tutorial), denoted by a **yellow** output port. 
[![](https://wiki.gnuradio.org/images/e/e3/Reading_binary_files_file_sink_real_short.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_file_sink_real_short.png)
  
Also note that the **File Source** has the _Repeat_ field enabled as _Yes_ , which will continually and repeatedly play back the same file. Once the last sample is received in the file it skips back to the first sample in the file and continues cycling through the file. 
[![](https://wiki.gnuradio.org/images/7/76/Reading_binary_files_repeat_yes.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_repeat_yes.png)
  

## Reading Complex Float
Add a **File Source** block, open the properties and begin by selecting the complex type. 
[![](https://wiki.gnuradio.org/images/5/52/Reading_binary_files_add_complex_float_file_source.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_add_complex_float_file_source.png)
  
Click the three dots to the right side of the File property to browse to a stored binary file. 
[![](https://wiki.gnuradio.org/images/6/6c/Reading_binary_files_open_file.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_open_file.png)
  
Select the file ending in _.complex_float_ : 
  
[![](https://wiki.gnuradio.org/images/1/14/Reading_binary_files_select_complex_float.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_select_complex_float.png)
  
The **File Source** block will now populate the filename: 
[![](https://wiki.gnuradio.org/images/1/15/Reading_binary_files_complex_float_with_filename.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_complex_float_with_filename.png)
  
Notice that the filename is now filled in for the **File Source** however the _samp_rate_ variable is incorrectly 32 kHz (32,000). The sampling rate from the filename is 100 kHz (100,000) therefore update the _samp_rate_ variable: 
[![](https://wiki.gnuradio.org/images/5/56/Reading_binary_files_update_samp_rate.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_update_samp_rate.png)
  
The change will be reflected in the flowgraph: 
[![](https://wiki.gnuradio.org/images/9/9c/Reading_binary_file_update_samp_rate_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_file_update_samp_rate_flowgraph.png)
  
Add in the **QT GUI Time Sink** and **QT GUI Frequency Sink** and connect them accordingly. Notice how both blocks use _samp_rate_ variable automatically: 
[![](https://wiki.gnuradio.org/images/5/5a/Reading_binary_files_add_time_freq_sink.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_add_time_freq_sink.png)
  
Before running the flowgraph, recall that the [Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Writing_Binary_Files "Writing Binary Files") generated a 1 kHz complex sinusoid at a sampling rate of 100 kHz. When playing the file using the **File Source** the same waveform should be seen. 
[![](https://wiki.gnuradio.org/images/5/53/Reading_binary_files_signal_source.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_signal_source.png)
  
Now run the flowgraph. Notice that the time-domain plot has sinusoidal shapes on the I and Q channels, characteristic of a complex sinusoid. Also notice how the frequency plot displays a tone with a single peak, also characteristic of a complex sinusoid. Finally, notice how the peak of the frequency plot has a peak of approximately 1 kHz confirming that the binary file was read properly and the _samp_rate_ variable was set properly. 
[![](https://wiki.gnuradio.org/images/thumb/2/2e/Reading_binary_files_time_freq_complex_float_display.png/750px-Reading_binary_files_time_freq_complex_float_display.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_time_freq_complex_float_display.png)
## Reading Real Float
To read from a file storing real samples encoded as floating point numbers, open the **File Source** and change the _Output Type_ to float: 
[![](https://wiki.gnuradio.org/images/9/91/Reading_binary_files_select_real_float_type.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_select_real_float_type.png)
Click the three dots next to File and select the file ending in _.real_float_ : 
  
[![](https://wiki.gnuradio.org/images/3/3a/Reading_binary_files_select_real_float_file.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_select_real_float_file.png)
  
Open the **QT GUI Time Sink** properties and change the type to _float_ : 
[![](https://wiki.gnuradio.org/images/d/d2/Reading_binary_files_time_sink_real.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_time_sink_real.png)
  
Open the **QT GUI Freq Sink** properties and change the type to _float_ : 
[![](https://wiki.gnuradio.org/images/6/6a/Reading_binary_files_freq_sink_real.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_freq_sink_real.png)
  
The flowgraph should now look like the following: 
[![](https://wiki.gnuradio.org/images/9/9f/Reading_binary_files_real_float_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_real_float_flowgraph.png)
  
Run the flowgraph. Notice that the time-domain plot displays a single sinusoid, characteristic of a real sinusoid waveform. Also notice that the frequency domain plot displays two peaks, characteristic of a real sinusoid. Finally, notice that the peak on the right hand side, the positive frequencies, is at approximately 1 kHz, confirming that the binary file was read properly and the _samp_rate_ variable is set properly. 
[![](https://wiki.gnuradio.org/images/thumb/8/85/Reading_binary_files_time_freq_real_float_display.png/750px-Reading_binary_files_time_freq_real_float_display.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_time_freq_real_float_display.png)
  

## Reading Real Integers
Begin by adding a **File Source** block. Open the properties and navigate to the file ending in _.real_int_ : 
[![](https://wiki.gnuradio.org/images/b/b5/Reading_binary_files_select_real_int_file.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_select_real_int_file.png)
  
Change the _Output Type_ property to be _short_. **Be sure not to select int** : 
[![](https://wiki.gnuradio.org/images/1/15/Reading_binary_files_select_real_short_type.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_select_real_short_type.png)
  
Add in a Short to Float block and connect it accordingly: 
[![](https://wiki.gnuradio.org/images/4/45/Reading_binary_files_real_int_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_real_int_flowgraph.png)
  
Notice that the scale factor here is set to 1. This will plot all of the values at full scale, which is from −215 to 215−1, or 32,768 to +32767. Running the flowgraph with a scaling value of 1 is valid, although some flowgraphs may use a scale factor in order to normalize the data to be within -1 to +1. Open the **Short to Float** properties and enter a scale factor of 2^15: 
[![](https://wiki.gnuradio.org/images/f/f0/Reading_binary_files_short_to_float_scale_factor.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_short_to_float_scale_factor.png)
  
The **Short to Float** block applies the inverse of the scale factor, meaning it will scale the output samples by 2−15 or 1/32768. The flowgraph will now look like the following: 
[![](https://wiki.gnuradio.org/images/4/4b/Reading_binary_files_real_int_flowgraph_with_scale_factor.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_real_int_flowgraph_with_scale_factor.png)
  
Running the flowgraph displays the file after being read as real integers. The time domain plot displays a single sinusoid which is characteristic of a real sinusoid, and the frequency domain plot displays two tones which is also characteristic of a real sinusoid. Finally, the peak at the positive frequency tone is approximately 1 kHz which confirms that the file is being read correctly. 
[![](https://wiki.gnuradio.org/images/thumb/e/e9/Reading_binary_files_time_freq_real_int.png/750px-Reading_binary_files_time_freq_real_int.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_time_freq_real_int.png)
## Reading Complex Integers
Begin by adding a **File Source** block. Open the properties and navigate to the file ending in _.complex_int_ : 
[![](https://wiki.gnuradio.org/images/d/d6/Reading_binary_files_select_complex_int.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_select_complex_int.png)
  
Open the **File Source** properties and select the short data type. **Do not select the int type** : 
[![](https://wiki.gnuradio.org/images/2/20/Reading_binary_files_select_short_type.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_select_short_type.png)
  
Drag in a **IShort to Complex** block and connect it accordingly. Convert the **QT GUI Time Sink** and **QT GUI Frequency Sink** blocks into the _complex_ data type. The flowgraph should look like the following. 
  
[![](https://wiki.gnuradio.org/images/a/a6/Reading_binary_files_complex_int_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_complex_int_flowgraph.png)
  
Note that the **IShort to Complex** block has a scale factor of 1, which would plot the data on a range of −215 to 215−1, or -32,768 to +32,767. Running the flowgraph in this state is valid. However, some flowgraphs require normalization such that all values are within -1 and +1. To do so, open the block’s properties and use a scale factor of 215: 
[![](https://wiki.gnuradio.org/images/c/c9/Reading_binary_files_ishort_to_complex_scale_factor.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_ishort_to_complex_scale_factor.png)
  
The **IShort to Complex** block will apply the inverse of the scale factor, 2−15 or 1/32768, producing normalized samples from -1 to +1. 
[![](https://wiki.gnuradio.org/images/6/68/Reading_binary_files_complex_int_flowgraph_scale_factor.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_complex_int_flowgraph_scale_factor.png)
  
Run the flowgraph. The time domain plot displays two sinusoids, characteristic of a complex sinusoid. The frequency domain plot displays a single tone, also characteristic of a complex sinusoid. Finally, the tone is at approximate 1 kHz which confirms that the file is being read correctly. 
[![](https://wiki.gnuradio.org/images/thumb/0/05/Reading_binary_files_time_freq_complex_int_display.png/750px-Reading_binary_files_time_freq_complex_int_display.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_time_freq_complex_int_display.png)
## Continuous Playback from File
The **File Source** block comes with the option to repeat playback from file. When _Yes_ is selected for repeat, the samples will be played back on loop until the flowgraph is stopped. 
[![](https://wiki.gnuradio.org/images/7/76/Reading_binary_files_repeat_yes.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_repeat_yes.png)
When _No_ is selected for repeat, then all of the samples will be read from file and then the flowgraph will stop running once the last sample is read and then processed through the flowgraph. 
[![](https://wiki.gnuradio.org/images/6/69/Reading_binary_files_repeat_no.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_repeat_no.png)
## Diagnosing Errors: Wrong Type and Format
In order to properly read a binary file both the type (real or complex) and format (integer or floating point) need to be known. If given a file and the type or format is unknown, it is best to check all possible combinations and to see which is the most reasonable. Endianness (described in the next section) is another potential problem when reading binary files. 
The following are examples of a file being read improperly. Warning: different recordings will present different type and format errors differently, **the images presented here are not exhaustive** and are only a couple of examples to help build intuition to diagnose these kinds of errors. 
The following image is an example of a real integer being read as real floats. Note how large the values are in the time domain: on the order of 1038! Values that are abnormally small or abnormally large clearly indicate the file is not being read correctly. 
[![](https://wiki.gnuradio.org/images/thumb/1/14/Reading_binary_files_real_int_as_real_float.png/750px-Reading_binary_files_real_int_as_real_float.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_real_int_as_real_float.png)
  
The following image is an example of complex floats being read as real floats. This kind of error can be deceptive because both the time domain and frequency domain are reasonable. The time domain has a semi-sinusoidal effect and the frequency domain has a series of peaks. Without knowing the underlying data, it could be reasonable to assume this file is being read correctly. However, it is important to try the different combinations of type and format, and reading the file as complex floats should more clearly reveal the true nature of the file. 
[![](https://wiki.gnuradio.org/images/thumb/6/69/Reading_binary_files_complex_floats_as_real_floats.png/750px-Reading_binary_files_complex_floats_as_real_floats.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_complex_floats_as_real_floats.png)
  
The following image shows the result when a complex floats are read as complex integers. Note that the imaginary portion of the time domain in the red represents a very strange shape which is suggestive that the file is being read incorrectly. Similarly, the frequency domain plot does not display a clearly intelligible signal. 
[![](https://wiki.gnuradio.org/images/thumb/d/d4/Reading_binary_files_complex_float_as_complex_int.png/750px-Reading_binary_files_complex_float_as_complex_int.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_complex_float_as_complex_int.png)
  
The following image is a binary file of real integers being read as complex integers. This one is tricky because at first glance it appears to be tricky, but for a complex sinusoid the real and imaginary data should be pi/2 radians out of phase with one another. Also note that the highlighted frequency is 2 kHz, and not 1 kHz as it should be, another indicator that the file was not read correctly. This is an example of why it it is important to try the different combinations of type and format, such that reading the file as complex integers should allow the user to recognize the signal is being read correctly. 
[![](https://wiki.gnuradio.org/images/thumb/6/66/Reading_binary_files_real_int_as_complex_integers.png/750px-Reading_binary_files_real_int_as_complex_integers.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_real_int_as_complex_integers.png)
## Diagnosing Errors: Endianness
Endianness describes the ordering of the bits,from most significant bit (MSB) to least significant bit (LSB). Different processing architectures use different endianness and that is another factor effecting how binary files are interpreted. Endianness is only a potential problem when dealing with files from different processing systems, and therefore not an issue when performing playback from a capture taken from the same native system. 
The following image is an example of a complex float file being read using the incorrect endianness: 
[![](https://wiki.gnuradio.org/images/thumb/3/34/Reading_binary_files_complex_float_endianness_display.png/750px-Reading_binary_files_complex_float_endianness_display.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_complex_float_endianness_display.png)
  
The values being abnormally large (10^38) is a clear indicator that the file is being read incorrectly. Add the **Endian Swap** block to the flowgraph at the output of the **File Source** : 
[![](https://wiki.gnuradio.org/images/5/56/Reading_binary_files_complex_float_endian_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_complex_float_endian_flowgraph.png)
  
Running the flowgraph now displays the correct result: 
[![](https://wiki.gnuradio.org/images/thumb/b/b1/Reading_binary_files_complex_float_endianness_correct_display.png/750px-Reading_binary_files_complex_float_endianness_correct_display.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_complex_float_endianness_correct_display.png)
  
The following image is an example of real integers being read with the incorrect endianness: 
[![](https://wiki.gnuradio.org/images/thumb/b/ba/Reading_binary_files_real_int_endianness.png/750px-Reading_binary_files_real_int_endianness.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_real_int_endianness.png)
  
This error can be correct by using the **Endian Swap block** and selecting the _short_ data type and connecting it in the flowgraph after the **File Source** : 
[![](https://wiki.gnuradio.org/images/e/e8/Reading_binary_files_real_int_endian_swap_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_real_int_endian_swap_flowgraph.png)
  
Running the updated flowgraph now displays the correct result: 
[![](https://wiki.gnuradio.org/images/thumb/9/90/Reading_binary_files_real_int_endianness_display_correct.png/750px-Reading_binary_files_real_int_endianness_display_correct.png)](https://wiki.gnuradio.org/index.php?title=File:Reading_binary_files_real_int_endianness_display_correct.png)
The next tutorial, [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver"), describes how to build a broadcast FM receiver using an RTL-SDR receiver. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files&oldid=14446](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files&oldid=14446)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Reading+and+Writing+Binary+Files "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Reading_and_Writing_Binary_Files&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files)
  * [View source](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Reading_and_Writing_Binary_Files "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Reading_and_Writing_Binary_Files "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files&oldid=14446 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files&action=info "More information about this page")


  * This page was last edited on 12 June 2024, at 22:34.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


