# M-ASK, M-PSK, and QAM-M Mod and Demod
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#searchInput)
Purpose of the LAB: To put into practice M-ASK, M-PSK and QAM-M mod/demod and observe their performances/differences. 
Educational objectives: 
- Consider M-ary modulations with different schemes. 
- Visualize the constellation of modulations. 
- Analyze their spectral efficiency and noise sensitivity 
- Compare performances via BER 
## Contents
  * [1 BPSK/2-ASK](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#BPSK/2-ASK)
    * [1.1 Variables](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#Variables)
    * [1.2 Blocks Setting](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#Blocks_Setting)
  * [2 M-PSK](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#M-PSK)
    * [2.1 QPSK](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#QPSK)
    * [2.2 8-PSK](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#8-PSK)
  * [3 M-ASK](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#M-ASK)
    * [3.1 4-ASK](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#4-ASK)
    * [3.2 8-ASK](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#8-ASK)
  * [4 QAM-M](https://wiki.gnuradio.org/index.php?title=M-ASK%2C_M-PSK%2C_and_QAM-M_Mod_and_Demod#QAM-M)


## BPSK/2-ASK
At first, run [Media:BPSK.grc](https://wiki.gnuradio.org/images/9/9e/BPSK.grc "BPSK.grc") corresponding to the flowgraph : 
[![](https://wiki.gnuradio.org/images/thumb/9/92/BPSK.png/800px-BPSK.png)](https://wiki.gnuradio.org/index.php?title=File:BPSK.png)
Refer to the section <https://wiki.gnuradio.org/index.php?title=Simulation_example:_BPSK_Demodulation> for explanation. 
### Variables
Contents of the variables 'Delay', 'rrc-taps' and 'timing_loop_bw' 
Delay: int (5.5 * sps + 7) 
rrc_ taps : firdes.root _raised_ cosine ( nfilts , nfilts , 1.0/float( sps ), Alpha, 11* sps * nfilts ) 
timing_loop_bw : 0.0628 
### Blocks Setting
[![](https://wiki.gnuradio.org/images/thumb/7/71/Block_setting.png/800px-Block_setting.png)](https://wiki.gnuradio.org/index.php?title=File:Block_setting.png)
## M-PSK
### QPSK
Modify the previous flowgraph to obtain a QPSK [Media:QPSK.grc](https://wiki.gnuradio.org/images/5/58/QPSK.grc "QPSK.grc")
[![](https://wiki.gnuradio.org/images/thumb/7/7c/QPSK.png/800px-QPSK.png)](https://wiki.gnuradio.org/index.php?title=File:QPSK.png) - Edit Constellation object QPSK. 
- Modulus Edit 4. 
- Add a K-bit Unpack block (K: 2) to be placed as shown in the flowgraph 
- Multiply the delay by 2 --> int (5.5 * sps + 7)*2 
- Observe the BER as the noise power increases. 
### 8-PSK
Modify the previous flowgraph to obtain an 8-PSK [Media:8psk.grc](https://wiki.gnuradio.org/images/0/04/8psk.grc "8psk.grc")
[![](https://wiki.gnuradio.org/images/thumb/1/1e/8psk.png/800px-8psk.png)](https://wiki.gnuradio.org/index.php?title=File:8psk.png) - Edit Constellation object 8PSK 
- Modulus Edit 8 
- K-bit Unpack block (K: 3). 
- Observe the BER as the noise power increases. 
- Multiply the delay by 3 --> int (5.5 * sps + 7)*3 

```
[![](https://wiki.gnuradio.org/images/thumb/3/34/8PSK_Output.png/800px-8PSK_Output.png)](https://wiki.gnuradio.org/index.php?title=File:8PSK_Output.png)

```

## M-ASK
### 4-ASK
Modify the QPSK flowgraph to obtain a 4-ASK. 
- Replace the Constellation Object by Constellation Rect Object block 
[![](https://wiki.gnuradio.org/images/thumb/f/f5/4ASK_Const.png/400px-4ASK_Const.png)](https://wiki.gnuradio.org/index.php?title=File:4ASK_Const.png)
4-ASK [Media:4ASK.grc](https://wiki.gnuradio.org/images/7/75/4ASK.grc "4ASK.grc") whose flowgraph is given below : 
[![](https://wiki.gnuradio.org/images/thumb/c/c2/4ASK.png/800px-4ASK.png)](https://wiki.gnuradio.org/index.php?title=File:4ASK.png) - Modify Constellation Rect Object as shown above. 
- Modulus Edit 4. 
- K-bit Unpack block (K: 2) 
- Multiply the delay by 2 --> int (5.5 * sps + 7)*2 
- Observe the BER as the noise power increases. 
[![](https://wiki.gnuradio.org/images/thumb/f/fb/4ASK_Output.png/800px-4ASK_Output.png)](https://wiki.gnuradio.org/index.php?title=File:4ASK_Output.png)
### 8-ASK
8-ASK [Media:8ASK.grc](https://wiki.gnuradio.org/images/f/fd/8ASK.grc "8ASK.grc") whose flowgraph is given below : 
[![](https://wiki.gnuradio.org/images/thumb/0/00/8ASK.png/800px-8ASK.png)](https://wiki.gnuradio.org/index.php?title=File:8ASK.png)
- Edit Constellation Rect Object as shown 

```
[![](https://wiki.gnuradio.org/images/thumb/a/a2/8ASK_Const.png/400px-8ASK_Const.png)](https://wiki.gnuradio.org/index.php?title=File:8ASK_Const.png)

```

## QAM-M
QAM-16 [Media:QAM16.grc](https://wiki.gnuradio.org/images/e/e9/QAM16.grc "QAM16.grc") whose flowgraph is given below : 
[![](https://wiki.gnuradio.org/images/thumb/0/02/QAM16.png/800px-QAM16.png)](https://wiki.gnuradio.org/index.php?title=File:QAM16.png)
- Modify the QPSK flowgraph to obtain a 16-QAM 
- Edit Constellation object 16QAM 
- Modulus Edit 16 
- K-bit Unpack block (K: 4). 
- Observe the BER as the noise power increases. 
- Multiply the delay by 4 --> int (5.5 * sps + 7)*4 
[![](https://wiki.gnuradio.org/images/thumb/2/2a/16QAM_Output.png/800px-16QAM_Output.png)](https://wiki.gnuradio.org/index.php?title=File:16QAM_Output.png)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod&oldid=15409](https://wiki.gnuradio.org/index.php?title=M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod&oldid=15409)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=M-ASK%2C+M-PSK%2C+and+QAM-M+Mod+and+Demod "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod)
  * [View source](https://wiki.gnuradio.org/index.php?title=M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod&oldid=15409 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=M-ASK,_M-PSK,_and_QAM-M_Mod_and_Demod&action=info "More information about this page")


  * This page was last edited on 2 October 2025, at 17:10.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


