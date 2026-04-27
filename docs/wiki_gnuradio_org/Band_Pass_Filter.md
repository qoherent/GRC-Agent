# Band Pass Filter
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter#searchInput)
This filter is a convenience wrapper for an [Decimating FIR Filter](https://wiki.gnuradio.org/index.php?title=Decimating_FIR_Filter "Decimating FIR Filter") (or the interpolating FIR filter) and a firdes taps generating function of band-pass type, i.e. calling firdes.band_pass() or firdes.complex_band_pass(). 
## Parameters
(_R_): [_Run-time adjustable_](https://wiki.gnuradio.org/index.php/GNURadioCompanion#Variable_Controls) 

FIR Type (_R_)
    Specify whether input/output is real or complex, and if the taps are real or complex. 

Decimation
    Decimation rate of filter, must be an integer, and cannot change in realtime. 

Gain (_R_)
    Scaling factor applied to output. 

Sample Rate (_R_)
    Input sample rate. 

Low Cutoff Freq (_R_)
    Lower cutoff frequency in Hz 

High Cutoff Freq (_R_)
    Upper cutoff frequency in Hz 

Transition Width (_R_)
    Transition width between stop-band and pass-band in Hz 

Window (_R_)
    Type of window to use 

Beta (_R_)
    The beta parameter only applies to the Kaiser window.
## Example Flowgraph
This flowgraph shows the use of a Band Pass Filter block. This is a working AM broadcast band receiver. 
[![](https://wiki.gnuradio.org/images/thumb/0/01/Complex_to_Mag.png/800px-Complex_to_Mag.png)](https://wiki.gnuradio.org/index.php?title=File:Complex_to_Mag.png)
## Source Files
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter&oldid=15042](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter&oldid=15042)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Band+Pass+Filter "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Band_Pass_Filter&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter)
  * [View source](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Band_Pass_Filter "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Band_Pass_Filter "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter&oldid=15042 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Band_Pass_Filter&action=info "More information about this page")


  * This page was last edited on 6 May 2025, at 09:51.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


