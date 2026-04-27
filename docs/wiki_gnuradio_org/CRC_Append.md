# CRC Append
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=CRC_Append#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=CRC_Append#searchInput)
  
The CRC Append block receives a PDU, calculates the CRC of the PDU data, appends it to the PDU, and sends that as its output. It can support any CRC whose size is a multiple of 8 bits between 8 and 64 bits. 
The block uses the same notation as [this online CRC calculator](http://www.sunshine2k.de/coding/javascript/crc/crc_js.html) to define the CRC code parameters. The calculator includes a list of commonly used CRC codes, so it is a useful resource to find the parameters that are needed for this block. The default parameters of CRC Append correspond to the CRC-32 code. 
`Added in 3.10.2.0`
## Parameters 

CRC size (bits)
    The size of the CRC in bits. It must be a multiple of 8 bits between 8 and 64. 

CRC polynomial
    The CRC polynomial. 

Initial register value
    The initial value to load into the CRC register. 

Final XOR value
    The value that is XORed with the CRC immediately before producing the final result. 

LSB-first input
    A boolean that indicates if the input bytes should be processed least-significant bit first or not. 

LSB-first result
    A boolean that indicates if the output should be treated LSB-first, thus inverting the order of all the bits in the output. 

LSB CRC in PDU
    A boolean that indicates if the CRC field to be appended to the PDU should be least-significant-byte first. Set to True for compatibility with [Stream CRC32](https://wiki.gnuradio.org/index.php?title=Stream_CRC32 "Stream CRC32") 

Header bytes to skip
    Indicates the number of bytes at the beginning of the input PDU to consider as header. These header bytes are not used for the calculation of the CRC, but are included in the output PDU.
## Example Flowgraph
[![](https://wiki.gnuradio.org/images/thumb/9/9d/Pkt_13_fg.png/799px-Pkt_13_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Pkt_13_fg.png)
## Source Files 

C++ files
    [[1]](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/crc_append_impl.cc) 

Header files
    [[2]](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/crc_append_impl.h) 

Public header files
    [[3]](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/include/gnuradio/digital/crc_append.h) 

Block definition
    [GRC yaml](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/grc/digital_crc_append.block.yml)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=CRC_Append&oldid=15514](https://wiki.gnuradio.org/index.php?title=CRC_Append&oldid=15514)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=CRC+Append "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=CRC_Append "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:CRC_Append&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=CRC_Append)
  * [View source](https://wiki.gnuradio.org/index.php?title=CRC_Append&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=CRC_Append&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/CRC_Append "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/CRC_Append "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=CRC_Append&oldid=15514 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=CRC_Append&action=info "More information about this page")


  * This page was last edited on 9 December 2025, at 19:18.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


