# Understanding ZMQ Blocks
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#searchInput)
This tutorial presents the GNU Radio ZMQ blocks. It is a set of six Source Blocks and six Sink Blocks. The naming convention follows other source and sink blocks in that a source block provides data entering a GNU Radio flowgraph and a sink block sends data out of the flowgraph. It is a flowgraph-oriented perspective. 
From the [ZeroMQ](https://zeromq.org/) website: "ZeroMQ (also known as ØMQ, 0MQ, or zmq) looks like an embeddable networking library but acts like a concurrency framework. It gives you sockets that carry atomic messages across various transports like in-process, inter-process, TCP, and multicast." 
## Contents
  * [1 Prerequisites](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Prerequisites)
  * [2 Types of ZMQ Blocks](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Types_of_ZMQ_Blocks)
    * [2.1 Data Blocks](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Data_Blocks)
    * [2.2 Message Blocks](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Message_Blocks)
  * [3 Using ZMQ Blocks](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Using_ZMQ_Blocks)
    * [3.1 TCP Bind vs Connect](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#TCP_Bind_vs_Connect)
      * [3.1.1 TCP Bind](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#TCP_Bind)
      * [3.1.2 TCP Connect](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#TCP_Connect)
    * [3.2 Wire Format](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Wire_Format)
  * [4 Examples](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Examples)
    * [4.1 Separate GR flowgraphs on Same Computer](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Separate_GR_flowgraphs_on_Same_Computer)
    * [4.2 Separate GR flowgraphs on Different Computers](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Separate_GR_flowgraphs_on_Different_Computers)
    * [4.3 Python Program as a REQ / REP Server](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Python_Program_as_a_REQ_/_REP_Server)
    * [4.4 Python Program as a PUSH / PULL Server](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Python_Program_as_a_PUSH_/_PULL_Server)
    * [4.5 Python Program to Process Flowgraph Data](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks#Python_Program_to_Process_Flowgraph_Data)


## Prerequisites
  * [**Intro to GR usage: GRC and flowgraphs**](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_GRC "Guided Tutorial GRC")
  * [**Understanding sample rate**](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial "Sample Rate Tutorial")


## Types of ZMQ Blocks
For GNU Radio, the two basic groups of ZMQ blocks are those which transport stream data, and those which transport messages. They are described below. 
ZMQ blocks come in pairs: 
  * PUB - SUB
  * PUSH - PULL
  * REQ - REP


The PUB, PUSH, and REP blocks are always sink blocks; the others are source blocks. Choosing which pair to use depends on your system architecture. 
  * The PUB - SUB pair can be compared to broadcasting. The PUBlish sink sends out data which can be received by one or more SUBscribers.
  * The PUSH - PULL pair is a point to point link of equal peers.
  * The REQ - REP pair is a point to point link which operates in lock-step: one REQuest input gives one REPly output. This case changes the perspective somewhat in that the flowgraph is acting as a server for a remote client.


### Data Blocks
ZMQ data blocks transport raw stream data; there is no formatting. The data type and the sample rate are determined by the flowgraph feeding the ZMQ Sink. Therefore the flowgraph or program receiving the data must know those parameters in order to interpret the data correctly. 
### Message Blocks
Unlike the generic ZeroMQ strings, GNU Radio ZMQ Message Blocks utilize [Polymorphic_Types_(PMTs)](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\) "Polymorphic Types \(PMTs\)") to encode/decode the data. See also [Message_Passing](https://wiki.gnuradio.org/index.php?title=Message_Passing "Message Passing"). 
## Using ZMQ Blocks
Users of ZMQ blocks are expected to have some familiarity with ZeroMQ. In particular, one should be cognizant of the differences between ZMQ sockets and BSD sockets. See the [ZMQ Socket API](https://zeromq.org/socket-api/) for an overview. 
ZMQ blocks use **endpoints** to describe how ZMQ should pass the data. While the most common endpoint uses TCP to transfer the data, other protocols are possible. See [zmq_tcp](http://api.zeromq.org/master:zmq_tcp) and [zmq_ipc](http://api.zeromq.org/master:zmq-ipc) for a description of how each protocol defines endpoints. 
The cases described below explain the differences in the block addressing. To conform to port addressing defined by the [Internet Assigned Numbers Authority (IANA)](https://www.iana.org/), private ports can be assigned in the range 49152–65535. Within a single flowgraph using ZMQ blocks is not recommended, since [Virtual_Source](https://wiki.gnuradio.org/index.php?title=Virtual_Source "Virtual Source") and [Virtual_Sink](https://wiki.gnuradio.org/index.php?title=Virtual_Sink "Virtual Sink") blocks are much more efficient. 
### TCP Bind vs Connect
Some users might be tempted to connect directly to GNU Radio ZMQ Blocks. While this is possible, some caution is needed. First be aware that in any topology, there must be **exactly one** `bind` to a given endpoint, while there may be multiple `connect`s to the same endpoint. In GNU Radio, stream **sinks** `bind` and stream **sources** `connect`. Message blocks accept a parameter that specify whether the block should `bind` or `connect`. 
Also be aware that the semantics of TCP endpoints vary between `bind` and `connect`. 
#### TCP Bind
When binding a TCP endpoint, you specify where you will listen for connections. If you specify an IP address, you are telling the socket to only accept connections on the network associated with that address (e.g., `127.0.0.1` or `192.168.1.123`). In some cases, you may want to listen on all networks connected to your node. For GNU Radio, you should use `0.0.0.0` as the wildcard address; although ZMQ does accept `*` as a wildcard, it doesn't work well in all cases. So, you may choose to `bind` to `tcp://0.0.0.0:54321`. 
Be aware that if you don't enter an IP address, `bind` treats the value as a network adapter name (e.g., `eth0`). See [zmq_tcp](http://api.zeromq.org/master:zmq_tcp). **Bind to`tcp://localhost:1234` will not do what you think!** Use either `tcp://127.0.0.1:1234` or `tcp://lo:1234` (for the loopback network adapter). 
#### TCP Connect
When connecting a TCP endpoint, you specify the remote endpoint you want to connect to. You can specify either an IP address or a DNS resolvable name. This difference in semantics between _connect_ and _bind_ is confusing, but must be honored, in order to have your flowgraph communicate as you expect. The simplest solution is to use IP addresses everywhere, but that is not an option in some configurations (e.g., deploying into a Kubernetes cluster). 
### Wire Format
The ZMQ stream blocks have the option to pass tags. In addition, the PUB/SUB blocks support filtering. Both of these options affect the ZMQ wire protocol. 
When a filter string is supplied to a PUB/SUB block, GNU Radio uses [multi-part messages](https://zeromq.org/messages/) to send the filter string, followed by the payload. Non-GNU Radio code attempting to interface with GNU Radio ZMQ blocks must be prepared for this part, and discard it. Note that the sender only sends this message part if a non-empty filter has been specified. 
Next, if sending tags is enabled, any tags within the window of the data to be sent are encoded in a special format and _prepended_ to the payload data. If tags are not enabled, this header is elided. 
These two features make matching the sender configuration to the receiver configuration essential. Failure to do so will cause runtime errors in your flowgraph. 
## Examples
### Separate GR flowgraphs on Same Computer
When the ZMQ blocks are in separate flowgraphs but on the same computer, the IP address should be `127.0.0.1` for localhost. It has less overhead than a full IP. 
These flowgraphs, using the PUB / SUB pair, are taken from [Simulation_example:_AM_transmitter_and_receiver](https://wiki.gnuradio.org/index.php?title=Simulation_example:_AM_transmitter_and_receiver "Simulation example: AM transmitter and receiver"). 
[![](https://wiki.gnuradio.org/images/thumb/2/2c/AM_transmit_fg.png/800px-AM_transmit_fg.png)](https://wiki.gnuradio.org/index.php?title=File:AM_transmit_fg.png)
* * *
[![](https://wiki.gnuradio.org/images/thumb/7/74/AM_receive_fg.png/800px-AM_receive_fg.png)](https://wiki.gnuradio.org/index.php?title=File:AM_receive_fg.png)
### Separate GR flowgraphs on Different Computers
If the Source and Sink blocks are on two different computers, then the IP and port number of the Sink block must be specified on each end of that connection. For example, if the Sink is on IP `192.168.1.194:50241` and the Source is on IP `192.168.1.85`, both Source and Sink blocks must specify the Sink IP and port `192.168.1.194:50241`. 
[![](https://wiki.gnuradio.org/images/6/60/ZMQ_PUSH_msg_test_fg.png)](https://wiki.gnuradio.org/index.php?title=File:ZMQ_PUSH_msg_test_fg.png)
* * *
[![](https://wiki.gnuradio.org/images/b/bb/PULL_msg_test_fg.png)](https://wiki.gnuradio.org/index.php?title=File:PULL_msg_test_fg.png)
### Python Program as a REQ / REP Server
The following Python program receives a string message on its **REQ** uest socket, capitalizes the text, and sends the string on its **REP** ly socket. The terminology gets confusing here because the incoming REQ came from a GR [ZMQ_REP_Message_Sink](https://wiki.gnuradio.org/index.php?title=ZMQ_REP_Message_Sink "ZMQ REP Message Sink") and is returned to a [ZMQ_REQ_Message_Source](https://wiki.gnuradio.org/index.php?title=ZMQ_REQ_Message_Source "ZMQ REQ Message Source"). Just remember that a **sink** is the terminating point of a flowgraph (swallows all data) and a **source** is the origin of a flowgraph (produces or ingests data). 

```
#!/usr/bin/python3
# -*- coding: utf-8 -*-

# zmq_REQ_REP_server.py

# This server program capitalizes received strings and returns them.
# NOTES:
#   1) To comply with the GNU Radio view, messages are received on the REQ socket and sent on the REP socket.
#   2) The REQ and REP messages must be on separate port numbers.

import pmt
import zmq

_debug = 0          # set to zero to turn off diagnostics

# create a REQ socket
_PROTOCOL = "tcp://"
_SERVER = "127.0.0.1"          # localhost
_REQ_PORT = ":50246"
_REQ_ADDR = _PROTOCOL + _SERVER + _REQ_PORT
if (_debug):
    print ("'zmq_REQ_REP_server' version 20056.1 connecting to:", _REQ_ADDR)
req_context = zmq.Context()
if (_debug):
    assert (req_context)
req_sock = req_context.socket (zmq.REQ)
if (_debug):
    assert (req_sock)
rc = req_sock.connect (_REQ_ADDR)
if (_debug):
    assert (rc == None)

# create a REP socket
_PROTOCOL = "tcp://"
_SERVER = "127.0.0.1"          # localhost
_REP_PORT = ":50247"
_REP_ADDR = _PROTOCOL + _SERVER + _REP_PORT
if (_debug):
    print ("'zmq_REQ_REP_server' version 20056.1 binding to:", _REP_ADDR)
rep_context = zmq.Context()
if (_debug):
    assert (rep_context)
rep_sock = rep_context.socket (zmq.REP)
if (_debug):
    assert (rep_sock)
rc = rep_sock.bind (_REP_ADDR)
if (_debug):
    assert (rc == None)

while True:
    #  Wait for next request from client
    data = req_sock.recv()
    message = pmt.to_python(pmt.deserialize_str(data))
    print("Received request: %s" % message)

    output = message.upper()

    #  Send reply back to client
    rep_sock.send (pmt.serialize_str(pmt.to_pmt(output)))

```

### Python Program as a PUSH / PULL Server
Similar to the example above, the following Python program receives a string message on its ZMQ PULL socket, capitalizes the text, and returns the string on its ZMQ PUSH socket. 

```
#!/usr/bin/python3
# -*- coding: utf-8 -*-

# zmq_PUSH_PULL_server.py

import sys
import pmt
import zmq

_debug = 0          # set to zero to turn off diagnostics

# create a PUSH socket
_PROTOCOL = "tcp://"
_SERVER = "127.0.0.1"          # localhost
_PUSH_PORT = ":50252"
_PUSH_ADDR = _PROTOCOL + _SERVER + _PUSH_PORT
if (_debug):
    print ("'zmq_PUSH_PULL_server' version 20068.1 binding to:", _PUSH_ADDR)
push_context = zmq.Context()
if (_debug):
    assert (push_context)
push_sock = push_context.socket (zmq.PUSH)
if (_debug):
    assert (push_sock)
rc = push_sock.bind (_PUSH_ADDR)
if (_debug):
    assert (rc == None)

# create a PULL socket
_PROTOCOL = "tcp://"
_SERVER = "127.0.0.1"          # localhost
_PULL_PORT = ":50251"
_PULL_ADDR = _PROTOCOL + _SERVER + _PULL_PORT
if (_debug):
    print ("'zmq_PUSH_PULL_server' connecting to:", _PULL_ADDR)
pull_context = zmq.Context()
if (_debug):
    assert (pull_context)
pull_sock = pull_context.socket (zmq.PULL)
if (_debug):
    assert (pull_sock)
rc = pull_sock.connect (_PULL_ADDR)
if (_debug):
    assert (rc == None)

while True:
    #  Wait for next request from client
    data = pull_sock.recv()
    message = pmt.to_python(pmt.deserialize_str(data))
    # print("Received request: %s" % message)

    output = message.upper()    # capitalize message

    #  Send reply back to client
    push_sock.send (pmt.serialize_str(pmt.to_pmt(output)))


```

### Python Program to Process Flowgraph Data
Here's the code to do GNU Radio --> Python over ZMQ PUB/SUB, which is by far the most useful case. Often you use GNU Radio for the signal processing but then at some point you want the resulting stream to go to a regular Python app. PUB/SUB is just so you can have multiple apps getting the stream. In the [ZMQ_PUB_Sink](https://wiki.gnuradio.org/index.php?title=ZMQ_PUB_Sink "ZMQ PUB Sink") you can switch `*` to `127.0.0.1`, but with `*`, any device on the LAN would be able to see it. It's essentially broadcasting to any interface, not just the loopback. 
An example flowgraph: 
[![](https://wiki.gnuradio.org/images/8/8c/ZMQ_data_PUB_fg.png)](https://wiki.gnuradio.org/index.php?title=File:ZMQ_data_PUB_fg.png)
* * *
and the Python code: 

```
#!/usr/bin/python3
# -*- coding: utf-8 -*-

# zmq_SUB_proc.py
# Author: Marc Lichtman

import zmq
import numpy as np
import time
import matplotlib.pyplot as plt

context = zmq.Context()
socket = context.socket(zmq.SUB)
socket.connect("tcp://127.0.0.1:55555") # connect, not bind, the PUB will bind, only 1 can bind
socket.setsockopt(zmq.SUBSCRIBE, b'') # subscribe to topic of all (needed or else it won't work)

while True:
    if socket.poll(10) != 0: # check if there is a message on the socket
        msg = socket.recv() # grab the message
        print(len(msg)) # size of msg
        data = np.frombuffer(msg, dtype=np.complex64, count=-1) # make sure to use correct data type (complex64 or float32); '-1' means read all data in the buffer
        print(data[0:10])
        # plt.plot(np.real(data))
        # plt.plot(np.imag(data))
        # plt.show()
    else:
        time.sleep(0.1) # wait 100ms and try again

```

Retrieved from "[https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks&oldid=12956](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks&oldid=12956)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Understanding+ZMQ+Blocks "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Understanding_ZMQ_Blocks&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks)
  * [View source](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Understanding_ZMQ_Blocks "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Understanding_ZMQ_Blocks "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks&oldid=12956 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Understanding_ZMQ_Blocks&action=info "More information about this page")


  * This page was last edited on 20 February 2023, at 23:42.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


