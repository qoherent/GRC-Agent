# grcc

`grcc` is the GNU Radio Companion Compiler. It compiles a GNU Radio Companion
`.grc` flowgraph without launching the graphical interface. Given an input GRC
file and an output directory, it creates a runnable GNU Radio Python application
in the output directory.

In GRC Agent evidence reports, `grcc` validation means the candidate `.grc`
flowgraph is passed through the same GNU Radio compiler path used to compile a
GRC file into generated Python. A successful compile is evidence that GNU Radio
accepted the graph structure, blocks, parameters, and connections at a high
level. It is not docs/RAG mutation authority and it does not authorize edits by
itself.

Provenance: Source title: `grcc` manual page, GNU Radio Companion Compiler.
Source URL: https://github.com/gnuradio/gnuradio/blob/main/grc/scripts/grcc.
Retrieval topic: grcc compile validation flowgraph. Aliases: grcc, GNU Radio
Companion Compiler, GRC compiler, compile flowgraph, validation. Official or
primary: primary local GNU Radio CLI documentation from
`/usr/share/man/man1/grcc.1.gz`, cross-referenced with the GNU Radio source
tree script URL above. Why relevant: this snippet grounds docs QA rows about
what `grcc` does and why GRC Agent treats it as compile/validation evidence.
