# Band-pass Filter Taps
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps#searchInput)
Generates taps for a band-pass filter and stores it in variable called whatever the ID is set to. It's essentially a convenience wrapper for calling firdes.band_pass() or firdes.complex_band_pass(). None of the parameters are realtime changeable because the taps are created once at the beginning of the flowgraph. 
## Parameters 

ID
    Similar to the [Variable](https://wiki.gnuradio.org/index.php?title=Variable "Variable") block, the ID will hold the values generated. 

Tap Type
    Whether the taps are real or complex. 

Gain
    Scaling factor applied to output. 

Sample Rate
    Input sample rate. 

Low Cutoff Freq
    Lower cutoff frequency in Hz 

High Cutoff Freq
    Upper cutoff frequency in Hz 

Transition Width
    Transition width between stop-band and pass-band in Hz 

Window
    Type of window to use 

Beta
    The beta paramater only applies to the Kaiser window.
## Example Flowgraph
This flowgraph can be found at <https://github.com/gnuradio/gnuradio/blob/master/gr-filter/examples/filter_taps.grc>. 
[![](https://wiki.gnuradio.org/images/thumb/2/2b/Filter_taps_fg.png/800px-Filter_taps_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Filter_taps_fg.png)
## Source Files
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps&oldid=13099](https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps&oldid=13099)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Band-pass+Filter+Taps "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Band-pass_Filter_Taps&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps)
  * [View source](https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Band-pass_Filter_Taps "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Band-pass_Filter_Taps "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps&oldid=13099 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Band-pass_Filter_Taps&action=info "More information about this page")


  * This page was last edited on 27 April 2023, at 08:42.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


