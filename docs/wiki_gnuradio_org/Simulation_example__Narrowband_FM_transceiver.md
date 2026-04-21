# Simulation example: Narrowband FM transceiver
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#searchInput)
This tutorial explains how a Narrowband FM (NBFM) signal can be generated and received. Rather than using any real hardware for transmission, the signal is sent via a socket from the transmit section to the receive section. The only actual hardware involved is the computer's microphone input and speaker output. In the case of a Raspberry Pi computer, which has no microphone input, three alternatives are presented. 
This tutorial can be performed with GNU Radio (GR) version 3.8 and later. It has been tested with GR version 3.8.2. The Graphical User Interface gnuradio-companion (GRC) is used to create a flowgraph for each section. 
## Contents
  * [1 Prerequisites](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#Prerequisites)
  * [2 NBFM receiver](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#NBFM_receiver)
    * [2.1 Flowgraph](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#Flowgraph)
    * [2.2 Block descriptions](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#Block_descriptions)
    * [2.3 Test receiver section](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#Test_receiver_section)
  * [3 NBFM transmitter](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#NBFM_transmitter)
    * [3.1 Flowgraph](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#Flowgraph_2)
    * [3.2 Block descriptions](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#Block_descriptions_2)
    * [3.3 Note for Raspberry Pi](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#Note_for_Raspberry_Pi)
  * [4 Testing](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#Testing)
    * [4.1 Terminal 2](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#Terminal_2)
    * [4.2 Terminal 1](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#Terminal_1)
  * [5 What do to next](https://wiki.gnuradio.org/index.php?title=Simulation_example%3A_Narrowband_FM_transceiver#What_do_to_next)


## Prerequisites
  * [**Intro to GR usage: GRC and flowgraphs**](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_GRC "Guided Tutorial GRC")
  * [**Understanding sample rate**](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Tutorial "Sample Rate Tutorial")
  * [**Where to get help**](https://wiki.gnuradio.org/index.php?title=FAQ#The_Community:_Where_you_get_help.2C_advice_and_code "FAQ")


## NBFM receiver
### Flowgraph
Using gnuradio-companion (GRC) and the following Block descriptions, build this flowgraph for the receiver section:  

[![](https://wiki.gnuradio.org/images/thumb/8/8f/NFM_rcv_fg.png/800px-NFM_rcv_fg.png)](https://wiki.gnuradio.org/index.php?title=File:NFM_rcv_fg.png)
The GR version 3.8 file can be found at [[1]](https://raw.githubusercontent.com/duggabe/gr-control/maint-3.8/Receivers/NFM_rcv.grc) Copy and paste it into a local file as `NFM_rcv.grc` 
### Block descriptions
  * Data is received from the transmitter via a [ZMQ_SUB_Source](https://wiki.gnuradio.org/index.php?title=ZMQ_SUB_Source "ZMQ SUB Source") at a sample rate of 576kHz. **NOTE:** Change the address of the [ZMQ_SUB_Source](https://wiki.gnuradio.org/index.php?title=ZMQ_SUB_Source "ZMQ SUB Source") to `tcp://127.0.0.1:49203` so it will connect to the transmitter.
  * It is filtered to a bandwidth of 6kHz and decimated (reduced) by a factor of 3 by the [FFT_Filter](https://wiki.gnuradio.org/index.php?title=FFT_Filter "FFT Filter"), giving an output sample rate of 192kHz.
  * A [Simple_Squelch](https://wiki.gnuradio.org/index.php?title=Simple_Squelch "Simple Squelch") mutes the audio when the input is less than the squelch level.
  * The [NBFM_Receive](https://wiki.gnuradio.org/index.php?title=NBFM_Receive "NBFM Receive") block demodulates the input and produces an output sample rate of 48kHz which matches the desired audio rate.
  * The [Multiply_Const](https://wiki.gnuradio.org/index.php?title=Multiply_Const "Multiply Const") block implements a Volume control.
  * The speaker output is defined by an [Audio_Sink](https://wiki.gnuradio.org/index.php?title=Audio_Sink "Audio Sink") block. 
    * Device name: for most speakers (or headphone jacks) built into the computer, the Device name can be left blank; for other cases, see [Audio_Sink#Device_Name](https://wiki.gnuradio.org/index.php?title=Audio_Sink#Device_Name "Audio Sink")
    * OK to Block: Yes


### Test receiver section
Without the transmitter, there is not much to test, but you can generate and run the flowgraph. After a few seconds, a GUI window will open showing Volume and Squelch controls as well as a waterfall spectrum display. Note that the waterfall will not be running since there is no input data. To terminate the process cleanly, click on the 'X' in the upper corner of the GUI rather than using Control-C. 
## NBFM transmitter
### Flowgraph
Using gnuradio-companion (GRC) and the following Block descriptions, build this flowgraph for the transmitter section:  

[![](https://wiki.gnuradio.org/images/thumb/a/a3/NFM_xmt_1_fg.png/800px-NFM_xmt_1_fg.png)](https://wiki.gnuradio.org/index.php?title=File:NFM_xmt_1_fg.png)
The GR version 3.8 file can be found at [[2]](https://raw.githubusercontent.com/duggabe/gr-control/maint-3.8/Transmitters/NFM_xmt.grc) Copy and paste it into a local file as `NFM_xmt.grc` 
### Block descriptions
  * The microphone input is defined by an [Audio_Source](https://wiki.gnuradio.org/index.php?title=Audio_Source "Audio Source") block. The parameters are: 
    * Sample rate: set to 48khz (use the pull-down)
    * Device name: for most microphone jacks built into the computer, the Device name can be left blank; for other cases, see [Audio_Source#Device_Name](https://wiki.gnuradio.org/index.php?title=Audio_Source#Device_Name "Audio Source")
    * OK to Block: No
  * The audio is filtered to a range of 300 to 5000 Hz by the [Band_Pass_Filter](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter "Band Pass Filter").
  * The [Multiply_Const](https://wiki.gnuradio.org/index.php?title=Multiply_Const "Multiply Const") block implements an Audio Gain control.
  * Most repeaters utilize a tone to trigger the transmitter. 
    * The PL (Private Line) tone can be selected by the [QT_GUI_Chooser](https://wiki.gnuradio.org/index.php?title=QT_GUI_Chooser "QT GUI Chooser"). Using a value of 0.0 turns off the PL.
    * The [Signal_Source](https://wiki.gnuradio.org/index.php?title=Signal_Source "Signal Source") generates the PL tone.
  * The audio signal plus PL tone is fed into a [NBFM_Transmit](https://wiki.gnuradio.org/index.php?title=NBFM_Transmit "NBFM Transmit") block. The output sample rate is 192kHz.
  * The [Low_Pass_Filter](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter "Low Pass Filter") limits the signal to 5kHz.
  * A [Repeat](https://wiki.gnuradio.org/index.php?title=Repeat "Repeat") block interpolates (multiplies) the sample rate by 3, giving an output rate of 576kHz.
  * The transmit signal is fed to a [ZMQ_PUB_Sink](https://wiki.gnuradio.org/index.php?title=ZMQ_PUB_Sink "ZMQ PUB Sink") with an address of `tcp://127.0.0.1:49203`, matching the port of the receiver.


### Note for Raspberry Pi
Since a Raspberry Pi has no audio input jack, there are three alternatives: 
  1. use a USB audio dongle and a microphone
  2. use a USB headset with microphone
  3. use a USB webcam with microphone


## Testing
When using GRC, doing a Generate and/or Run creates a Python file with the same name as the .grc file. You can execute the Python file without running GRC again. 
For testing this system we will use two processes, so we will need two terminal windows. 
#### Terminal 2
  * Open another terminal window.
  * change to whatever directory you used to generate the flowgraph for NFM_rcv
  * execute the following command:


```
   python3 -u NFM_rcv.py

```

  * After a few seconds, a GUI window will open showing Volume and Squelch controls as well as a waterfall spectrum display.


#### Terminal 1
  * going back to the GRC window, since you just finished building the `NFM_xmt.grc` flowgraph, you can just do a Run. After a few seconds, a GUI window will open with the Audio gain control and the GUI Frequency Sink.


Speaking into the microphone should show a change in the pattern on the QT GUI Time Sink. The level of modulation can be adjusted with the transmit gain control. You should hear your voice from the speakers. The speaker volume can be adjusted with the receive volume control. 
To terminate each of the processes cleanly, click on the 'X' in the upper corner of the GUI rather than using Control-C. 
## What do to next
The files used in this simulation are part of a package in <https://github.com/duggabe/gr-control> If you clone that repository, you will have a complete NBFM transceiver which can use either a B200mini or a Pluto SDR. It has been tested with GNU Radio version 3.10.10.0. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Simulation_example:_Narrowband_FM_transceiver&oldid=14264](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Narrowband_FM_transceiver&oldid=14264)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Simulation+example%3A+Narrowband+FM+transceiver "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Narrowband_FM_transceiver "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Simulation_example:_Narrowband_FM_transceiver&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Narrowband_FM_transceiver)
  * [View source](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Narrowband_FM_transceiver&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Narrowband_FM_transceiver&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Simulation_example:_Narrowband_FM_transceiver "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Simulation_example:_Narrowband_FM_transceiver "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Narrowband_FM_transceiver&oldid=14264 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Simulation_example:_Narrowband_FM_transceiver&action=info "More information about this page")


  * This page was last edited on 15 May 2024, at 21:30.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


