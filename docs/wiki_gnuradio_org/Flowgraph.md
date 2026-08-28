# Flowgraph

A GNU Radio flowgraph is a graph of connected signal-processing blocks. Source
blocks provide samples, sink blocks terminate or export samples, and processing
blocks transform data between them. GNU Radio uses flowgraphs to model the
connections through which a continuous stream of samples flows.

In GNU Radio Companion, a `.grc` file records the visual flowgraph, and GRC can
translate that flowgraph into generated Python code. The flowgraph concept is
therefore the user-facing graph of blocks and connections, not a raw YAML edit
surface.

Source: [Handling Flowgraphs](https://wiki.gnuradio.org/index.php/What_Is_GNU_Radio) and the GNU Radio usage-manual export in the GNU Radio source tree.