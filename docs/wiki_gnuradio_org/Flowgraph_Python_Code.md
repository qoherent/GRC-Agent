# Flowgraph Python Code
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Flowgraph_Python_Code#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Flowgraph_Python_Code#searchInput)
## Dial Tone Flowgraph
The following example flowgraph implements a dial-tone: 
![tutorial_three_1.png](https://raw.githubusercontent.com/gnuradio/gr-tutorial/master/examples/tutorial3/images/tutorial_three_1.png)
When we click the **Generate** button from within GRC, the terminal tells us that it produced a "tutorial_three_1.py" file, so let's open it to examine the code. 

```
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: tutorial_three_1
# GNU Radio version: 3.8.0.0

from distutils.version import StrictVersion

if __name__ == '__main__':
    import ctypes
    import sys
    if sys.platform.startswith('linux'):
        try:
            x11 = ctypes.cdll.LoadLibrary('libX11.so')
            x11.XInitThreads()
        except:
            print("Warning: failed to XInitThreads()")

from gnuradio import analog
from gnuradio import audio
from gnuradio import gr
from gnuradio.filter import firdes
import sys
import signal
from PyQt5 import Qt
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
from gnuradio import qtgui

class tutorial_three_1(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "tutorial_three_1")
        Qt.QWidget.__init__(self)
        self.setWindowTitle("tutorial_three_1")
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

        self.settings = Qt.QSettings("GNU Radio", "tutorial_three_1")

        try:
            if StrictVersion(Qt.qVersion()) < StrictVersion("5.0.0"):
                self.restoreGeometry(self.settings.value("geometry").toByteArray())
            else:
                self.restoreGeometry(self.settings.value("geometry"))
        except:
            pass

        ##################################################
        # Variables
        ##################################################
        self.samp_rate = samp_rate = 32000

        ##################################################
        # Blocks
        ##################################################
        self.audio_sink_0 = audio.sink(samp_rate, '', True)
        self.analog_sig_source_x_1 = analog.sig_source_f(samp_rate, analog.GR_COS_WAVE, 350, 0.1, 0, 0)
        self.analog_sig_source_x_0 = analog.sig_source_f(samp_rate, analog.GR_COS_WAVE, 440, 0.1, 0, 0)



        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_sig_source_x_0, 0), (self.audio_sink_0, 0))
        self.connect((self.analog_sig_source_x_1, 0), (self.audio_sink_0, 1))

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "tutorial_three_1")
        self.settings.setValue("geometry", self.saveGeometry())
        event.accept()

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.analog_sig_source_x_0.set_sampling_freq(self.samp_rate)
        self.analog_sig_source_x_1.set_sampling_freq(self.samp_rate)



def main(top_block_cls=tutorial_three_1, options=None):

    if StrictVersion("4.5.0") <= StrictVersion(Qt.qVersion()) < StrictVersion("5.0.0"):
        style = gr.prefs().get_string('qtgui', 'style', 'raster')
        Qt.QApplication.setGraphicsSystem(style)
    qapp = Qt.QApplication(sys.argv)

    tb = top_block_cls()
    tb.start()
    tb.show()

    def sig_handler(sig=None, frame=None):
        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    def quitting():
        tb.stop()
        tb.wait()
    qapp.aboutToQuit.connect(quitting)
    qapp.exec_()


if __name__ == '__main__':
    main()

```

Once GRC has created a Python file, the user is free to modify it in any desired manner, such as changing parameters, sample rate, and even connections among the blocks. 
To execute this file from a terminal, enter:  
`python3 tutorial_three_1.py`
**Warning:** After the Python file has been modified, running GRC again with that flowgraph will wipe out your changes!
## Dial Tone Python Code Dissected
Let's examine pertinent lines of the code: 

```
#!/usr/bin/env python3

```

This tells the shell to use the Python3 interpreter to run this file. 

```
from gnuradio import analog
from gnuradio import audio
from gnuradio import gr

```

These tell Python what modules to include. We must always have **gr** to run GNU Radio applications. The audio sink is included in the audio module and the signal source is included in the analog module. 

```
class tutorial_three_1(gr.top_block, Qt.QWidget):

```

Define a class called "tutorial_three_1" which is derived from another class, **gr.top_block**. This class is basically a container for the flow graph. By deriving from gr.top_block, we get all the hooks and functions we need to add blocks and interconnect them. 

```
def __init__(self):

```

Only one member function is defined for this class: the function "_init_()", which is the constructor of this class. 

```
gr.top_block.__init__(self, "tutorial_three_1")

```

The parent constructor is called. 

```
self.samp_rate = samp_rate = 32000

```

Variable declaration for sample rate. 

```
self.connect((self.analog_sig_source_x_0, 0), (self.audio_sink_0, 0))
self.connect((self.analog_sig_source_x_1, 0), (self.audio_sink_0, 1))

```

There are 2 inputs to the **Audio Sink** block. The first line connects the only output of analog_sig_source_x_0 (440 Hz waveform) to the first input of audio_sink_0. The second line connects the only output of analog_sig_source_x_1 (350 Hz waveform) to the second input of audio_sink_0. 
## GNU Radio Flowgraph in Lisp
Can this be done in Lisp? 
Yes, if the Lisp is [Hy](http://hylang.org/) - which has very tight coupling to Python. 

```
(import [gnuradio [gr]]
	[gnuradio [audio]]
	[gnuradio.eng_arg [eng_float]]
	[gnuradio [analog]])

(defclass my_top_block [gr.top_block]
 "Play a dialtone through the speakers"
    (defn __init__[self]
        (.__init__ gr.top_block self)
	(setv args
	      (parse-args [["-O" "--audio-output" :default ""
	        	      :help "pcm output device name.  E.g., hw:0,0 or /dev/dsp"]
		           ["-r" "--sample-rate" :type eng_float :default 48000
		              :help "set sample rate, default=%(default)s"]]
		          :description "Set sound card and sample rate"))
        (setv sample_rate args.sample_rate)
        (setv ampl 0.1)
        (setv src0 (analog.sig_source_f sample_rate analog.GR_SIN_WAVE 350 ampl)
              src1 (analog.sig_source_f sample_rate analog.GR_SIN_WAVE 440 ampl))
        (setv dst (audio.sink sample_rate args.audio_output))
        (.connect self src0 [dst 0])
        (.connect self src1 [dst 1])))

(defmain [&rest args]
 (try 
  (.run (my_top_block))
  (except [KeyboardInterrupt])))

```

Retrieved from "[https://wiki.gnuradio.org/index.php?title=Flowgraph_Python_Code&oldid=8791](https://wiki.gnuradio.org/index.php?title=Flowgraph_Python_Code&oldid=8791)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Flowgraph+Python+Code "You are encouraged to log in; however, it is not mandatory \[o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Flowgraph_Python_Code "View the content page \[c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Flowgraph_Python_Code&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Flowgraph_Python_Code)
  * [View source](https://wiki.gnuradio.org/index.php?title=Flowgraph_Python_Code&action=edit "This page is protected.
You can view its source \[e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Flowgraph_Python_Code&action=history "Past revisions of this page \[h\]")


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
  * [Recent changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChanges "A list of recent changes in the wiki \[r\]")
  * [Random page](https://wiki.gnuradio.org/index.php?title=Special:Random "Load a random page \[x\]")
  * [Help](https://www.mediawiki.org/wiki/Special:MyLanguage/Help:Contents "The place to find out")


###  Tools
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Flowgraph_Python_Code "A list of all wiki pages that link here \[j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Flowgraph_Python_Code "Recent changes in pages linked from this page \[k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Flowgraph_Python_Code&oldid=8791 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Flowgraph_Python_Code&action=info "More information about this page")


  * This page was last edited on 27 September 2021, at 03:53.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


