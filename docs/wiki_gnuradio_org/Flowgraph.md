# Flowgraph

A GNU Radio flowgraph is a graph of connected signal-processing blocks. Source
blocks provide samples, sink blocks terminate or export samples, and processing
blocks transform data between them. GNU Radio uses flowgraphs to model the
connections through which a continuous stream of samples flows.

In GNU Radio Companion, a `.grc` file records the visual flowgraph, and GRC can
translate that flowgraph into generated Python code. The flowgraph concept is
therefore the user-facing graph of blocks and connections, not a raw YAML edit
surface.

Flowgraph documentation explains graph structure only. It is not mutation
authority, does not authorize graph edits, and does not provide topology repair
rules.

Provenance: Source title: What Is GNU Radio / Handling Flowgraphs. Source URL:
https://wiki.gnuradio.org/index.php/What_Is_GNU_Radio and
https://github.com/gnuradio/gnuradio/blob/main/docs/usage-manual/(exported%20from%20wiki)%20Handling%20Flowgraphs.txt.
Retrieval topic: flowgraph blocks connections samples. Aliases: flowgraph,
top_block, graph, blocks, connections. Official or primary: official GNU Radio
Wiki and GNU Radio source-tree usage manual export. Why relevant: this snippet
grounds docs QA row Q11 asking what a flowgraph is without selecting
flowgraph-code or porting fragments.
