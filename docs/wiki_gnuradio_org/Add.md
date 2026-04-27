# Add
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Add#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Add#searchInput)
Add samples across all input streams. 
For all n samples on all M input streams x_m: 

```
output[n] = sum( x_0[n], x_1[n], ..., x_m[n])

```

## Contents
  * [1 Supported Data Types](https://wiki.gnuradio.org/index.php?title=Add#Supported_Data_Types)
  * [2 Parameters](https://wiki.gnuradio.org/index.php?title=Add#Parameters)
  * [3 Example Flowgraph](https://wiki.gnuradio.org/index.php?title=Add#Example_Flowgraph)
  * [4 Source Files](https://wiki.gnuradio.org/index.php?title=Add#Source_Files)


## Supported Data Types
  * Complex
  * Float
  * Int
  * Short


## Parameters 

IO Type
    Supported data types 
  * Complex
  * Float
  * Int
  * Short



Vec Length
    Length of the vector 

Num Inputs
    Number of streams to add
## Example Flowgraph
This flowgraph uses an Add Block to generate the classic "dial tone".  

[![](https://wiki.gnuradio.org/images/thumb/d/d7/Add_block_fg.png/700px-Add_block_fg.png)](https://wiki.gnuradio.org/index.php?title=File:Add_block_fg.png)
[![](https://wiki.gnuradio.org/images/thumb/8/82/Add_Block_out.png/700px-Add_Block_out.png)](https://wiki.gnuradio.org/index.php?title=File:Add_Block_out.png)
## Source Files 

C++ files
    [add_blk_impl.cc](https://github.com/gnuradio/gnuradio/blob/main/gr-blocks/lib/add_blk_impl.cc) 

Header files
    [add_blk_impl.h](https://github.com/gnuradio/gnuradio/blob/main/gr-blocks/lib/add_blk_impl.h) 

Public header files
    [add_blk.h](https://github.com/gnuradio/gnuradio/blob/main/gr-blocks/include/gnuradio/blocks/add_blk.h) 

Block definition
    [blocks_add_xx.block.yml](https://github.com/gnuradio/gnuradio/blob/main/gr-blocks/grc/blocks_add_xx.block.yml)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Add&oldid=12528](https://wiki.gnuradio.org/index.php?title=Add&oldid=12528)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Add "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Add "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Add&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Add)
  * [View source](https://wiki.gnuradio.org/index.php?title=Add&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Add&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Add "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Add "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Add&oldid=12528 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Add&action=info "More information about this page")


  * This page was last edited on 25 August 2022, at 09:23.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


