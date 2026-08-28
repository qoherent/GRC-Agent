# grcc

`grcc` is the GNU Radio Companion Compiler. It compiles a GNU Radio Companion
`.grc` flowgraph without launching the graphical interface. Given an input GRC
file and an output directory, it creates a runnable GNU Radio Python application
in the output directory.

In GRC Agent evidence reports, `grcc` validation means the candidate `.grc`
flowgraph is passed through the same GNU Radio compiler path used to compile a
GRC file into generated Python. A successful compile is evidence that GNU Radio
accepted the graph structure, blocks, parameters, and connections at a high
level.