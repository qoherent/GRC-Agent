# Guided Tutorial Programming Topics


Tags, Messages and PMTs.

## Objectives
  * Learn about PMTs
  * Understand what tags are / what they do / when to use them
  * Understand difference between streaming and message passing
  * Point to the manual for more advanced block manipulation

## Prerequisites
  * General familiarity with C++ and Python
  * Tutorials:
    * [**A brief introduction to GNU Radio, SDR, and DSP**](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_Introduction "Guided Tutorial Introduction")
    * [**Intro to GR usage: GRC and flowgraphs**](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_GRC "Guided Tutorial GRC")

* * *
So far, we have only discussed data streaming from one block to another. The data often consists of samples, and a streaming architecture makes a lot of sense for those. For example, a sound card driver block will constantly produce audio samples once active.
In some cases, we don't want to pipe a stream of samples, though, but rather pass individual messages to another block, such as "this is the first sample of a burst", or "change the transmit frequency to 144 MHz". Or consider a MAC layer on top of a PHY: At higher communication levels, data is usually passed around in PDUs (protocol data units) instead of streams of items.
In GNU Radio we have two mechanisms to pass these messages:
  * Synchronously to a data stream, using [**stream tags**](https://wiki.gnuradio.org/index.php/Stream_Tags)
  * Asynchronously, using the [**message passing interface**](https://wiki.gnuradio.org/index.php/Message_Passing)

Before we discuss these, let's consider what such a message is from a programming perspective. It could be a string, a vector of items, a dictionary... anything, really, that can be represented as a data type. In Python, this would not be a problem, since it is weakly typed, and a message variable could simply be assigned whatever we need. C++ on the other hand is strongly typed, and it is not possible to create a variable without knowing its type. What makes things harder is that we need to be able to share the same data objects between Python and C++. To circumvent this problem, we introduce _polymorphic types (PMTs)_.
## Polymorphic Types (PMT)
<content merged with PMT page in usage manual>
OK, so now we know all about creating messages - but how do we send them from block to block?
## Stream Tags
<merged with Stream Tags usage manual page>
## Message Passing
<moved to message passing page of the usage manual>

[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"):
  * [Guided Tutorials](https://wiki.gnuradio.org/index.php?title=Category:Guided_Tutorials "Category:Guided Tutorials")
