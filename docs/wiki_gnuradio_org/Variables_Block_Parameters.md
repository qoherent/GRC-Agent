# Variables in Flowgraphs

GNU Radio Companion flowgraphs can contain Variable blocks. A variable has an
ID and a value, and that ID can be used in other block parameter fields. When a
block parameter references a variable, changing the variable changes the value
used by that block parameter when the flowgraph is generated or run.

Variables can depend on other variables by using Python expressions in the
value field. For example, a frequency variable may be expressed in terms of
`samp_rate`, and a block parameter may then use that variable.

Variable documentation explains how variables and block parameters relate.

Source: [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php/Variables_in_Flowgraphs) on the official GNU Radio Wiki.
