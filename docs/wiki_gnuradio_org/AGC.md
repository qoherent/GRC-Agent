# AGC
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=AGC#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=AGC#searchInput)
High performance Automatic Gain Control. Power is approximated by absolute value. 
Here is a diagram showing how it works: 
[![](https://wiki.gnuradio.org/images/2/2f/AGC_loop.jpg)](https://wiki.gnuradio.org/index.php?title=File:AGC_loop.jpg)
## Parameters
(_R_): [_Run-time adjustable_](https://wiki.gnuradio.org/index.php/GNURadioCompanion#Variable_Controls) 

Rate (_R_)
    The update rate of the loop. 

Reference (_R_)
    Reference value to adjust signal power to. 

Gain (_R_)
    Initial gain value. 

Max gain (_R_)
    Maximum gain value (0 for unlimited)
## Example Flowgraph
This flowgraph shows an Automatic Gain Control block in an AM receiver. 
[![](https://wiki.gnuradio.org/images/thumb/c/c8/FunCube_AM.png/800px-FunCube_AM.png)](https://wiki.gnuradio.org/index.php?title=File:FunCube_AM.png)
## Source Files 

C++ files
    [Complex input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/lib/agc_cc_impl.cc)     [Float input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/lib/agc_ff_impl.cc)     [Algorithms implementation](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/include/gnuradio/analog/agc.h) 

Header files
    [Complex input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/lib/agc_cc_impl.h)     [Float input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/lib/agc_ff_impl.h) 

Public header files
    [Complex input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/include/gnuradio/analog/agc_cc.h)     [Float input](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/include/gnuradio/analog/agc_ff.h) 

Block definition
    [Yaml](https://github.com/gnuradio/gnuradio/blob/master/gr-analog/grc/analog_agc_xx.block.yml)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=AGC&oldid=9233](https://wiki.gnuradio.org/index.php?title=AGC&oldid=9233)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=AGC "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=AGC "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:AGC&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=AGC)
  * [View source](https://wiki.gnuradio.org/index.php?title=AGC&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=AGC&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/AGC "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/AGC "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=AGC&oldid=9233 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=AGC&action=info "More information about this page")


  * This page was last edited on 4 December 2021, at 16:14.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


