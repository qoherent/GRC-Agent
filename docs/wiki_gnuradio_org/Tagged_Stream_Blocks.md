# Tagged Stream Blocks

A tagged stream block works on streamed but packetized input. Tagged stream
blocks use tags to identify PDU boundaries: the first item of a streamed PDU has
a tag with a specific key, and that tag stores the PDU length as a PMT integer.

Regular stream blocks do not inherently know packet boundaries. The Stream to
Tagged Stream adapter (gr::blocks::stream_to_tagged_stream) adds length tags at
regular intervals so that a regular stream can be consumed by a tagged stream
block when the graph is otherwise compatible. Per its documentation, the block
"converts a regular stream into a tagged stream" by adding a length tag every
`packet_len` items; if other blocks sit between it and a tagged stream block,
they must either keep the rate unchanged or adjust the tag value so the length
tags still represent the real packet length.

A transmitted burst is therefore structured as a length header followed by the
payload items it describes. The Header/Payload Demuxer (HPD) block is the
canonical receiver-side example of this split: it is "designed to demultiplex
packets from a bursty transmission" — it passes the header section to other
blocks for demodulation, and using the information from the demodulated header
it then outputs the payload. The header carries the length information that
tells the receiver how many payload items belong to the packet, which is why
receivers can consume bursts of yet-to-determine length.

Source: [Tagged Stream Blocks](https://wiki.gnuradio.org/index.php/Tagged_Stream_Blocks) on the official GNU Radio Wiki; block documentation for `gr::blocks::stream_to_tagged_stream` and `gr::digital::header_payload_demux` from the installed GNU Radio libraries.