# Basic OFDM Tutorial
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#searchInput)
## Contents
  * [1 Introduction](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#Introduction)
  * [2 Conventions and Notations](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#Conventions_and_Notations)
    * [2.1 FFT Shifting](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#FFT_Shifting)
    * [2.2 Carrier Indexing](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#Carrier_Indexing)
    * [2.3 Carrier and Symbol Allocation](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#Carrier_and_Symbol_Allocation)
  * [3 Detection and Synchronization](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#Detection_and_Synchronization)
  * [4 Transmitting](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#Transmitting)
  * [5 Receiving](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#Receiving)
  * [6 Example loopback flowgraph](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial#Example_loopback_flowgraph)


## Introduction
In the following, we assume the reader is familiar with [Orthogonal Frequency Division Multiplexing (OFDM)](https://en.wikipedia.org/wiki/Orthogonal_frequency-division_multiplexing) and how it works. For an introduction to OFDM, refer to standard textbooks on digital communication such as found in [Suggested Reading](https://wiki.gnuradio.org/index.php?title=SuggestedReading "SuggestedReading"). 
GNU Radio provides blocks to transmit and receive OFDM-modulated signals. These blocks are designed in a very generic fashion. As a developer, this means that often, a desired functionality can be achieved by correct parametrization of the available blocks, but in some cases, custom blocks have to be included. The design of the OFDM components is such that adding one's own functionality is possible with very little effort. 
## Conventions and Notations
### FFT Shifting
In all cases where OFDM symbols are passed between blocks, the default behaviour is to apply an FFT shift to the symbol vectors. This reorders the subcarriers so that the DC (zero-frequency) subcarrier appears at the center of the array, instead of at index 0. This shift is equivalent to the operation performed by functions such as NumPy’s fftshift. For an FFT length N, the DC component is placed at index floor(N/2). For instance, numpy.fft.fftshift([0, 1, 2, 3]) returns [2, 3, 0, 1]. 
The reason for this convention is that some blocks require FFT-shifted ordering of the symbols to function (such as gr::digital::ofdm_chanest_vcvc), and for consistency, this was chosen as a default for all blocks that pass OFDM symbols. Also, when viewing OFDM symbols, FFT-shifted symbols are in their natural order, i.e. as they appear in the pass band. 
### Carrier Indexing
Carriers are always indexed starting at the DC carrier, which has the index 0 (you usually don't want to occupy this carrier). The carriers right of the DC carrier (the ones at higher frequencies) are indexed with 1 through N/2-1 (N being the FFT length again). 
The carriers left of the DC carrier (with lower frequencies) can be indexed -N/2 through -1 or N/2 through N-1. Carrier indices N-1 and -1 are thus equivalent. The advantage of using negative carrier indices is that the FFT length can be changed without changing the carrier indexing. 
### Carrier and Symbol Allocation
Many blocks require knowledge of which carriers are allocated, and whether they carry data or pilot symbols. GNU Radio blocks uses three objects for this, typically called occupied_carriers (for the data symbols), pilot_carriers and pilot_symbols (for the pilot symbols). 
Every one of these objects is a vector of vectors. occupied_carriers and pilot_carriers identify the position within a frame where data and pilot symbols are stored, respectively. 
'occupied_carriers[0]' identifies which carriers are occupied on the first OFDM symbol, 'occupied_carriers[1]' does the same on the second OFDM symbol etc. 
Here's an example: 

```
 occupied_carriers = ((-2, -1, 1, 3), (-3, -1, 1, 2))
 pilot_carriers = ((-3, 2), (-2, 3))

```

Every OFDM symbol carries 4 data symbols. On the first OFDM symbol, they are on carriers -2, -1, 1 and 3. Carriers -3 and 2 are not used, so they are where the pilot symbols can be placed. On the second OFDM symbol, the occupied carriers are -3, -1, 1 and 2. The pilot symbols must thus be placed elsewhere, and are put on carriers -2 and 3. 
If there are more symbols in the OFDM frame than the length of occupied_carriers or pilot_carriers, they wrap around (in this example, the third OFDM symbol uses the allocation in occupied_carriers[0]). 
But how are the pilot symbols set? This is a valid parameterization: 

```
 pilot_symbols = ((-1, 1j), (1, -1j), (-1, 1j), (-1j, 1))

```

The position of these symbols are those in pilot_carriers. So on the first OFDM symbol, carrier -3 will transmit a -1, and carrier 2 will transmit a 1j. Note that pilot_symbols is longer than pilot_carriers in this example-- this is valid, the symbols in pilot_symbols[2] will be mapped according to pilot_carriers[0]. 
## Detection and Synchronization
Before anything happens, an OFDM frame must be detected, the beginning of OFDM symbols must be identified, and frequency offset must be estimated. 
## Transmitting
[![](https://wiki.gnuradio.org/images/6/60/Ofdm_tx_core.png)](https://wiki.gnuradio.org/index.php?title=File:Ofdm_tx_core.png)Core elements of an OFDM transmitter
This image shows a very simple example of a transmitter. It is assumed that the input is a stream of complex scalars with a length tag, i.e. the transmitter will work on one frame at a time. 
The first block is the carrier allocator (gr::digital::ofdm_carrier_allocator_cvc). This sorts the incoming complex scalars onto OFDM carriers, and also places the pilot symbols onto the correct positions. There is also the option to pass OFDM symbols which are prepended in front of every frame (i.e. preamble symbols). These can be used for detection, synchronisation and channel estimation. 
The carrier allocator outputs OFDM symbols (i.e. complex vectors of FFT length). These must be converted to time domain signals before continuing, which is why they are piped into an (I)FFT block. Note that because all the OFDM symbols are treated in the shifted form, the IFFT block must be shifting as well. 
Finally, the cyclic prefix is added to the OFDM symbols. The gr::digital::ofdm_cyclic_prefixer can also perform pulse shaping on the OFDM symbols (raised cosine flanks in the time domain). 
## Receiving
On the receiver side, some more effort is necessary. The following flow graph assumes that the input starts at the beginning of an OFDM frame and is prepended with a Schmidl & Cox preamble for coarse frequency correction and channel estimation. Also assumed is that the fine frequency offset is already corrected and that the cyclic prefix has been removed. The latter can be achieved by a gr::digital::header_payload_demux, the former can be done using a gr::digital::ofdm_sync_sc_cc. 
[![](https://wiki.gnuradio.org/images/6/63/Ofdm_rx_core.png)](https://wiki.gnuradio.org/index.php?title=File:Ofdm_rx_core.png)Core elements of an OFDM receiver
First, an FFT shifts the OFDM symbols into the frequency domain, where the signal processing is performed (the OFDM frame is thus in the memory in matrix form). It is passed to a block that uses the preambles to perform channel estimation and coarse frequency offset. Both of these values are added to the output stream as tags; the preambles are then removed from the stream and not propagated. 
Note that this block does not correct the OFDM frame. Both the coarse frequency offset correction and the equalizing (using the initial channel state estimate) are done in the following block, gr::digital::ofdm_frame_equalizer_vcvc. The interesting property about this block is that it uses a gr::digital::ofdm_equalizer_base derived object to perform the actual equalization. 
The last block in the frequency domain is the gr::digital::ofdm_serializer_vcc, which is the inverse block to the carrier allocator. It plucks the data symbols from the occupied_carriers and outputs them as a stream of complex scalars. These can then be directly converted to bits, or passed to a forward error correction decoder. 
## Example loopback flowgraph
[![](https://wiki.gnuradio.org/images/thumb/0/06/OFDM_loopback_BER_fg.png/800px-OFDM_loopback_BER_fg.png)](https://wiki.gnuradio.org/index.php?title=File:OFDM_loopback_BER_fg.png)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial&oldid=15485](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial&oldid=15485)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Tutorials](https://wiki.gnuradio.org/index.php?title=Category:Tutorials "Category:Tutorials")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Basic+OFDM+Tutorial "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Basic_OFDM_Tutorial&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial)
  * [View source](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Basic_OFDM_Tutorial "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Basic_OFDM_Tutorial "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial&oldid=15485 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Basic_OFDM_Tutorial&action=info "More information about this page")


  * This page was last edited on 1 December 2025, at 23:38.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


