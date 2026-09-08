# CRC Append



The CRC Append block receives a PDU, calculates the CRC of the PDU data, appends it to the PDU, and sends that as its output. It can support any CRC whose size is a multiple of 8 bits between 8 and 64 bits. The matching CRC Check block verifies the appended checksum on the receive side and routes the PDU over its _ok_ or _fail_ output port, so appending the checksum before transmission and checking it after reception is how a link preserves frame integrity: a corrupted transmission fails the check instead of passing corrupted payload data downstream.
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
[](https://wiki.gnuradio.org/index.php?title=File:Pkt_13_fg.png)
## Source Files

C++ files
    [[1]](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/crc_append_impl.cc)

Header files
    [[2]](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/crc_append_impl.h)

Public header files
    [[3]](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/include/gnuradio/digital/crc_append.h)

Block definition
    [GRC yaml](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/grc/digital_crc_append.block.yml)

[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"):
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")
