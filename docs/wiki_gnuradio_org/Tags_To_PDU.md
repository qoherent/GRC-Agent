# Tags To PDU
`Added in 3.10`

## Parameters

**(_R_):** _Run-time adjustable_

PDU Type
    options: [Complex, Float, Int, Short, Byte]

Start Tag (_R_)
    default: SOB

End Tag (_R_)
    default: EOB

Max PDU Size (_R_)
    default: '1024'

Sample Rate (_R_)
    default: samp_rate

Prepend (_R_)
    default: '[]'

Config Port
    options: [Enabled, Disabled]

Emit Detections
    options: ['Yes', 'No']

Tail Size (_R_)
    default: '0'

EOB Alignment (_R_)
    default: '1'

EOB Offset (_R_)
    default: '0'

Start Time (s)
    default: '0.0'

Boost Time
    options: ['Yes', 'No']

## Messages

### Inputs

conf
    input message

### Outputs

detects
    output message

pdus
    output message

## Example Flowgraph

This flowgraph can be found at [[1]](https://raw.githubusercontent.com/gnuradio/gnuradio/master/gr-pdu/examples/tags_to_pdu_example.grc)


## Source Files

C++ files
    [[2]](https://github.com/gnuradio/gnuradio/blob/master/gr-pdu/lib/tags_to_pdu_impl.cc)

Header files
    [[3]](https://github.com/gnuradio/gnuradio/blob/master/gr-pdu/lib/tags_to_pdu_impl.h)

Public header files
    [[4]](https://github.com/gnuradio/gnuradio/blob/master/gr-pdu/include/gnuradio/pdu/tags_to_pdu.h)

Block definition
    [[5]](https://github.com/gnuradio/gnuradio/blob/master/gr-pdu/grc/pdu_tags_to_pdu.block.yml)
