# Binary Files for DSP
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP#searchInput)
Binary files allow RF information to be recorded for offline usage. Before continuing it is useful to refer to the tutorial [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types") which describes different data types in GNU Radio. 
## Contents
  * [1 Data File Formats](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP#Data_File_Formats)
  * [2 Real and Complex Formats](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP#Real_and_Complex_Formats)
  * [3 Saving Samples as Binary Files](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP#Saving_Samples_as_Binary_Files)
  * [4 Endianness](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP#Endianness)


## Data File Formats
Each binary file will have a specific data format, with the two most common being 32-bit floats and 16-bit integers. RF samples can be both positive and negative, and therefore all integers will be implied to be signed integers, for simplicity. The binary file will save all samples in the same format back to back. For example, a binary file of 16-bit integers will save sample 0 as 16 bits, then sample 1 as 16-bits, sample 2 as 16-bits, and so on. 

```
[ sample 0: 16 bit int ][ sample 1: 16 bit int ][ sample 2: 16 bit int ] ...

```

For example, a binary file of 32-bit floats will save sample 0 as 32 bits, then sample 1 as 32-bits, sample 2 as 32-bits, and so on. 

```
[ sample 0: 32 bit float ][ sample 1: 32 bit float ][ sample 2: 32 bit float ] ...

```

## Real and Complex Formats
RF samples can be either real or complex. When a real sample is saved to a binary file each sample is saved in order: sample 0, then sample 1, then sample 2, and so on. 

```
[ real sample 0 ][ real sample 1 ][ real sample 2 ] ...

```

A complex sample, I + jQ, has both a real component (I) and imaginary component (Q). The I and Q components of each sample will be interleaved when saved to a binary file. I of sample 0, then Q of sample 0, then I of sample 1, then Q of sample 1, then I of sample 2, then Q of sample 2, and so on. 

```
[ I sample 0 ][ Q sample 0 ][ I sample 1 ][ Q sample 1 ][ I sample 2 ][ Q sample 2 ] ...

```

## Saving Samples as Binary Files
The different types of sample representations and binary file formats can be mixed and matched: 
  * Real samples stored as 16-bit integers
  * Real samples stored as 32-bit floats
  * Complex samples stored as interleaved 16-bit integers
  * Complex samples stored as interleaved 32-bit floats


Real samples stored as 16-bit integers: 

```
[ sample 0: 16 bit int ][ sample 1: 16 bit int ][ sample 2: 16 bit int ] ...

```

Real samples stored as 32-bit floats: 

```
[ sample 0: 32 bit float ][ sample 1: 32 bit float ][ sample 2: 32 bit float ] ...

```

Complex samples stored as interleaved 16-bit integers: 

```
[ I sample 0: 16 bit int ][ Q sample 0: 16 bit int ][ I sample 1: 16 bit int ][ Q sample 1: 16 bit int ][ I sample 2: 16 bit int ][ Q sample 2: 16 bit int ] ...

```

Complex samples stored as interleaved 32-bit floats: 

```
[ I sample 0: 32 bit float ][ Q sample 0: 32 bit float ][ I sample 1: 32 bit float ][ Q sample 1: 32 bit float ][ I sample 2: 32 bit float ] [ Q sample 2: 32 bit float ] ...

```

## Endianness
Endianness describes the order of bytes within binary data. Big endian and little endian systems differ on the placement of the most significant byte and least significant byte. Little endian systems place the least significant byte in the smallest memory address and the most significant byte in the largest memory address. Big endian systems store the most significant byte in the smallest memory address and the least significant byte in the largest memory address. 
For example, consider the hexadecimal value 0xABCD0123. A big endian system would store the value into memory by: 

```
Data Value:      [ 0xAB ] [ 0xCD] [ 0x01 ] [ 0x23 ]
Memory Address:  [ 0x00 ] [ 0x01] [ 0x02 ] [ 0x03 ]

```

A little endian system stores the bytes in the reversed order into memory: 

```
Data Value:      [ 0x23 ] [ 0x01] [ 0xCD ] [ 0xAB ] [BR]
Memory Address:  [ 0x00 ] [ 0x01] [ 0x02 ] [ 0x03 ]

```

Converting between endianness is sometimes referred to as a “byte swap” operation. The [Endian Swap](https://wiki.gnuradio.org/index.php?title=Endian_Swap "Endian Swap") block performs this endian conversion. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP&oldid=14335](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP&oldid=14335)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Binary+Files+for+DSP "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Binary_Files_for_DSP&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP)
  * [View source](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Binary_Files_for_DSP "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Binary_Files_for_DSP "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP&oldid=14335 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Binary_Files_for_DSP&action=info "More information about this page")


  * This page was last edited on 18 May 2024, at 16:49.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


