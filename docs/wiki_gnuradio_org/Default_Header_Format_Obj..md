# Default Header Format Obj.
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj.#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj.#searchInput)
Default header formatter for PDU formatting. Used to handle the default packet header. 
See the parent class header_format_base for details of how these classes operate. 
The default header created in this base class consists of an access code and the packet length. The length is encoded as a 16-bit value repeated twice: 

```
 | access code | hdr | payload |

```

Where the access code is <= 64 bits and hdr is: 

```
 |  0 -- 15 | 16 -- 31 |
 | pkt len  | pkt len  |

```

The access code and header are formatted for network byte order. 
This header generator does not calculate or append a CRC to the packet. Use the CRC32 Async block for that before adding the header. The header's length will then measure the payload plus the CRC length (4 bytes for a CRC32). 
The default header parser produces a PMT dictionary that contains the following keys. All formatter blocks MUST produce these two values in any dictionary. 
See [[1]](https://www.gnuradio.org/doc/doxygen/classgr_1_1digital_1_1header__format__default.html) for more info. 
## Parameters 

Access Code
    An access code that is used to find and synchronize the start of a packet. Used in the parser and in other blocks like a corr_est block that helps trigger the receiver. Can be up to 64-bits long. 

Threshold
    How many bits can be wrong in the access code and still count as correct. 

Payload Bits per Symbol
    The number of bits per symbol used in the payload's modulator.
## Example Flowgraph
[![](https://wiki.gnuradio.org/images/thumb/7/76/Pkt_3_fg.png/800px-Pkt_3_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Pkt_3_fg.png)
## Source Files 

C++ files
    [TODO](https://github.com/gnuradio/gnuradio) 

Header files
    [TODO](https://github.com/gnuradio/gnuradio) 

Public header files
    [TODO](https://github.com/gnuradio/gnuradio) 

Block definition
    [TODO](https://github.com/gnuradio/gnuradio)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj.&oldid=9079](https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj.&oldid=9079)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Default+Header+Format+Obj. "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj. "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Default_Header_Format_Obj.&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj.)
  * [View source](https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj.&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj.&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Default_Header_Format_Obj. "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Default_Header_Format_Obj. "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj.&oldid=9079 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Default_Header_Format_Obj.&action=info "More information about this page")


  * This page was last edited on 12 November 2021, at 13:53.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


