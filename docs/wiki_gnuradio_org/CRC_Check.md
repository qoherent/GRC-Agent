# CRC Check



The CRC Check block receives a PDU containing a CRC at its end, and checks whether the CRC is correct. The PDU is sent over the _ok_ or _fail_ output ports according to the result of this check. It can support any CRC whose size is a multiple of 8 bits between 8 and 64 bits.
The block uses the same notation as [this online CRC calculator](http://www.sunshine2k.de/coding/javascript/crc/crc_js.html) to define the CRC code parameters. The calculator includes a list of commonly used CRC codes, so it is a useful resource to find the parameters that are needed for this block. The default parameters of CRC Check correspond to the CRC-32 code.
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

Discard CRC
    A boolean that selects whether the CRC should be removed from the output PDU.      **Note:** The default of "False" causes the CRC to be passed to the 'ok' output port.

Header bytes to skip
    Indicates the number of bytes at the beginning of the input PDU to consider as header. These header bytes are not used for the calculation of the CRC, but are included in the output PDU.
## Example Flowgraph
[](https://wiki.gnuradio.org/index.php?title=File:Pkt_13_fg.png)
## Source Files

C++ files
    [[1]](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/crc_check_impl.cc)

Header files
    [[2]](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/crc_check_impl.h)

Public header files
    [[3]](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/include/gnuradio/digital/crc_check.h)

Block definition
    [GRC yaml](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/grc/digital_crc_check.block.yml)

[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"):
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")
