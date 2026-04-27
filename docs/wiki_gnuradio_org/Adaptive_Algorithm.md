# Adaptive Algorithm
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Adaptive_Algorithm#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Adaptive_Algorithm#searchInput)
Creates an Adaptive Algorithm object to be used in [Linear_Equalizer](https://wiki.gnuradio.org/index.php?title=Linear_Equalizer "Linear Equalizer") or [Decision_Feedback_Equalizer](https://wiki.gnuradio.org/index.php?title=Decision_Feedback_Equalizer "Decision Feedback Equalizer") to define how the error signal is calculated and how taps are updated, depending on the specified algorithm 
## Parameters
(_R_): [_Run-time adjustable_](https://wiki.gnuradio.org/index.php/GNURadioCompanion#Variable_Controls) 

Algorithm Type
    enum to specify which adaptive algorithm will be used; LMS, NLMS, CMA are the valid choices 

Digital Constellation Object
    A [Constellation_Object](https://wiki.gnuradio.org/index.php?title=Constellation_Object "Constellation Object") which specifies the modulation used to adapt using decision directed mode of the equalizer 

Step Size
    Specifies how quickly the adaptive algorithm will converge. Too high and the equalizer becomes unstable. The optimal value is dependent on the statistical properties of the input signal 

Modulus
    (CMA only) Specifies the number of constellation points, e.g. for QPSK modulus = 4
## Example Flowgraph
See [Linear_Equalizer](https://wiki.gnuradio.org/index.php?title=Linear_Equalizer "Linear Equalizer") for a flowgraph utilizing the Adaptive Algorithm Object 
[![](https://wiki.gnuradio.org/images/7/7c/Adaptive_algorithm.png)](https://wiki.gnuradio.org/index.php?title=File:Adaptive_algorithm.png)
[![](https://wiki.gnuradio.org/images/8/83/Adaptive_algorithm_LMS.png)](https://wiki.gnuradio.org/index.php?title=File:Adaptive_algorithm_LMS.png)
[![](https://wiki.gnuradio.org/images/8/8f/Adaptive_algorithm_CMA.png)](https://wiki.gnuradio.org/index.php?title=File:Adaptive_algorithm_CMA.png)
## Source Files 

C++ files
    [TODO](https://github.com/gnuradio/gnuradio) 

Header files
    [TODO](https://github.com/gnuradio/gnuradio) 

Public header files
    [TODO](https://github.com/gnuradio/gnuradio) 

Block definition
    [[1]](https://raw.githubusercontent.com/gnuradio/gnuradio/master/gr-digital/grc/digital_adaptive_algorithm.block.yml)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Adaptive_Algorithm&oldid=9011](https://wiki.gnuradio.org/index.php?title=Adaptive_Algorithm&oldid=9011)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Adaptive+Algorithm "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Adaptive_Algorithm "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Adaptive_Algorithm&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Adaptive_Algorithm)
  * [View source](https://wiki.gnuradio.org/index.php?title=Adaptive_Algorithm&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Adaptive_Algorithm&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Adaptive_Algorithm "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Adaptive_Algorithm "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Adaptive_Algorithm&oldid=9011 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Adaptive_Algorithm&action=info "More information about this page")


  * This page was last edited on 27 October 2021, at 13:11.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


