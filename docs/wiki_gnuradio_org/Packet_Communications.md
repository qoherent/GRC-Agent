# Packet Communications
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Packet_Communications#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Packet_Communications#searchInput)
**NOTE: This document has been revised. The previous version can be found in[Packet_Communications_Test_Page](https://wiki.gnuradio.org/index.php?title=Packet_Communications_Test_Page "Packet Communications Test Page").**  

This tutorial presents methods to transmit and receive packet data. As was shown in the QPSK and BPSK tutorials, the received bit data stream could be verified by comparing it to the (delayed) transmitted data. However, there was no way to recover the byte alignment of those bits. The demodulation chain cannot determine the start or end of transmission, nor determine bit errors. That is where packet processing, or more accurately frame processing, comes into play.   
  

## Contents
  * [1 Prerequisites](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Prerequisites)
  * [2 Header Format Object](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Header_Format_Object)
  * [3 Simulating Packet Comms Using Messages](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Simulating_Packet_Comms_Using_Messages)
    * [3.1 Building the flowgraph](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Building_the_flowgraph)
    * [3.2 Testing](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Testing)
  * [4 Simulating Packet Comms Using Streams](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Simulating_Packet_Comms_Using_Streams)
    * [4.1 Building the flowgraph](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Building_the_flowgraph_2)
    * [4.2 Testing](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Testing_2)
      * [4.2.1 Create an input file](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Create_an_input_file)
      * [4.2.2 Executing the flowgraph](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Executing_the_flowgraph)
  * [5 Observations](https://wiki.gnuradio.org/index.php?title=Packet_Communications#Observations)


## Prerequisites
  * [**QPSK Modulation / Demodulation**](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_PSK_Demodulation "Guided Tutorial PSK Demodulation")
  * [**BPSK Demodulation**](https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation "Simulation example: BPSK Demodulation")
  * [**Polymorphic Types (PMTs)**](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\) "Polymorphic Types \(PMTs\)")
  * [**Stream Tags**](https://wiki.gnuradio.org/index.php?title=Stream_Tags "Stream Tags")
  * [**Message Passing**](https://wiki.gnuradio.org/index.php?title=Message_Passing "Message Passing")


## Header Format Object
A packet header is commonly used to aid in demodulation. The header often contains a sync word or access code to determine the start of transmission. It may also contain a payload length to determine the end of transmission. Because headers can vary greatly depending on the use case, GNU Radio provides header format objects to help create them in a flexible manner. There are two such formatters, header_format_base and packet_header_default. 
Users who need custom-defined headers may inherit from header_format_base and provide their own formatting and parsing methods. In addition, GNU Radio provides a few concrete format objects readily available. To create such an object, a [Variable](https://wiki.gnuradio.org/index.php?title=Variable "Variable") block is used. The 'Value' field can be one of several choices depending on what type of header is needed, such as: 
  * digital.header_format_default(access_code, threshold)
  * digital.header_format_crc(len_key, num_key)
  * digital.header_format_counter(access_code, threshold, bps)


The following blocks use formatters derived from header_format_base: 
  * [Protocol Formatter](https://wiki.gnuradio.org/index.php?title=Protocol_Formatter "Protocol Formatter")
  * [Protocol Formatter (Async)](https://wiki.gnuradio.org/index.php?title=Protocol_Formatter_\(Async\) "Protocol Formatter \(Async\)")
  * [Protocol Parser](https://wiki.gnuradio.org/index.php?title=Protocol_Parser "Protocol Parser")
  * [Default Header Format Obj.](https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj. "Default Header Format Obj.")


The second type of format object derives from packet_header_default. This is an older version, but still worth mentioning because it is used by the OFDM transmitter and receiver. The following blocks use the older formatter: 
  * [Packet Header Generator](https://wiki.gnuradio.org/index.php?title=Packet_Header_Generator "Packet Header Generator")
  * [Packet Header Generator (Default)](https://wiki.gnuradio.org/index.php?title=Packet_Header_Generator_\(Default\) "Packet Header Generator \(Default\)")
  * [Packet Header Parser](https://wiki.gnuradio.org/index.php?title=Packet_Header_Parser "Packet Header Parser")
  * [Packet Header Parser (Default)](https://wiki.gnuradio.org/index.php?title=Packet_Header_Parser_\(Default\) "Packet Header Parser \(Default\)")


## Simulating Packet Comms Using Messages
In order to grasp the basics of packet processing, this section presents a transmitter and receiver simulation using standard GNU Radio blocks without any modulation or channel impairments. We describe the blocks and give subsequent hints on how to implement modulation and demodulation. 
### Building the flowgraph
Build the following flowgraph using the details given below: 
[![](https://wiki.gnuradio.org/images/thumb/f/f6/Pkt_8_fg.png/800px-Pkt_8_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Pkt_8_fg.png)
* * *
This flowgraph can be downloaded from [Media:Pkt_8.grc](https://wiki.gnuradio.org/images/9/96/Pkt_8.grc "Pkt 8.grc").  

Message Strobe

For the Message Strobe to generate a PDU, the Message PMT must be of the form 

```
 pmt.cons(pmt.PMT_NIL,pmt.init_u8vector(9,(71,78,85,32,82,97,100,105,111)))

```

This specific vector has a length of 9 and the character values of "GNU Radio". It is sent twice per second.  

CRC Append

The CRC Append block computes the CRC32 of the payload (message) and appends the value to it.  

Protocol Formatter (Async)

The Protocol Formatter block receives the payload as input and generates a header according to the Format Object. It produces separate header and payload output messages, allowing unique modulation if desired. 
The Format Obj. parameter is set to the variable hdr_format. This variable is created by a Variable Block with ID hdr_format and value digital.header_format_default(access_key, 0). Similarly, the variable access_key is created by another Variable Block with ID access_key and value of a 32-bit string of ones and zeros. This formatter generates a header with the access code and 16-bit payload length (repeated).  

PDU to Tagged Stream

Converts received PDUs into a tagged stream by adding length tags.  

Tagged Stream Mux

The Tagged Stream Mux combines the header and the payload into a single stream.  

Virtual Sink and Source (packet_bytes)

The virtual sink and source blocks with Stream ID "packet_bytes" represents the entire packet including the header, payload, and CRC. It is a tagged stream of packed bytes with length tag "packet_len". At the output of the virtual source, the packet is ready for modulation and transmission.  

Repack Bits

This block is used to unpack the packet bytes as needed for the Correlate Access Code - Tag Stream block. This block represents the entire modulation, transmission, reception, and demodulation chain. Most GNU Radio modulation blocks take packed bytes as input, and their demodulation blocks produce unpacked bytes as output. Therefore, once modulation and demodulation is implemented, this block may need to be removed, depending on the modem.  

Throttle

Since this flowgraph does not contain hardware blocks, the throttle block is used to slow down processing for the CPU. If implementing real SDR hardware, this block should be removed.  

Virtual Sink and Source (demod_bits)

The second pair of virtual sink and source contains the Stream ID "demod_bits". These blocks represent the demodulated bits streamed as unpacked bytes.  

Correlate Access Code - Tag Stream

This block searches the input stream for a match to the given Access Code. It reads the payload length field of the header and produces the desired payload with CRC. The access code is given as a _string_ with the 32-bit value (ones and zeros). The output is a tagged stream with length tag "packet_len".  

Tagged Stream to PDU

Converts the payload bytes from tagged stream to PDU message.  

CRC Check

The CRC Check block computes the CRC of the payload and compares it to the CRC in the message. If they match, the payload is output to the "ok" port with the CRC stripped from the message. If the CRC does not match, the entire message with CRC is output to the "fail" port. 
* * *
### Testing
1. Open a new terminal window.  
2. Create a working directory, e.g. `~/GRdev`: 

```
   mkdir ~/GRdev

```

3. Change to that directory: 

```
   cd ~/GRdev

```

4. Assuming that you downloaded `Pkt_8.grc`, copy it to the working directory: 

```
   cp ~/Downloads/Pkt_8.grc ./pkt_8.grc

```

5. Compile `pkt_8.grc`: 

```
   grcc pkt_8.grc

```

6. Execute `pkt_8`: 

```
   python3 -u pkt_8.py

```

7. A new window will open showing two Time Sinks which are the input and output of the 'Correlate Access Code - Tag Stream' block.  
8. Once the Correlate Output shows a signal, wait one second, then terminate that window (titled `pkt_8`) by clicking the "X" in the upper right corner.  
9. The terminal screen should show repeats of this message:  


```
***** VERBOSE PDU DEBUG PRINT ******
()
pdu length =          9 bytes
pdu vector contents = 
0000: 47 4e 55 20 52 61 64 69 6f 
************************************

```

The "pdu vector contents" are the character codes for "GNU Radio". 
## Simulating Packet Comms Using Streams
### Building the flowgraph
Build the following flowgraph using the details given below: 
[![](https://wiki.gnuradio.org/images/thumb/e/e7/Str_pkt_10_fg.png/800px-Str_pkt_10_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Str_pkt_10_fg.png)
* * *
This flowgraph can be downloaded from [Media:Str_pkt_10.grc](https://wiki.gnuradio.org/images/c/ce/Str_pkt_10.grc "Str pkt 10.grc").  

File Source

The File Source reads a padded text file and outputs to a stream.  

Stream to Tagged Stream

Converts a regular stream into a tagged stream by adding length tags in regular intervals.  

Stream CRC32

The Stream CRC32 block computes the CRC of the payload and appends the value to it.  

Protocol Formatter

The entry for the Format Obj. is 'hdr_format'. The Protocol Formatter produces the header for the packet.  

Tagged Stream Mux

The Mux combines the protocol header and the payload data into one packet.  

Correlate Access Code - Tag Stream

The Access Code is a _string_ with the 32 bit value. 
* * *
### Testing
#### Create an input file
The success of this methodology is based on the input file being padded to a multiple of 'packet_len' bytes so that the last few bytes of the source file are not lost in a partial unprocessed packet. To create a padded file for input to the flowgraph, the following Python program can be used: 

```
#!/usr/bin/python3
# -*- coding: utf-8 -*-

# Padded_File_Source.py

import os.path
import sys

Pkt_len = 252
_debug = 1          # set to zero to turn off diagnostics

if (len(sys.argv) < 2):
    print ('Usage: python3 Padded_File_Source.py <input file>')
    print ('Number of arguments=', len(sys.argv))
    print ('Argument List:', str(sys.argv))
    exit (1)
# test if file exists
fn = sys.argv[1]
if (_debug):
    print (fn)
if not(os.path.exists(fn)):
    print('The input file does not exist')
    exit (1)

# open input file
f_in = open (fn, 'r')

# open output file
f_out = open ("padded.txt", 'w')

while True:
    buff = f_in.read (Pkt_len)
    b_len = len(buff)
    if (_debug):
        print (b_len)
    if b_len == 0:
        print ('End of file')
        break
    while (b_len < Pkt_len):
        buff += ' '
        b_len += 1
    # write output
    f_out.write (buff)

f_in.close()
f_out.close()

```

Note that the 'Pkt_len' in the Python program and the 'packet_len' value in the flowgraph 'Stream to Tagged Stream' block must be the same. The output of the Python program is a file named "padded.txt". 
1. In a terminal window change to `~/GRdev`: 

```
   cd ~/GRdev

```

2. Copy the `Padded_File_Source.py` file above into a text file in `~/GRdev`.  
3. Create a padded text file: 

```
   python3 Padded_File_Source.py <input text file>

```

Choose a text file which is not very long and not a multiple of 252 bytes. 
#### Executing the flowgraph
4. Assuming that you downloaded `Str_pkt_10.grc`, copy it to the working directory: 

```
   cp ~/Downloads/Str_pkt_10.grc ./str_pkt_10.grc

```

5. Execute `gnuradio-companion`. 

```
   gnuradio-companion

```

6. Open the `str_pkt_10.grc` flowgraph.  
7. Right click on the 'File Source' block and select Properties.  
8. Change the File path to your 'padded.text'. (Your user name will be different!)  
9. Click 'Apply' and 'OK'.  
10. Click 'Generate the flowgraph' or press F5.  
11. Exit `gnuradio-companion` by clicking the "X" in the upper right corner of the screen.  
12. On the same terminal screen, execute `str_pkt_10`: 

```
   python3 -u str_pkt_10.py

```

13. A new window will open showing two Time Sinks which are the input and output of the Correlate Access Code - Tag Stream block.  
14. Once the Correlate Output signal quits changing, the file transfer has finished. Terminate that window (titled `str_pkt_10`) by clicking the "X" in the upper right corner.  
15. Examine the output file "output.txt". The output file will be the same as the original file, but will have trailing spaces at the end as a result of the padding.  

## Observations
During the development and testing for this tutorial, the following items were observed: 
  * The 'Header/Payload Demux' and 'Protocol Parser' blocks seem to work only with the 'digital.header_format_crc' format.
  * The 'Correlate Access Code - Tag Stream' block requires headers of type header_format_default and works best with an access code of 32 or 64 bits.


* * *
`Tested with v3.10.9.2`  

Retrieved from "[https://wiki.gnuradio.org/index.php?title=Packet_Communications&oldid=15434](https://wiki.gnuradio.org/index.php?title=Packet_Communications&oldid=15434)"
[Categories](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Tutorials](https://wiki.gnuradio.org/index.php?title=Category:Tutorials "Category:Tutorials")
  * [Tested With 3.10](https://wiki.gnuradio.org/index.php?title=Category:Tested_With_3.10 "Category:Tested With 3.10")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Packet+Communications "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Packet_Communications "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Packet_Communications "Discussion about the content page \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Packet_Communications)
  * [View source](https://wiki.gnuradio.org/index.php?title=Packet_Communications&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Packet_Communications&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Packet_Communications "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Packet_Communications "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Packet_Communications&oldid=15434 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Packet_Communications&action=info "More information about this page")


  * This page was last edited on 6 November 2025, at 18:37.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


