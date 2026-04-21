# VOLK Guide
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=VOLK_Guide#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=VOLK_Guide#searchInput)
## Contents
  * [1 Introduction](https://wiki.gnuradio.org/index.php?title=VOLK_Guide#Introduction)
  * [2 Setting and Using Memory Alignment Information](https://wiki.gnuradio.org/index.php?title=VOLK_Guide#Setting_and_Using_Memory_Alignment_Information)
  * [3 Calling VOLK kernels in Work()](https://wiki.gnuradio.org/index.php?title=VOLK_Guide#Calling_VOLK_kernels_in_Work\(\))
  * [4 Tuning VOLK Performance](https://wiki.gnuradio.org/index.php?title=VOLK_Guide#Tuning_VOLK_Performance)
    * [4.1 Hand-Tuning Performance](https://wiki.gnuradio.org/index.php?title=VOLK_Guide#Hand-Tuning_Performance)


## Introduction
Note: Many blocks have already been converted to use VOLK in their calls, so they can also serve as examples. See the gr::blocks::complex_to_<type>.h files for examples of various blocks that make use of VOLK. 
VOLK is the Vector-Optimized Library of Kernels. It is a library that contains kernels of hand-written SIMD code for different mathematical operations. Since each SIMD architecture can be greatly different and no compiler has yet come along to handle vectorization properly or highly efficiently, VOLK approaches the problem differently. For each architecture or platform that a developer wishes to vectorize for, a new proto-kernel is added to VOLK. At runtime, VOLK will select the correct proto-kernel. In this way, the users of VOLK call a kernel for performing the operation that is platform/architecture agnostic. This allows us to write portable SIMD code. 
VOLK kernels are always defined with a 'generic' proto-kernel, which is written in plain C. With the generic kernel, the kernel becomes portable to any platform. Kernels are then extended by adding proto-kernels for new platforms in which they are desired. 
A good example of a VOLK kernel with multiple proto-kernels defined is the volk_32f_s32f_multiply_32f_a. This kernel implements a scalar multiplication of a vector of floating point numbers (each item in the vector is multiplied by the same value). This kernel has the following proto-kernels that are defined for 'generic,' 'avx,' 'sse,' and 'neon' 

```
   void volk_32f_s32f_multiply_32f_a_generic
   void volk_32f_s32f_multiply_32f_a_sse
   void volk_32f_s32f_multiply_32f_a_avx
   void volk_32f_s32f_multiply_32f_a_neon

```

These proto-kernels means that on platforms with AVX support, VOLK can select this option or the SSE option, depending on which is faster. If all else fails, VOLK can fall back on the generic proto-kernel, which will always work. 
See [libvolk.org](http://libvolk.org) for details on the VOLK naming scheme. 
## Setting and Using Memory Alignment Information
For VOLK to work as best as possible, we want to use memory-aligned SIMD calls, which means we have to have some way of knowing and controlling the alignment of the buffers passed to gr_block's work function. We set the alignment requirement for SIMD aligned memory calls with: 

```
 const int alignment_multiple =
   volk_get_alignment() / output_item_size;
 set_alignment(std::max(1,alignment_multiple));

```

The VOLK function 'volk_get_alignment' provides the alignment of the the machine architecture. We then base the alignment on the number of output items required to maintain the alignment, so we divide the number of alignment bytes by the number of bytes in an output items (sizeof(float), sizeof(gr_complex), etc.). This value is then set per block with the 'set_alignment' function. 
Because the scheduler tries to optimize throughput, the number of items available per call to work will change and depends on the availability of the read and write buffers. This means that it sometimes cannot produce a buffer that is properly memory aligned. This is an inevitable consequence of the scheduler system. Instead of requiring alignment, the scheduler enforces the alignment as much as possible, and when a buffer becomes unaligned, the scheduler will work to correct it as much as possible. If a block's buffers are unaligned, then, the scheduler sets a flag to indicate as much so that the block can then decide what best to do. The next section discusses the use of the aligned/unaligned information in a gr_block's work function. 
## Calling VOLK kernels in Work()
The buffers passed to work/general_work in a gr_block are not guaranteed to be aligned, but they will mostly be aligned whenever possible. When not aligned, the 'is_unaligned()' flag will be set so the scheduler knows to try to realign the buffers. We actually make calls to the VOLK dispatcher, which is mainly designed to check the buffer alignments and call the correct version of the kernel for us. From the user-level view of VOLK, calling the dispatcher allows us to ignore the concept of aligned versus unaligned. This looks like: 

```
 int
 gr_some_block::work (int noutput_items,
                      gr_vector_const_void_star &input_items,
                      gr_vector_void_star &output_items)
 {
   const float *in = (const float *) input_items[0];
   float *out = (float *) output_items[0];
 
   // Call the dispatcher to check alignment and call the _a or _u
   // version of the kernel.
   volk_32f_something_32f(out, in, noutput_items);
 
   return noutput_items;
 }

```

## Tuning VOLK Performance
VOLK comes with a profiler that will build a config file for the best SIMD architecture for your processor. Run volk_profile that is installed into $PREFIX/bin. This program tests all known VOLK kernels for each architecture supported by the processor. When finished, it will write to $HOME/.volk/volk_config the best architecture for the VOLK function. This file is read when using a function to know the best version of the function to execute. 
### Hand-Tuning Performance
If you know a particular architecture works best for your processor, you can specify the particular architecture to use in the VOLK preferences file: $HOME/.volk/volk_config 
The file looks like: 

```
volk_<FUNCTION_NAME> <ARCHITECTURE>

```

Where the "FUNCTION_NAME" is the particular function that you want to over-ride the default value and "ARCHITECTURE" is the VOLK SIMD architecture to use (generic, sse, sse2, sse3, avx, etc.). For example, the following config file tells VOLK to use SSE3 for the aligned and unaligned versions of a function that multiplies two complex streams together. 

```
volk_32fc_x2_multiply_32fc_a sse3
volk_32fc_x2_multiply_32fc_u sse3

```

**Tip:** If benchmarking GNU Radio blocks, it can be useful to have a volk_config file that sets all architectures to 'generic' as a way to test the vectorized versus non-vectorized implementations. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=VOLK_Guide&oldid=4875](https://wiki.gnuradio.org/index.php?title=VOLK_Guide&oldid=4875)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Usage Manual](https://wiki.gnuradio.org/index.php?title=Category:Usage_Manual "Category:Usage Manual")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=VOLK+Guide "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=VOLK_Guide "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:VOLK_Guide&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=VOLK_Guide)
  * [View source](https://wiki.gnuradio.org/index.php?title=VOLK_Guide&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=VOLK_Guide&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/VOLK_Guide "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/VOLK_Guide "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=VOLK_Guide&oldid=4875 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=VOLK_Guide&action=info "More information about this page")


  * This page was last edited on 12 March 2019, at 22:44.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


