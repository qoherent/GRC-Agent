# E310 FM Receiver
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#searchInput)  
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
  5. [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files")

SDR Hardware 
  1. [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver")
  2. [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver")
  3. E310 FM Receiver

 |  
| --- |  
This tutorial describes how to receive broadcast commercial radio stations transmitting Frequency Modulated (FM) signals using the Ettus Research E310. 
## Contents
  * [1 USRP (E310) Setup Guide](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#USRP_\(E310\)_Setup_Guide)
    * [1.1 Prerequisites](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Prerequisites)
      * [1.1.1 Linux versions](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Linux_versions)
      * [1.1.2 GNU RADIO versions](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#GNU_RADIO_versions)
    * [1.2 Hardware Connection](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Hardware_Connection)
    * [1.3 Initialization of the USRP](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Initialization_of_the_USRP)
    * [1.4 Determine the USRP’s IP Address](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Determine_the_USRP%E2%80%99s_IP_Address)
    * [1.5 Assign a Static IP Address](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Assign_a_Static_IP_Address)
      * [1.5.1 Create the **static_ip.sh** Script](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Create_the_static_ip.sh_Script)
      * [1.5.2 Run the Script](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Run_the_Script)
    * [1.6 SSH Connection](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#SSH_Connection)
    * [1.7 Mount USRP Filesystem via SSHFS](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Mount_USRP_Filesystem_via_SSHFS)
    * [1.8 Configure Geany IDE](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Configure_Geany_IDE)
      * [1.8.1 Open Build Commands](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Open_Build_Commands)
      * [1.8.2 Create the **start_usrp_script.sh** Script](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Create_the_start_usrp_script.sh_Script)
      * [1.8.3 Update the Build Commands](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Update_the_Build_Commands)
    * [1.9 USRP/Host Codes and flowgraphs](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#USRP/Host_Codes_and_flowgraphs)
      * [1.9.1 USRP Code](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#USRP_Code)
      * [1.9.2 Host Flowgraph](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Host_Flowgraph)
    * [1.10 Running the Flow](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Running_the_Flow)
    * [1.11 Additional Notes](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver#Additional_Notes)


# USRP (E310) Setup Guide
This page provides step-by-step instructions to configure a USRP E310 for: 
  * Receiving signals with a Python script
  * Remote control via SSH
  * Using GNU Radio on a host machine


Both the USRP (IP: 10.67.44.142) and the host (IP: 10.67.44.132) run Linux operating systems. 
## Prerequisites
### Linux versions
Before starting, ensure you know the Linux versions running on both the host and the USRP, as this helps verify compatibility and troubleshoot potential issues. 

```
(base) host@host:~$ lsb_release -a
No LSB modules are available.
Distributor ID:	Ubuntu
Description:	Ubuntu 18.04.6 LTS
Release:	18.04
Codename:	bionic

```


```
root@USRP:~# lsb_release -a    
LSB Version:	n/a
Distributor ID:	Alchemy
Description:	Alchemy 2021.04
Release:	2021.04
Codename:	Alchemy-Zeus

```

### GNU RADIO versions

```
(base) host@host:~$ gnuradio-companion --version
Gtk-Message: 15:17:09.634: Failed to load module "canberra-gtk-module"
GNU Radio Companion 3.7.11

This program is part of GNU Radio
GRC comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it.

```

## Hardware Connection
  * Plug the antenna into the RX2-A port on the USRP. The RX2-A port is dedicated to receive (RX) operations.


  * Turn on the USRP and ensure it has a stable power supply.


## Initialization of the USRP
see dedicated page 
  

## Determine the USRP’s IP Address
On your host machine (connected to the same network): 

```
sudo snap install nmap    # Install nmap if not already present
nmap -sn 10.0.0.0/24      # Scan the local subnet

```
    This finds the DHCP‑assigned IP of the USRP (e.g., 10.67.44.2).
## Assign a Static IP Address
To simplify SSH access, give the USRP a fixed IP. 
### Create the **static_ip.sh** Script
SSH into the USRP (using the DHCP IP) and run: 

```
nano static_ip.sh

```

Paste the following: 

```
#!/bin/sh

# 1. Flush existing IP addresses
ip addr flush dev eth0

# 2. Assign static IP and netmask
ip addr add 10.67.44.142/24 dev eth0

# 3. Bring the interface up
ip link set eth0 up

# 4. Add default gateway
ip route add default via 10.0.0.1

```
    Each step configures the Ethernet interface (eth0) for static addressing.
### Run the Script

```
bash static_ip.sh

```
    The IP remains active until the next reboot.
## SSH Connection
On the host: 

```
ssh root@10.67.44.142

```
    You now have remote shell access to the USRP.
## Mount USRP Filesystem via SSHFS
To edit USRP files locally: 

```
sshfs root@10.67.44.142:/ ~/remote_usrp

```
    Mounts the USRP’s root directory at `~/remote_usrp` on the host.
## Configure Geany IDE
To simplify the workflow, we will use Geany, a lightweight IDE, on the host machine to: 
  * Edit files located on the USRP (via SSHFS)
  * Launch scripts remotely on the USRP (via SSH)
  * Centralize all development and execution within one interface

    This allows you to work entirely from the host, avoiding the need to manually SSH into the USRP or use a separate editor.
  

### Open Build Commands
In Geany, go to Build → Set Build Commands. 
  

### Create the **start_usrp_script.sh** Script
Before applying the static IP, prepare the Geany startup helper on the USRP: 

```
nano /home/root/start_script_geany.sh

```

Paste the following: 

```
#!/bin/sh

cleanup() {
    echo '[INFO] Stop requested'
    pkill -f RX_FM_USRP_UDP.py
    exit 0
}

trap cleanup INT TERM

python3 /home/root/RX_FM_USRP_UDP.py

```
    This script installs a cleanup handler that intercepts interrupt or termination signals, cleanly kills the FM‑UDP Python process, and exits. When Geany’s “Execute” command runs this script remotely, RX_FM_USRP_UDP.py is launched and properly managed.
### Update the Build Commands
In "Independent Commands", to the right of "Run Remote", add: 

```
scp "%f" root@10.67.44.142:/tmp/ && ssh root@10.67.44.142 'python3 /tmp/"%f"'

```
    This single command performs two actions back-to-back: first, it securely transfers the file you’re editing to the USRP’s temporary directory over SSH; then, once that transfer completes successfully, it opens an SSH session on the USRP and immediately invokes Python 3 to run the uploaded file from its temporary location. In other words, it bundles “copy the script over” and “execute it remotely” into one seamless operation.
In "Execute commands", to the right of "Execute", add: 

```
ssh -t root@10.67.44.142 "bash -i -c '/home/root/start_script_geany.sh'"

```

## USRP/Host Codes and flowgraphs
All operations are performed in a networked setup where a host PC (IP address 10.67.44.132) handles control messaging and UDP reception, and the USRP E310 (IP address 10.67.44.142) runs the GNURadio flowgraph and streams data. 
### USRP Code
This script is written in Python to be more easily edited and executed remotely via Geany on the host machine, providing flexibility and rapid iteration. 

```
#!/usr/bin/env python3  # Corrected for execution on host
# -*- coding: utf-8 -*-

import time
import signal
import sys
from gnuradio import gr, blocks, analog, filter, uhd, zeromq
from gnuradio.filter import firdes

from message_to_freq import message_to_freq  # Custom block: maps incoming ZMQ messages to frequency updates
from message_to_gain import message_to_gain  # Custom block: maps incoming ZMQ messages to gain updates

class RX_FM_USRP_UDP(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "Rx FM USRP UDP Headless")

        ##################################################
        # Variables
        ##################################################
        self.samp_rate = 2e6           # Sample rate for USRP
        self.gain = 15                 # Initial gain (dB)
        self.freq = 102.5e6            # Center frequency (Hz)
        self.freq_cos = 300e3          # Offset for cosine mixing (Hz)
        self.bw = 200e3                # RF bandwidth (Hz)

        ##################################################
        # USRP Source
        ##################################################
        self.uhd_source = uhd.usrp_source(
            ",".join(('', '')),      # Empty args: will use default device
            uhd.stream_args(cpu_format="fc32", channels=[0]),
        )
        self.uhd_source.set_samp_rate(self.samp_rate)
        self.uhd_source.set_center_freq(self.freq, 0)
        self.uhd_source.set_gain(self.gain, 0)
        self.uhd_source.set_antenna("RX2", 0)
        self.uhd_source.set_bandwidth(self.bw, 0)

        ##################################################
        # Signal Processing Chain
        ##################################################
        # Generate a cosine wave for mixing
        self.sig_source = analog.sig_source_c(
            self.samp_rate, analog.GR_COS_WAVE, self.freq_cos, 1, 0
        )
        # Multiply RF signal with cosine to shift frequency
        self.mult = blocks.multiply_vcc(1)

        # Low-pass filter to isolate FM bandwidth
        self.lowpass = filter.fir_filter_ccf(
            decimation=10,
            taps=firdes.low_pass(1, self.samp_rate, 90e3, 5e3, firdes.WIN_HAMMING)
        )

        # Rational resampler to adjust sample rate for UDP sink
        self.resampler = filter.rational_resampler_ccc(
            interpolation=12, decimation=15
        )

        # Send complex baseband samples over UDP to host
        self.udp_sink = blocks.udp_sink(
            gr.sizeof_gr_complex, '10.67.44.132', 9997, 1472, True
        )

        ##################################################
        # ZMQ Control: Frequency Updates
        ##################################################
        self.zmq_pull_freq = zeromq.pull_msg_source(
            'tcp://10.67.44.132:9996', 100
        )
        self.msg_freq_handler = message_to_freq(self.set_freq)
        self.msg_connect(
            (self.zmq_pull_freq, 'out'),
            (self.msg_freq_handler, 'in')
        )

        ##################################################
        # ZMQ Control: Gain Updates
        ##################################################
        self.zmq_pull_gain = zeromq.pull_msg_source(
            'tcp://10.67.44.132:9995', 100
        )
        self.msg_gain_handler = message_to_gain(self.set_gain)
        self.msg_connect(
            (self.zmq_pull_gain, 'out'),
            (self.msg_gain_handler, 'in')
        )

        ##################################################
        # Block Connections
        ##################################################
        self.connect((self.uhd_source, 0), (self.mult, 0))
        self.connect((self.sig_source, 0), (self.mult, 1))
        self.connect((self.mult, 0), (self.lowpass, 0))
        self.connect((self.lowpass, 0), (self.resampler, 0))
        self.connect((self.resampler, 0), (self.udp_sink, 0))

    def set_freq(self, freq):
        """Update center frequency on the fly."""
        # print(f"[INFO] Updating frequency to {freq/1e6:.2f} MHz")
        self.freq = freq
        self.uhd_source.set_center_freq(freq, 0)

    def set_gain(self, gain):
        """Update gain on the fly."""
        # print(f"[INFO] Updating gain to {gain:.1f} dB")
        self.gain = gain
        self.uhd_source.set_gain(gain, 0)

def main():
    tb = RX_FM_USRP_UDP()

    def cleanup(signum=None, frame=None):
        """Handle termination signals to stop flowgraph cleanly."""
        print("[INFO] Stop requested via signal.")
        tb.stop()
        tb.wait()
        sys.exit(0)

    # Catch Ctrl+C and termination signals
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    tb.start()
    print("[INFO] Flowgraph started. Waiting for ZMQ commands (freq/gain)...")

    try:
        while True:
            time.sleep(1)
    except Exception as e:
        print(f"[ERROR] Unexpected exception: {e}")
        cleanup()

if __name__ == '__main__':
    main()

```

This script defines and runs a headless GNU Radio flowgraph on the USRP E310 that continuously receives FM broadcasts, applies signal processing, and streams the resulting complex baseband samples over UDP to a host computer. It begins by initializing key parameters—sample rate (2 MHz), RF center frequency (e.g. 102.5 MHz), gain, mixing offset, and bandwidth—all of which can be easily adjusted in code. The script then configures the USRP source block to use these parameters (including selecting the RX2 antenna port), and constructs a processing chain that mixes the incoming RF signal down with a cosine wave, filters it with a low‑pass FIR filter to isolate the FM spectrum, and resamples it to match the network transport rate before sending it out via a UDP sink. Meanwhile, two ZeroMQ pull sockets listen for real‑time control messages from the host—one for frequency updates and one for gain adjustments. When a message arrives, custom handler blocks invoke set_freq() or set_gain() to retune the USRP or change its gain without restarting the flowgraph. The script also installs a cleanup function that traps interruption or termination signals, allowing a graceful shutdown that stops streaming and releases hardware resources. Finally, after starting the flowgraph, it enters an infinite sleep loop to keep the process alive and responsive until the user requests it to stop. 
### Host Flowgraph
On the host PC, it’s simpler to use a GNU Radio Companion flowgraph so you can take full advantage of its graphical interface, preserving the intuitive and easy-to-use workflow. [![](https://wiki.gnuradio.org/images/1/13/---home-student-Pictures-TX_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:---home-student-Pictures-TX_flowgraph.png)
The following code corresponds to the flowgraph above. 

```
#!/usr/bin/env python2
# -*- coding: utf-8 -*-
##################################################
# GNU Radio Python Flow Graph
# Title: Rx Fm Host Udp
# Generated: Thu Jun 26 14:44:45 2025
##################################################

from distutils.version import StrictVersion

if __name__ == '__main__':
    import ctypes
    import sys
    if sys.platform.startswith('linux'):
        try:
            x11 = ctypes.cdll.LoadLibrary('libX11.so')
            x11.XInitThreads()
        except:
            print "Warning: failed to XInitThreads()"

from PyQt5 import Qt
from PyQt5 import Qt, QtCore
from gnuradio import analog
from gnuradio import audio
from gnuradio import blocks
from gnuradio import eng_notation
from gnuradio import gr
from gnuradio import qtgui
from gnuradio import zeromq
from gnuradio.eng_option import eng_option
from gnuradio.filter import firdes
from gnuradio.qtgui import Range, RangeWidget
from optparse import OptionParser
import pmt
import sip
import sys
from gnuradio import qtgui


class RX_FM_Host_UDP(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "Rx Fm Host Udp")
        Qt.QWidget.__init__(self)
        self.setWindowTitle("Rx Fm Host Udp")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except:
            pass
        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("GNU Radio", "RX_FM_Host_UDP")

        if StrictVersion(Qt.qVersion()) < StrictVersion("5.0.0"):
            self.restoreGeometry(self.settings.value("geometry").toByteArray())
        else:
            self.restoreGeometry(self.settings.value("geometry", type=QtCore.QByteArray))

        ##################################################
        # Variables
        ##################################################
        self.var_freq = var_freq = 102.2
        self.var_freq_MHz = var_freq_MHz = var_freq*1e6
        self.var_amp = var_amp = 0.5
        self.samp_rate = samp_rate = 2e6
        self.bw = bw = 300e3

        ##################################################
        # Blocks
        ##################################################
        self._var_amp_range = Range(0, 1, 0.01, 0.5, 200)
        self._var_amp_win = RangeWidget(self._var_amp_range, self.set_var_amp, "var_amp", "counter_slider", float)
        self.top_layout.addWidget(self._var_amp_win)
        self.zeromq_push_msg_sink_0 = zeromq.push_msg_sink('tcp://10.67.44.132:9996', 100)
        self._var_freq_range = Range(88.4, 107.6, 0.01, 102.2, 200)
        self._var_freq_win = RangeWidget(self._var_freq_range, self.set_var_freq, "var_freq", "counter_slider", float)
        self.top_layout.addWidget(self._var_freq_win)
        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_f(
        	1024, #size
        	firdes.WIN_BLACKMAN_hARRIS, #wintype
        	var_freq_MHz, #fc
        	bw, #bw
        	"", #name
        	1 #number of inputs
        )
        self.qtgui_freq_sink_x_0.set_update_time(0.10)
        self.qtgui_freq_sink_x_0.set_y_axis(-140, 10)
        self.qtgui_freq_sink_x_0.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.qtgui_freq_sink_x_0.enable_autoscale(True)
        self.qtgui_freq_sink_x_0.enable_grid(True)
        self.qtgui_freq_sink_x_0.set_fft_average(1.0)
        self.qtgui_freq_sink_x_0.enable_axis_labels(True)
        self.qtgui_freq_sink_x_0.enable_control_panel(False)

        if not True:
          self.qtgui_freq_sink_x_0.disable_legend()

        if "float" == "float" or "float" == "msg_float":
          self.qtgui_freq_sink_x_0.set_plot_pos_half(not True)

        labels = ['', '', '', '', '',
                  '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
                  1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
                  "magenta", "yellow", "dark red", "dark green", "dark blue"]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
                  1.0, 1.0, 1.0, 1.0, 1.0]
        for i in xrange(1):
            if len(labels[i]) == 0:
                self.qtgui_freq_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_freq_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_freq_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_freq_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_freq_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_freq_sink_x_0_win = sip.wrapinstance(self.qtgui_freq_sink_x_0.pyqwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._qtgui_freq_sink_x_0_win)
        self.blocks_udp_source_0 = blocks.udp_source(gr.sizeof_gr_complex*1, '10.67.44.132', 9997, 1472, True)
        self.blocks_multiply_const_vxx_0_0 = blocks.multiply_const_vff((var_amp, ))
        self.blocks_message_strobe_0 = blocks.message_strobe(pmt.from_double(((var_freq_MHz)+300e3)), 1000)
        self.blocks_complex_to_mag_0 = blocks.complex_to_mag(1)
        self.audio_sink_0_0 = audio.sink(16000, '', True)
        self.analog_wfm_rcv_0 = analog.wfm_rcv(
        	quad_rate=int(samp_rate/150*12),
        	audio_decimation=10,
        )

        ##################################################
        # Connections
        ##################################################
        self.msg_connect((self.blocks_message_strobe_0, 'strobe'), (self.zeromq_push_msg_sink_0, 'in'))
        self.connect((self.analog_wfm_rcv_0, 0), (self.blocks_multiply_const_vxx_0_0, 0))
        self.connect((self.blocks_complex_to_mag_0, 0), (self.qtgui_freq_sink_x_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0_0, 0), (self.audio_sink_0_0, 0))
        self.connect((self.blocks_udp_source_0, 0), (self.analog_wfm_rcv_0, 0))
        self.connect((self.blocks_udp_source_0, 0), (self.blocks_complex_to_mag_0, 0))

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "RX_FM_Host_UDP")
        self.settings.setValue("geometry", self.saveGeometry())
        event.accept()

    def get_var_freq(self):
        return self.var_freq

    def set_var_freq(self, var_freq):
        self.var_freq = var_freq
        self.set_var_freq_MHz(self.var_freq*1e6)

    def get_var_freq_MHz(self):
        return self.var_freq_MHz

    def set_var_freq_MHz(self, var_freq_MHz):
        self.var_freq_MHz = var_freq_MHz
        self.qtgui_freq_sink_x_0.set_frequency_range(self.var_freq_MHz, self.bw)
        self.blocks_message_strobe_0.set_msg(pmt.from_double(((self.var_freq_MHz)+300e3)))

    def get_var_amp(self):
        return self.var_amp

    def set_var_amp(self, var_amp):
        self.var_amp = var_amp
        self.blocks_multiply_const_vxx_0_0.set_k((self.var_amp, ))

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate

    def get_bw(self):
        return self.bw

    def set_bw(self, bw):
        self.bw = bw
        self.qtgui_freq_sink_x_0.set_frequency_range(self.var_freq_MHz, self.bw)


def main(top_block_cls=RX_FM_Host_UDP, options=None):

    if StrictVersion("4.5.0") <= StrictVersion(Qt.qVersion()) < StrictVersion("5.0.0"):
        style = gr.prefs().get_string('qtgui', 'style', 'raster')
        Qt.QApplication.setGraphicsSystem(style)
    qapp = Qt.QApplication(sys.argv)

    tb = top_block_cls()
    tb.start()
    tb.show()

    def quitting():
        tb.stop()
        tb.wait()
    qapp.aboutToQuit.connect(quitting)
    qapp.exec_()


if __name__ == '__main__':
    main()

```

## Running the Flow
  1. In Geany, open `~/remote_usrp/home/root/usrp_fm_receiver.py`.
  2. Press “Execute” to start the script on the USRP.
  3. On your host, launch GNU Radio Companion and run `fm_receiver_host_udp.py` to receive the UDP stream.

    The USRP script streams FM over UDP; GNU Radio handles it on the host side.
[![](https://wiki.gnuradio.org/images/b/b5/-home-student-Pictures-View_TX_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:-home-student-Pictures-View_TX_flowgraph.png)
  
This is the console output you see when you execute the GNU Radio flowgraph on the USRP. 

```
root@10.67.44.142's password:
[INFO] [UHD] linux; GNU C++ version 9.2.0; Boost_107100; UHD_4.1.0.5-0-g6bd0be9c
[INFO] [MPMD] Initializing 1 device(s) in parallel with args: mgmt_addr=127.0.0.1,type=e3xx,product=e310_sg3,serial=3465D7F,fpga=n/a,claimed=False
[INFO] [MPM.PeriphManager] Found 1 daughterboard(s).
[INFO] [MPM.PeriphManager] init() called with device args `fpga=n/a,mgmt_addr=127.0.0.1,product=e310_sg3`.
[INFO] [0/Radio#0] Performing CODEC loopback test on channel 0 ...
[INFO] [0/Radio#0] CODEC loopback test passed
[INFO] [0/Radio#0] Performing CODEC loopback test on channel 1 ...
[INFO] [0/Radio#0] CODEC loopback test passed
[INFO] Flow started. Waiting for ZMQ commands (frequency and gain)...
DDODDDDOODDODOODDOODDDDODDDDDDODDDDDODDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD^C
[INFO] Stop of the flow requested (signal).

```

  

## Additional Notes
  * To make the IP configuration persistent, consider adding `static_ip.sh` to `/etc/rc.local` or creating a `systemd` service.
  * Verify compatibility of Python and GNU Radio versions between the USRP and host.
  * IP addresses have been anonymized, but this obviously needs to be adapted to the use case.


Last updated: 30 June 2025
Retrieved from "[https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver&oldid=16024](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver&oldid=16024)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=E310+FM+Receiver "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:E310_FM_Receiver&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver)
  * [View source](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/E310_FM_Receiver "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/E310_FM_Receiver "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver&oldid=16024 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver&action=info "More information about this page")


  * This page was last edited on 6 March 2026, at 04:03.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


