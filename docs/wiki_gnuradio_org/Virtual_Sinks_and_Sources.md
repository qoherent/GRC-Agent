# Virtual Sinks and Sources
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Virtual_Sinks_and_Sources#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Virtual_Sinks_and_Sources#searchInput)
A flowgraph with many blocks and connections can be come difficult to analyze visually. **Virtual Sinks and Virtual Sources** are blocks which can be used to simplify the look of a flowgraph. 
## Connecting Virtual Sinks and Sources
Initially when virtual sinks and sources are added to a flowgraph they are given a white color, which represents that they do not have a data type yet. The data type will be assumed when connected to a block. 
[![](https://wiki.gnuradio.org/images/5/51/Virtual_sink_source_added_blocks.png)](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_added_blocks.png)
  
Add a **Signal Source** and a **QT GUI Time Sink** block to the flowgraph, and connect them accordingly: 
[![](https://wiki.gnuradio.org/images/9/99/Virtual_sink_source_complex_connection.png)](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_complex_connection.png)
  
Note that the previously white connections are now blue due to the complex type of the **Signal Source** and **QT GUI Time Sink** blocks. Add the following blocks and connect them: 
  * Noise Source
  * QT GUI Freq Sink
  * Virtual Sink
  * Virtual Source


[![](https://wiki.gnuradio.org/images/d/df/Virtual_sink_source_connection_errors.png)](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_connection_errors.png)
There are now a couple errors in the flowgraph, highlighted by the red text in the blocks. There are two Virtual Sinks and neither named, therefore GRC does not know which connection to make for the two Virtual Sources. Does the top sink go to the top source? Or does the top sink go to the bottom source? To resolve the errors, names are given to the two sinks and two sources which clarify the connections. Open the two sinks and two sources and give each one the name _signalSource_ or _noiseSource_ : 
[![](https://wiki.gnuradio.org/images/9/91/Virtual_sink_source_properties_name.png)](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_properties_name.png)
[![](https://wiki.gnuradio.org/images/6/6e/Virtual_sink_source_named_connections.png)](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_named_connections.png)
  
Notice how the error messages have been resolved now that both the virtual sink and source blocks are named, removing any connection ambiguity. 
## Simplifying Flowgraphs
The following flowgraph is relatively simple with only a handful of blocks but it has a series of interconnections which overlap with blocks, making the connections difficult to follow: 
[![](https://wiki.gnuradio.org/images/b/be/Virtual_sink_source_messy_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_messy_flowgraph.png)
  
Virtual sinks and sources can be used to gather blocks in a logical and functional sense. For example, the original flowgraph has been simplified into three major functions: Signal Generation, Simulated Effects, and Plotting. 
[![](https://wiki.gnuradio.org/images/8/89/Virtual_sink_source_distinct_functions.png)](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_distinct_functions.png)
  
A series of virtual sinks and sources are used to visually isolate groups of blocks from one another, making the flowgraph easier to view visually. Each associated sink and source is given a distinct color. 
[![](https://wiki.gnuradio.org/images/5/5a/Virtual_sink_source_highlighted_connections.png)](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_highlighted_connections.png)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Virtual_Sinks_and_Sources&oldid=13869](https://wiki.gnuradio.org/index.php?title=Virtual_Sinks_and_Sources&oldid=13869)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Virtual+Sinks+and+Sources "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Virtual_Sinks_and_Sources "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Virtual_Sinks_and_Sources&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Virtual_Sinks_and_Sources)
  * [View source](https://wiki.gnuradio.org/index.php?title=Virtual_Sinks_and_Sources&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Virtual_Sinks_and_Sources&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Virtual_Sinks_and_Sources "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Virtual_Sinks_and_Sources "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Virtual_Sinks_and_Sources&oldid=13869 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Virtual_Sinks_and_Sources&action=info "More information about this page")


  * This page was last edited on 5 April 2024, at 22:13.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


