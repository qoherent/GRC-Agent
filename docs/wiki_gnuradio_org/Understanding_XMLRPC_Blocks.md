# Understanding XMLRPC Blocks
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#searchInput)
This tutorial presents the GNU Radio XMLRPC blocks. There are two blocks in this collection: [**XMLRPC Client**](https://wiki.gnuradio.org/index.php?title=XMLRPC_Client "XMLRPC Client") and [**XMLRPC Server**](https://wiki.gnuradio.org/index.php?title=XMLRPC_Server "XMLRPC Server"). Both blocks have IP address/port fields while the Client block also has callback and variable fields. The blocks use the Python XMLRPC module and use a subset of the full XMLRPC specification. 
XMLRPC is an **XML** -based **R** emote **P** rotocol **C** ontrol mechanism that does just that. It uses HTTP transport and allows a client to use SET commands to change parameters on a server or use GET commands to obtain the value of parameters on the server. 
To understand better how GNURadio implements XMLRPC, look at the block documentation linked in the paragraph above. 
To understand the XMLRPC protocol and Python implementation in detail, the reference links below are a good starting point. 
## Contents
  * [1 Reference Links](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#Reference_Links)
  * [2 Prerequisites](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#Prerequisites)
  * [3 Introduction: _What does XMLRPC do in GNURadio and Why Should I Care?_](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#Introduction:_What_does_XMLRPC_do_in_GNURadio_and_Why_Should_I_Care?)
  * [4 Overview of Tutorial](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#Overview_of_Tutorial)
  * [5 GNURadio XMLRPC Examples](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#GNURadio_XMLRPC_Examples)
  * [6 GNURadio XMLRPC Examples with ZMQ Streaming Data Visualization](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#GNURadio_XMLRPC_Examples_with_ZMQ_Streaming_Data_Visualization)
  * [7 GNURadio XMLRPC Remote Control Over IP Network](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#GNURadio_XMLRPC_Remote_Control_Over_IP_Network)
  * [8 GNURadio XMLRPC Server Automation using Standalone Python Code](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#GNURadio_XMLRPC_Server_Automation_using_Standalone_Python_Code)
  * [9 GNURadio XMLRPC ZMQ Advanced Usage Example](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks#GNURadio_XMLRPC_ZMQ_Advanced_Usage_Example)


## Reference Links
<http://xmlrpc.com>
<https://docs.python.org/3.8/library/xmlrpc.html>
## Prerequisites
  * [Intro to GR usage: GRC and flowgraphs](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_GRC "Guided Tutorial GRC")


  * [Understanding ZMQ Blocks](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks "Understanding ZMQ Blocks")


## Introduction: _What does XMLRPC do in GNURadio and Why Should I Care?_
**WHAT** : In GNURadio placing the XMLRPC Server block in a flowgraph will create an XMLRPC server that exposes all the variables in the flowgraph on the specified IP Address and Port. This allows a client to change them at runtime. The XMLRPC Client block allows the user to create a flowgraph that can control any of the parameters in a flowgraph that has the XMLRPC Server block. 
**WHY** : In many cases, we want to adjust the variables of a GNURadio flowgraph at runtime. We usually do this by adding QT GUI Widget block (range, push button, etc). But what if you want to have the same control interface, but in a different flowgraph? XMLRPC will allow you to control all of a flowgraph's variables from a second flowgraph using HTTP. In fact, the second flowgraph doesn't even have to be on the same computer! Therefore, XMLRPC can be used to add runtime control of any flowgraph running on a remote system like a Raspberry PI, headless server or otherwise. 
## Overview of Tutorial
**GNURadio XMLRPC Examples**
  * We will start with running the basic client/server example flowgraphs from the GNU Radio source tree ([gr-blocks/examples/xmlrpc](https://github.com/gnuradio/gnuradio/tree/maint-3.9/gr-blocks/examples/xmlrpc)).


**GNURadio XMLRPC Examples with added ZMQ Streaming Data Visualization**
  * Here we will modify the examples to include ZMQ streaming so we can visualize the remote flowgraph's datastream in our XMLRPC Client flowgraph.


**GNURadio XMLRPC Remote Control Over IP Network**
  * Next, we will run the server/client pair over an IP network using 2 separate computers. (Requires 2 hosts on the same network with GNURadio 3.9+ installed)


**GNURadio XMLRPC Server Automation using Standalone Python Code**
  * This section demonstrates the ability to automate our server flowgraph using a standalone python application.


**GNURadio XMLRPC Advanced Usage Example (OPTIONAL)**
  * As a bonus step we will run a project that implements a remote, headless wideband RF receiver and stream the data to a Remote Controller for control and visualization.


## GNURadio XMLRPC Examples
In GNURadio Companion: 
Open the [siggen_xmlrpc_server.grc](https://raw.githubusercontent.com/gnuradio/gnuradio/maint-3.9/gr-blocks/examples/xmlrpc/siggen_xmlrpc_server.grc) flowgraph. 
Shown here: 
[![](https://wiki.gnuradio.org/images/5/5c/Xmlrpc_server.png)](https://wiki.gnuradio.org/index.php?title=File:Xmlrpc_server.png)
In GNURadio Companion: 
Open the [siggen_controller_xmlrpc_client.grc](https://raw.githubusercontent.com/gnuradio/gnuradio/maint-3.9/gr-blocks/examples/xmlrpc/siggen_controller_xmlrpc_client.grc) flowgraph 
Shown here: 
[![](https://wiki.gnuradio.org/images/8/89/Xmlrpc_client.png)](https://wiki.gnuradio.org/index.php?title=File:Xmlrpc_client.png)
  
First, click on the tab in GRC that has the Siggen Server Flowgraph and start it by clicking the 'RUN' button. 
Your GUI window should look like this: 
[![](https://wiki.gnuradio.org/images/8/8c/Xmlrpc_server_before.png)](https://wiki.gnuradio.org/index.php?title=File:Xmlrpc_server_before.png)
Next, click on the tab in GRC that has the tab for the the Siggen Controller flowgraph and start it by clicking the 'RUN' button. 
Your GUI window should look like this: 
[![](https://wiki.gnuradio.org/images/f/f2/Xmlrpc_freq_slider.png)](https://wiki.gnuradio.org/index.php?title=File:Xmlrpc_freq_slider.png)
Arrange both GUI windows so you can see them at the same time. 
In the Siggen Controller Flowgraph Move the Frequency Slider slightly to the left and observe the Siggen Server time/frequency display. You should see the frequency changing as shown here: 
[![](https://wiki.gnuradio.org/images/6/67/Xmlrpc_server_after.png)](https://wiki.gnuradio.org/index.php?title=File:Xmlrpc_server_after.png)
That's it, you've just remote controlled a flowgraph over a network socket! 
Close both GUI windows and continue to the next section. 
## GNURadio XMLRPC Examples with ZMQ Streaming Data Visualization
Make sure you are familiar with ZMQ streaming from the [ZMQ tutorials](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks "Understanding ZMQ Blocks"). 
  * Create a copy ('save as') of both XMLRPC example flowgraphs and name them:


```
   "siggen_xmlrpc_server_streaming.grc"
   "siggen_controller_xmlrpc_client_streaming.grc"

```

(don't forget to change the names in the 'ID' of the 'Options' block to match the new .grc filenames) 
  * In the Streaming Server Flowgraph, copy the throttle and GUI display blocks, then paste them into the Streaming Controller flowgraph.


[![](https://wiki.gnuradio.org/images/4/4b/Xmlrpc_client_streaming_add_GUI.png)](https://wiki.gnuradio.org/index.php?title=File:Xmlrpc_client_streaming_add_GUI.png)
  * Now delete the GUI blocks from the Server Flowgraph and add a ZMQ Pub Sink block. Connect the output of the 'Throttle' block to the input of the ZMQ Pub Sink where the GUI blocks used to be connected.


```
   ZMQ PUB SINK PARAMETERS:
       'Address': tcp://127.0.0.1:5000
       'Pass Tags': 'Yes'
       All others default

```

  * Change the 'Generate Options' in the Server flowgraph to 'No GUI'. This will allow us to run the flowgraph on our localhost or a remote host as a 'headless' flowgraph.


[![](https://wiki.gnuradio.org/images/4/4f/Xmlrpc_server_streaming_add_ZMQ.png)](https://wiki.gnuradio.org/index.php?title=File:Xmlrpc_server_streaming_add_ZMQ.png)
  

  * In the Streaming Client flowgraph.


Change the sample rate to match the Streaming Server Flowgraph (32ksps) 
Add a ZMQ Sub Source block to the flowgraph and connect it to the input port of both the 'QT GUI Time Sink' and 'QT GUI Frequency Sink' GUI blocks. 
[![](https://wiki.gnuradio.org/images/4/4c/Xmlrpc_client_streaming_add_ZMQ.png)](https://wiki.gnuradio.org/index.php?title=File:Xmlrpc_client_streaming_add_ZMQ.png)

```
   ZMQ SUB SOURCE PARAMETERS:
       'Address': tcp://127.0.0.1:5000
       'Pass Tags': 'Yes'
       All others default

```

  * RUN IT!


From GRC: Click the run button on the 'siggen_xmlrpc_server_streaming' flowgraph. A blank terminal window will open (don't worry its doing it's job). 
Click the run button on the 'siggen_xmlrpc_controller_client_streaming' flowgraph. A GUI window will open with the same time/frequency display as before, but in the same graph with the GUI Range 'frequency' slider. 
In the Controller GUI: Adjust the slider as before and observe the frequency changing. 
[![](https://wiki.gnuradio.org/images/7/74/Xmlrpc_client_streaming_complete.png)](https://wiki.gnuradio.org/index.php?title=File:Xmlrpc_client_streaming_complete.png)
You are now both remote controlling and viewing streaming data over a network connection! 
## GNURadio XMLRPC Remote Control Over IP Network
  * Extending this example to two hosts using one of two methods.


Assume our Streaming Server machine is Host A and our Streaming Client machine is Host B. 
**NOTE: These tutorials are focused on functionality not security. Understanding of the IP mechanisms used in this tutorial fall on the user. Know your network!**
  * **METHOD 1**


SSH Port Forwarding (more information can be found [here](https://www.ssh.com/academy/ssh/tunneling/example) Using SSH Port Forwarding, a user can keep everything the same in the flowgraphs. We are using port 8000 for XMLRPC and Port 5000 for our ZMQ Streaming Data. Simply run the following command from Host B: 

```
   ssh -L 8000:localhost:8000 -L 5000:localhost:5000 <username>@<IP Address of Host A>

```

This SSH command effectively shares any traffic on ports 8000 and 5000 between the two hosts. 
  

  * **METHOD 2**


Changing the IP Addresses in both flowgraphs: 
  * Streaming Server Flowgraph (Host A)


ZMQ Pub Sink Address <IP Address of Host B -OR- '0.0.0.0'>
XMLRPC Server <IP Address of Host A -OR- '0.0.0.0'>
  * Streaming Client Flowgraph (Host B)


ZMQ Sub Source Address <IP Address of host A>
XMLRPC Client Block (frequency) Address <IP Address of host A>
## GNURadio XMLRPC Server Automation using Standalone Python Code
XMLRPC is extremely simple to use in Python. The following section will give a simple example of this by changing the frequency of the server flowgraph from a list of frequencies in a python script. 
If we run our original siggen_xmlrpc_server.grc flowgraph. We should see the same gui window pop up again as in the first part of this tutorial. 
Now instead of running our client flowgraph to manually control the frequency, we will automate the frequency change with a python script. 
This script will change the frequency of the signal generator block every 2 seconds. The frequency is determined by a list which our script will iterate through. 
With the server running, simply paste this code into a script called 'flowgraph_automator.py' 

```
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from xmlrpc.client import ServerProxy
import time
xmlrpc_control_client = ServerProxy('http://'+'localhost'+':8000')
freq_steps = [6e3, 11e3, 2e3, 14e3, 4e3, 3.5e3]
while True:
    for freq in freq_steps:
        print("retuning to:",freq/1000,"kHz")
        xmlrpc_control_client.set_rmt_freq(freq)
        time.sleep(2)

```

Open a terminal and run the script with `$ python3 flowgraph_automator.py` As soon as you run the python script, you will see terminal output displaying the frequency change. If you look in the GUI window for the server, you will see the frequency shifting every 2 seconds! 
## GNURadio XMLRPC ZMQ Advanced Usage Example
**Remote Spectrum Monitoring Example using Hardware**
This portion of the tutorial uses two open source projects that implement the concepts shown above to provide wideband remote spectrum monitoring over a low data rate IP connection. 
This tutorial assumes you have one of the following Software Defined Radios: 
  * Ettus Research USRP
  * Lime Micro LimeSDR
  * RTL-SDR


This tutorial can be run from the same computer, but is intended to be used on two separate machines. Both machines require GNURadio 3.9+ and the proper driver for the radio. In the case of the USRP, UHD is required. For either the LimeSDR or the RTLSDR, GNURadio 3.9+ includes SoapySDR drivers for both. 
Clone the following two repositories on the remote host: 
  * <https://github.com/muaddib1984/stillsuit>


  * <https://github.com/muaddib1984/arrakis>


Clone only the arrakis repository on the local host: 
  * <https://github.com/muaddib1984/arrakis>


  
**REMOTE HOST**
  * Attach one of the SDR's listed above to your remote host. Ensure your radio is connected to the host by running either:


For USRP 

```
uhd_find_devices
```

For LimeSDR/RTLSDR 

```
SoapySDRUtil --find
```

  * From your local host, open an SSH tunnel to the remote host with Local Port Forwarding:


`ssh -L 5001:localhost:5001 -L 8001:localhost:8001 -L 8000:localhost:8000 <username>@<hostname(or IP Address)>`
EXAMPLE: If my username on the remote host is 'user' and the remote host's ip address is 192.168.1.100, from my local host I would run: `ssh -L 5001:localhost:5001 -L 8001:localhost:8001 -L 8000:localhost:8000 user@192.168.1.100`
  * Now you are connected via SSH to the remote terminal.


  * If you prefer to have only one terminal window open, from the SSH terminal, run:


`cd path/to/stillsuit` `./<uhd|lime|rtl>_stillsuit.py &` (this will allow you to run the second application from the same terminal) 
  * If you prefer to have two terminals, you can open a second SSH session, but leave off the '-L' arguments since we already have the ports forwarded in the first shell.
  * With two terminals, you can also omit the "&" in the previous command.


  * Whether in one or two terminal windows run:


`cd path/to/arakkis`   
`space_folder.py` note: If you are using an RTLSDR, pass the -s argument to space_folder.py with a value of 2.5e6 like this   
`space_folder.py -s 2.5e6`
  
**LOCAL HOST**
  * Now open a terminal window on your local machine and run:


`cd path/to/arrakis`   
`./guild_navigator.py` note: If you are using an RTLSDR, pass the -s argument to guild_navigator.py with a value of 2.5e6 like this   
`guild_navigator.py -s 2.5e6`
  * You should see a frequency plot updating at a fairly slow update rate.
  * To make the frequency plot update faster, change the vectors per second dropdown menu to 8.
  * To make the spectrum more stable visually, change the 'Average/Raw' dropdown to 'AVERAGED'.
  * If using a USRP/Lime, you can adjust the sampling rate to 40MHz (performance will be dependent on your remote host's processing power.)
  * Finally, tune the frequency to 98000000.0 Hz in the frequency window (FM Radio).


Your plot should look something like this: 
[![](https://wiki.gnuradio.org/images/b/b5/Guild_navigator_fm.png)](https://wiki.gnuradio.org/index.php?title=File:Guild_navigator_fm.png)
To measure the data usage from one host to another install an application like 'iftop'   
`sudo apt install iftop` and run it like this:   
`sudo iftop -i lo -P` Port 5000 will have the throughput usage and look similar to this with the above settings: 

```
             191Mb         381Mb         572Mb         763Mb    954Mb
└────────────┴─────────────┴─────────────┴─────────────┴─────────────
localhost:5001       => localhost:59814       2.01Mb  2.06Mb  2.01Mb

```

This is a powerful capability because we are getting a very human readable frequency plot of live RF Spectrum, while only using a small amount of network data to get it from the remote host! Furthermore, XMLRPC is allowing us to **remotely** adjust all of the parameters of the SDR at runtime! 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks&oldid=12150](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks&oldid=12150)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Understanding+XMLRPC+Blocks "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Understanding_XMLRPC_Blocks&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks)
  * [View source](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Understanding_XMLRPC_Blocks "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Understanding_XMLRPC_Blocks "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks&oldid=12150 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Understanding_XMLRPC_Blocks&action=info "More information about this page")


  * This page was last edited on 18 March 2022, at 14:41.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


