# Default Header Format Obj.


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
[](https://wiki.gnuradio.org/index.php?title=File:Pkt_3_fg.png)
## Source Files

C++ files
    [TODO](https://github.com/gnuradio/gnuradio)

Header files
    [TODO](https://github.com/gnuradio/gnuradio)

Public header files
    [TODO](https://github.com/gnuradio/gnuradio)

Block definition
    [TODO](https://github.com/gnuradio/gnuradio)

[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"):
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")
