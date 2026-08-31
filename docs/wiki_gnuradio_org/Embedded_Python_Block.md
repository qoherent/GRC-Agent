# Embedded Python Block

An Embedded Python Block lets a GRC user create a custom GNU Radio block in
Python inside a flowgraph without first creating and installing an out-of-tree
module. The block is instantiated by GRC from Python code and can define ports,
parameters, and a work function like a normal GNU Radio Python block.

How it works: GRC interprets the Python source you write in the block's editor
through `gnuradio.grc.core.utils.epy_block_io.extract()`. That function finds
the class defined in the source (it must subclass a GNU Radio gateway block
such as `gr.sync_block`), requires every `__init__` argument to have a default
value, and instantiates the class once. Each `__init__` argument becomes a
block parameter, with the argument default as the parameter's default; the
block's stream ports come from the instance's `in_sig()`/`out_sig()`
declarations (numpy dtype names mapped to GRC port types, including vector
lengths), message ports come from `message_ports_in()`/`message_ports_out()`,
the class docstring becomes the block documentation, and constructor arguments
backed by settable attributes become callbacks. Editing the code and
refreshing re-runs this extraction — the Python source is the single source of
truth for the block's interface, which is why the block's ports and parameters
change when its source changes.

The corresponding block id in the platform library is `epy_block`. Exported
hier-block library versions and saved example blocks are ordinary standalone
catalog blocks; the embedded instance itself always runs the code stored in
the flowgraph.

Source: [Embedded Python Block](https://wiki.gnuradio.org/index.php/Embedded_Python_Block) on the official GNU Radio Wiki; introspection behavior read from the installed `gnuradio.grc.core.utils.epy_block_io` module (`extract()` / `_ports()` / `_find_block_class()`).
