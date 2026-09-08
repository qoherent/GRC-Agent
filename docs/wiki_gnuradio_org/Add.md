# Add


Add samples across all input streams.
For all n samples on all M input streams x_m:

```
output[n] = sum( x_0[n], x_1[n], ..., x_m[n])

```

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

[](https://wiki.gnuradio.org/index.php?title=File:Add_block_fg.png)
[](https://wiki.gnuradio.org/index.php?title=File:Add_Block_out.png)
## Source Files

C++ files
    [add_blk_impl.cc](https://github.com/gnuradio/gnuradio/blob/main/gr-blocks/lib/add_blk_impl.cc)

Header files
    [add_blk_impl.h](https://github.com/gnuradio/gnuradio/blob/main/gr-blocks/lib/add_blk_impl.h)

Public header files
    [add_blk.h](https://github.com/gnuradio/gnuradio/blob/main/gr-blocks/include/gnuradio/blocks/add_blk.h)

Block definition
    [blocks_add_xx.block.yml](https://github.com/gnuradio/gnuradio/blob/main/gr-blocks/grc/blocks_add_xx.block.yml)

[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"):
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")
