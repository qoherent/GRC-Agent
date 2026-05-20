# Tagged Stream Blocks

A tagged stream block works on streamed but packetized input. Tagged stream
blocks use tags to identify PDU boundaries: the first item of a streamed PDU has
a tag with a specific key, and that tag stores the PDU length as a PMT integer.

Regular stream blocks do not inherently know packet boundaries. The Stream to
Tagged Stream adapter can add length tags at regular intervals so that a regular
stream can be consumed by a tagged stream block when the graph is otherwise
compatible.

Tagged-stream docs explain packetization and length-tag concepts only. They are
not graph mutation authority.

Provenance: Source title: Tagged Stream Blocks. Source URL:
https://wiki.gnuradio.org/index.php/Tagged_Stream_Blocks. Retrieval topic:
tagged stream blocks packet boundaries length tags. Aliases:
tagged_stream_blocks, packet_tags, packet_length_tags, stream_to_tagged_stream.
Official or primary: official GNU Radio Wiki page. Why relevant: this snippet
grounds docs QA rows about packet boundaries and length tags.
