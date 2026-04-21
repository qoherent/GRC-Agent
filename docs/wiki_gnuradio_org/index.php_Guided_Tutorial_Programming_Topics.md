# Guided Tutorial Programming Topics
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php/Guided_Tutorial_Programming_Topics#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php/Guided_Tutorial_Programming_Topics#searchInput)
Tags, Messages and PMTs. 
## Contents
  * [1 Objectives](https://wiki.gnuradio.org/index.php/Guided_Tutorial_Programming_Topics#Objectives)
  * [2 Prerequisites](https://wiki.gnuradio.org/index.php/Guided_Tutorial_Programming_Topics#Prerequisites)
  * [3 Polymorphic Types (PMT)](https://wiki.gnuradio.org/index.php/Guided_Tutorial_Programming_Topics#Polymorphic_Types_\(PMT\))
  * [4 Stream Tags](https://wiki.gnuradio.org/index.php/Guided_Tutorial_Programming_Topics#Stream_Tags)
  * [5 Message Passing](https://wiki.gnuradio.org/index.php/Guided_Tutorial_Programming_Topics#Message_Passing)


## Objectives
  * Learn about PMTs
  * Understand what tags are / what they do / when to use them
  * Understand difference between streaming and message passing
  * Point to the manual for more advanced block manipulation


## Prerequisites
  * General familiarity with C++ and Python
  * Tutorials: 
    * [**A brief introduction to GNU Radio, SDR, and DSP**](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Introduction "Guided Tutorial Introduction")
    * [**Intro to GR usage: GRC and flowgraphs**](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_GRC "Guided Tutorial GRC")


* * *
So far, we have only discussed data streaming from one block to another. The data often consists of samples, and a streaming architecture makes a lot of sense for those. For example, a sound card driver block will constantly produce audio samples once active. 
In some cases, we don't want to pipe a stream of samples, though, but rather pass individual messages to another block, such as "this is the first sample of a burst", or "change the transmit frequency to 144 MHz". Or consider a MAC layer on top of a PHY: At higher communication levels, data is usually passed around in PDUs (protocol data units) instead of streams of items. 
In GNU Radio we have two mechanisms to pass these messages: 
  * Synchronously to a data stream, using [**stream tags**](https://wiki.gnuradio.org/index.php/Stream_Tags)
  * Asynchronously, using the [**message passing interface**](https://wiki.gnuradio.org/index.php/Message_Passing)


Before we discuss these, let's consider what such a message is from a programming perspective. It could be a string, a vector of items, a dictionary... anything, really, that can be represented as a data type. In Python, this would not be a problem, since it is weakly typed, and a message variable could simply be assigned whatever we need. C++ on the other hand is strongly typed, and it is not possible to create a variable without knowing its type. What makes things harder is that we need to be able to share the same data objects between Python and C++. To circumvent this problem, we introduce _polymorphic types (PMTs)_. 
## Polymorphic Types (PMT)
<content merged with PMT page in usage manual>
OK, so now we know all about creating messages - but how do we send them from block to block? 
## Stream Tags
<merged with Stream Tags usage manual page>
## Message Passing
<moved to message passing page of the usage manual>
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Programming_Topics&oldid=8455](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Programming_Topics&oldid=8455)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Guided Tutorials](https://wiki.gnuradio.org/index.php?title=Category:Guided_Tutorials "Category:Guided Tutorials")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Guided+Tutorial+Programming+Topics "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Programming_Topics "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Guided_Tutorial_Programming_Topics&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Programming_Topics)
  * [View source](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Programming_Topics&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Programming_Topics&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Guided_Tutorial_Programming_Topics "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Guided_Tutorial_Programming_Topics "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Programming_Topics&oldid=8455 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Programming_Topics&action=info "More information about this page")


  * This page was last edited on 8 April 2021, at 11:45.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


