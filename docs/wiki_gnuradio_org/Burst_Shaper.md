# Burst Shaper
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Burst_Shaper#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Burst_Shaper#searchInput)
Burst shaper block for applying burst padding and ramping. 
This block applies a configurable amount of zero padding before and/or after a burst indicated by tagged stream length tags. 
If phasing symbols are used, an alternating pattern of +1/-1 symbols of length ceil(N/2) will be inserted before and after each burst, where N is the length of the taps vector. The ramp- up/ramp-down shape will be applied to these phasing symbols. 
If phasing symbols are not used, the taper will be applied directly to the head and tail of each burst. 
Length tags will be updated to include the length of any added zero padding or phasing symbols and will be placed at the beginning of the modified tagged stream. Any other tags found at the same offset as a length tag will also be placed at the beginning of the modified tagged stream, since these tags are assumed to be associated with the burst rather than a specific sample. For example, if "tx_time" tags are used to control bursts, their offsets should be consistent with their associated burst's length tags. Tags at other offsets will be placed with the samples on which they were found. 
## Parameters 

Window Taps
    Vector of window taper taps; the first ceil(N/2) items are the up flank and the last ceil(N/2) items are the down flank. If taps.size() is odd, the middle tap will be used as the last item of the up flank and first item of the down flank. 

Pre-padding Length
    Number of zero samples to insert before the burst. 

Post-padding Length
    Number of zero samples to append after the burst. 

Insert Phasing Symbols
    If true, insert alternating +1/-1 pattern of length ceil(N/2) before and after the burst and apply ramp up and ramp down taps, respectively, to the inserted patterns instead of the head and tail items of the burst. 

Length Tag Name
    The name of the tagged stream length tag key.
## Example Flowgraph 

Example 1
    This flowgraph can be found at [[1]](https://github.com/gnuradio/gnuradio/blob/master/gr-digital/examples/packet/packet_tx.grc)     [![](https://wiki.gnuradio.org/images/thumb/e/ec/Packet_tx_fg.png/746px-Packet_tx_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Packet_tx_fg.png) 

Example 2
    Another flowgraph can be found in [Media:burst_shaper_demo.grc](https://wiki.gnuradio.org/images/b/b6/Burst_shaper_demo.grc "Burst shaper demo.grc"). It shows how the output of the graph changes with or without the insertion of phasing symbols, and without envelope shaping using a window function (equivalent to window.WIN_RECTANGULAR). The GUI Time Sinks use layout hints.     [![](https://wiki.gnuradio.org/images/thumb/6/68/Burst_shaper_flowgraph.png/600px-Burst_shaper_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Burst_shaper_flowgraph.png)     The change in the waveform can be seen as follows:     [![](https://wiki.gnuradio.org/images/thumb/b/bd/Burst_shaper_output.png/600px-Burst_shaper_output.png)](https://wiki.gnuradio.org/index.php?title=File:Burst_shaper_output.png)
## Source Files 

C++ files
    [burst_shaper_impl.cc](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/burst_shaper_impl.cc) 

Header files
    [burst_shaper_impl.h](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/burst_shaper_impl.h) 

Public header files
    [burst_shaper.h](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/include/gnuradio/digital/burst_shaper.h) 

Block definition
    [digital_burst_shaper.block.yml](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/grc/digital_burst_shaper.block.yml)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Burst_Shaper&oldid=14687](https://wiki.gnuradio.org/index.php?title=Burst_Shaper&oldid=14687)"
[Categories](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")
  * [Tested With 3.10](https://wiki.gnuradio.org/index.php?title=Category:Tested_With_3.10 "Category:Tested With 3.10")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Burst+Shaper "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Burst_Shaper "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Burst_Shaper&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Burst_Shaper)
  * [View source](https://wiki.gnuradio.org/index.php?title=Burst_Shaper&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Burst_Shaper&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Burst_Shaper "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Burst_Shaper "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Burst_Shaper&oldid=14687 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Burst_Shaper&action=info "More information about this page")


  * This page was last edited on 27 March 2025, at 16:39.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


