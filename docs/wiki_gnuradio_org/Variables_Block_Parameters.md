# Variables in Flowgraphs

GNU Radio Companion flowgraphs can contain Variable blocks. A variable has an
ID and a value, and that ID can be used in other block parameter fields. When a
block parameter references a variable, changing the variable changes the value
used by that block parameter when the flowgraph is generated or run.

Variables can depend on other variables by using Python expressions in the
value field. For example, a frequency variable may be expressed in terms of
`samp_rate`, and a block parameter may then use that variable.

Variable documentation explains how variables and block parameters relate. It
does not authorize graph mutation or choose parameter values.

Provenance: Source title: Variables in Flowgraphs. Source URL:
https://wiki.gnuradio.org/index.php/Variables_in_Flowgraphs. Retrieval topic:
variables blocks parameters flowgraph. Aliases: variables_in_flowgraphs,
variables_blocks, variable block, block parameters. Official or primary:
official GNU Radio Wiki page. Why relevant: this snippet grounds docs QA rows
about how GRC variables affect blocks and block parameters.
