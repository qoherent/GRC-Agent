# Virtual Sinks and Sources


A flowgraph with many blocks and connections can be come difficult to analyze visually. **Virtual Sinks and Virtual Sources** are blocks which can be used to simplify the look of a flowgraph.
## Connecting Virtual Sinks and Sources
Initially when virtual sinks and sources are added to a flowgraph they are given a white color, which represents that they do not have a data type yet. The data type will be assumed when connected to a block.
[](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_added_blocks.png)

Add a **Signal Source** and a **QT GUI Time Sink** block to the flowgraph, and connect them accordingly:
[](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_complex_connection.png)

Note that the previously white connections are now blue due to the complex type of the **Signal Source** and **QT GUI Time Sink** blocks. Add the following blocks and connect them:
  * Noise Source
  * QT GUI Freq Sink
  * Virtual Sink
  * Virtual Source

[](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_connection_errors.png)
There are now a couple errors in the flowgraph, highlighted by the red text in the blocks. There are two Virtual Sinks and neither named, therefore GRC does not know which connection to make for the two Virtual Sources. Does the top sink go to the top source? Or does the top sink go to the bottom source? To resolve the errors, names are given to the two sinks and two sources which clarify the connections. Open the two sinks and two sources and give each one the name _signalSource_ or _noiseSource_ :
[](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_properties_name.png)
[](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_named_connections.png)

Notice how the error messages have been resolved now that both the virtual sink and source blocks are named, removing any connection ambiguity.
## Simplifying Flowgraphs
The following flowgraph is relatively simple with only a handful of blocks but it has a series of interconnections which overlap with blocks, making the connections difficult to follow:
[](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_messy_flowgraph.png)

Virtual sinks and sources can be used to gather blocks in a logical and functional sense. For example, the original flowgraph has been simplified into three major functions: Signal Generation, Simulated Effects, and Plotting.
[](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_distinct_functions.png)

A series of virtual sinks and sources are used to visually isolate groups of blocks from one another, making the flowgraph easier to view visually. Each associated sink and source is given a distinct color.
[](https://wiki.gnuradio.org/index.php?title=File:Virtual_sink_source_highlighted_connections.png)
