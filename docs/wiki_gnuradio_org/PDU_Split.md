# PDU Split

Split PDU dict and data to separate ports.  
`Added in 3.10`  

Splits a PDU into its metadata dictionary and vector, outputs nothing if the input message is not a PDU. Useful for stripping metadata for printing or saving. 

## Parameters

**(_R_):** _Run-time adjustable_

Empty
    options: [Drop, Print]

## Messages

### Inputs

pdu
    input message

### Outputs

dict
    metadata dictionary

vec
    uniform vector

## Example Flowgraph

This flowgraph can be found at [[1]](https://raw.githubusercontent.com/gnuradio/gnuradio/master/gr-pdu/examples/pdu_tools_demo.grc)

## Source Files

C++ files
    [TODO](https://github.com/gnuradio/gnuradio)

Header files
    [TODO](https://github.com/gnuradio/gnuradio)

Public header files
    [TODO](https://github.com/gnuradio/gnuradio)

Block definition
    [[2]](https://github.com/gnuradio/gnuradio/blob/master/gr-pdu/grc/pdu_pdu_split.block.yml)
