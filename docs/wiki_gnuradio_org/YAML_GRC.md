# YAML GRC
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=YAML_GRC#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=YAML_GRC#searchInput)
Starting with release 3.8, YAML replaces XML as the file format for GNU Radio Companion (for 3.7 see [XML GRC](https://wiki.gnuradio.org/index.php?title=XML_GRC "XML GRC")). This is triggered by switching from Cheetah to Mako as the templating engine, since Cheetah does not support Python 3. Specifically, this will impact .grc files, block descriptions and block tree files. This article won’t focus on the .grc files, because they aren’t meant for manual editing. 
The most notable change is of course the absence of XML’s angle brackets in favour of YAML’s colon-separated keys and values, and the change in file names for blocks. The latter is important for GRC to recognise the file. Namely, the “.xml” ending has been replaced with “.block.yml” for block descriptions and the underscore in block tree files has been replaced with a dot. (For example, “qtgui_tree.xml” becomes “qtgui.tree.yml”) 
## Contents
  * [1 Block Descriptions](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Block_Descriptions)
    * [1.1 ID](https://wiki.gnuradio.org/index.php?title=YAML_GRC#ID)
    * [1.2 Label](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Label)
    * [1.3 Flags (optional)](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Flags_\(optional\))
    * [1.4 Category (optional)](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Category_\(optional\))
    * [1.5 Parameters](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Parameters)
    * [1.6 Inputs and Outputs (optional)](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Inputs_and_Outputs_\(optional\))
    * [1.7 Asserts (optional)](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Asserts_\(optional\))
    * [1.8 Templates](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Templates)
    * [1.9 Documentation](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Documentation)
    * [1.10 File Format](https://wiki.gnuradio.org/index.php?title=YAML_GRC#File_Format)
    * [1.11 Others](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Others)
      * [1.11.1 Value](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Value)
      * [1.11.2 Variable Make](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Variable_Make)
      * [1.11.3 Variable Value](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Variable_Value)
  * [2 Block Tree Files](https://wiki.gnuradio.org/index.php?title=YAML_GRC#Block_Tree_Files)


## Block Descriptions
The content of the block descriptions is still the same, although it has been shuffled around a bit. The parts are elaborated below, in the order they should appear in the files. 
### ID

```
id: blocks_multiply_const_vxx
label: Multiply Const

parameters:
-   id: type
    label: IO Type
(...)

```

The ID is unique for each block and is used to identify it. 
### Label

```
id: blocks_multiply_const_vxx
label: Multiply Const

parameters:
-   id: type
    label: IO Type
(...)

```

The label is simply the human-readable name of the block, and will be visible from within GRC. It will not appear in the generated code. 
### Flags (optional)

```
id: blocks_throttle
label: Throttle
flags: throttle

parameters:
-   id: type
    label: Type
(...)

```

The flags indicate special attributes of the block. The only current example of this is the throttle flag, which is used in the Throttle and hardware blocks. For more information on throttling, see the Guided Tutorial: [[1]](https://wiki.gnuradio.org/index.php/Guided_Tutorial_GRC#2.4.3._A_Note_on_the_Throttle_Block)
### Category (optional)

```
id: sample_block
label: Sample Block
category: '[Level 1 Category]/Level 2 Category'

parameters:
-   id: type
    label: Type
(...)

```

In the GRC Block Tree Panel, blocks are organized into categories (such as "Core"), which are then organized by further levels of categories. The category field can be used to define how your block will be stored in the Block Tree and allows for better organization. 
### Parameters

```
parameters:
-   id: tr_chan
    label: Trigger Channel
    category: Trigger
    dtype: int
    default: '0'
    hide: part
-   id: tr_type
    label: Trigger Type
    category: Trigger
    dtype: enum
    options: [TRIGGER_FREE, TRIGGER_AUTO]
    option_labels: ["Free", "Auto"]

```

This part describes the parameters to display to the user. A number of keywords is used there:  

id
    A unique name for the parameter, it is not displayed to the user but can be used to reference the parameter inside this file: ${<id>} 

label
    Human readable name to display to the user. 

dtype
     [Type](https://wiki.gnuradio.org/index.php?title=Datatypes "Datatypes") of the data handled by the parameter     This can have many values  

Numbers
    raw, complex, real, float, int, short, hex, bool 

Vectors of numbers
    complex_vector, real_vector, float_vector, int_vector 

Strings
    string, file_open, file_save, _multiline _mutiline_python_external 

Other special types
    gui_hint, import, id, stream_id, name and enum 

default
    A default value for the parameter 

category
    Used to organise a large number of parameters. If set, a new tab will be created and named as the value of the keyword with the parameter inside. 

hide
    If the value is part, the parameter only be shown in the properties window.     If it's all, it will never be shown, even inside the properties window.     If it's none, it will be shown in both the properties window and on the visual block component. 

base_key
    Inherit properties from another parameter. The value given is the id of that parameter.
### Inputs and Outputs (optional)

```
id: qtgui_freq_sink_x
(...)
    default: '1.0'
    hide: ${ ('part' if int(nconnections) >= 10 else 'all') }

inputs:
-   domain: stream
    dtype: ${ type.t }
    multiplicity: ${ (0 if (type == 'msg_complex' or type == 'msg_float') else nconnections) }
    optional: true
-   domain: message
    id: freq
    optional: true
    hide: ${ showports }

outputs:
-   domain: message
(...)

```

This describes the input ports. **domain** can be either **stream** or **message**. Stream ports need a type, which usually is specified as a parameter. This is true for our example, the type is specified in **type.t**. The **multiplicity** tells us how many "copies" of this port we want. (Yes, this can be zero!) Finally, the **optional** flag tells us whether this port _must_ be connected or not. (GRC won't generate the flowgraph if a non-optional port isn't connected) 
Message ports[[2]](https://wiki.gnuradio.org/index.php/Guided_Tutorial_Programming_Topics#5.3_Message_Passing) don't have a specified type here, but they have IDs. This message port can also be hidden, using the "Show message ports" option in the parameters. 
  
The output ports work similarly. 
### Asserts (optional)

```
id: blocks_throttle
(...)    
    dtype: ${ type }
    vlen: ${ vlen }

asserts:
- ${ vlen > 0 }

templates:
    imports: from gnuradio import blocks
    make: blocks.throttle(${type.size}*${vlen}, ${samples_per_second},${ignoretag})
(...)

```

Asserts (previously known as "checks" for the XML blocks) are expressions that _need_ to be true, otherwise GRC won't let you generate the flowgraph. Asserts are Python statements that should eval() to 'True' when correct, wrapped with Mako template designators '${' and '}'. 
### Templates

```
id: blocks_message_strobe_random
(...)
    optional: true

templates:
    imports: |-
        from gnuradio import blocks
        import pmt
    make: blocks.message_strobe_random(${msg}, ${dist}, ${mean}, ${std})
    callbacks:
    - set_msg(${msg})
    - set_dist(${dist})
(...)

```

The templates describe the code that is created when GRC generates the flowgraph. This part consists of the imports, the make/initialization statements and the callbacks. The values of these keys often happen to span over multiple lines, in which case you're likely to see the "|-" symbols. This is YAML syntax for a literal block scalar[[3]](http://yaml.org/spec/current.html#literal%20style/syntax) and the hyphen means that the line break at the end is omitted. 
  
Your block probably utilizes parts of GNU Radio or other modules that need to be imported. These are specified as _imports_. 
  
_make_ holds the initialization code, and often depends on several of the parameters. Some of the more involved blocks may also use the "%" symbol, denoting the use of a YAML directive[[4]](http://yaml.org/spec/current.html#directive/syntax). This can occur both in _imports_ and _make_. The snippet below may be helpful: 

```
id: rational_resampler_xxx
(...)
    make: |-
        filter.rational_resampler_${type}(
            interpolation=${interp},
            decimation=${decim},
        % if taps:
            taps=${taps},
        % else:
            taps=None,
        % endif

```

Please note that the "${}" is not valid syntax in a directive. 
### Documentation

```
id: variable_rrc_filter_taps
(...)
documentation: |-
    This is a convenience wrapper for calling firdes.root_raised_cosine(...).

file_format: 1

```

_documentation_ simply contains information about the block. This information is displayed in the block's Documentation tab in GRC. 
### File Format
Specifies the version of the GRC yml format used in the file. Should not be changed. 
### Others
Most GRC blocks correspond to GNU Radio blocks. However, there are also GRC blocks that are used as variables. The obvious one is the Variable block, but there are others like Parameter and Constellation Object. Their file formats are slightly different. Typically, they start with id, label, flags, category, and parameters. However, they also need a value and var_make. They do not need input or outputs. 
#### Value

```
parameters:
-   id: value
    label: Value
    dtype: raw
    default: '0'

value: ${value}

```

This sets the value of the variable-type block so other GRC blocks can evaluate it, typically in that other block's parameter. For many variable-type blocks, there is already a parameter defined as "value" and therefore the block's value can be set to ${value} as shown in the example above. 
For more sophisticated blocks, the value may be a construction of the desired object. For example, the Constellation Rect. Object has the following: 

```
id: variable_constellation_rect
(...)
value: ${ digital.constellation_rect(const_points, sym_map, rot_sym, real_sect, imag_sect,
    w_real_sect, w_imag_sect) }
(...)

```

#### Variable Make

```
(...)
parameters:
-   id: value
    label: Value
    dtype: raw
    default: '0'

value: {value}
templates:
    var_make: self.${id} = ${id} = ${value}

```

The _var_make_ holds the initialization code, and often requires that the variable be referenced by its ID and also as an attribute to self, which refers to the top_block. The example above shows the most common pattern, where there is a parameter with id "value", then "value:" is set to the corresponding parameter, and the var_make is also set to ${value}, referenced by the block's ID. 
#### Variable Value
## Block Tree Files
The block tree files are fairly straightforward, and tells GRC how to divide the block tree into categories. The following example snippet from gr-digital (which is part of core GNU Radio) describes two categories, _Coding_ and _Equalizers_. The blocks are specified using their _id_ s, which should equal their file names without ".block.yml". 

```
'[Core]':
- Coding:
  - digital_additive_scrambler_bb
  - digital_descrambler_bb
  - digital_scrambler_bb
- Equalizers:
  - digital_cma_equalizer_cc
  - digital_lms_dd_equalizer_cc
(...)

```

Retrieved from "[https://wiki.gnuradio.org/index.php?title=YAML_GRC&oldid=15436](https://wiki.gnuradio.org/index.php?title=YAML_GRC&oldid=15436)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [3.8](https://wiki.gnuradio.org/index.php?title=Category:3.8 "Category:3.8")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=YAML+GRC "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=YAML_GRC "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:YAML_GRC&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=YAML_GRC)
  * [View source](https://wiki.gnuradio.org/index.php?title=YAML_GRC&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=YAML_GRC&action=history "Past revisions of this page \[alt-shift-h\]")


More
### Search
[](https://wiki.gnuradio.org/index.php?title=Main_Page "Visit the main page")
###  Navigation
  * [Wiki Home](https://wiki.gnuradio.org/index.php?title=Main_Page)
  * [GNU Radio Website](https://gnuradio.org)
  * [FAQ](https://wiki.gnuradio.org/index.php?title=FAQ)
  * [Get a Wiki Account](https://wiki.gnuradio.org/index.php?title=Wiki_account)


###  Guides
  * [Tutorials](https://wiki.gnuradio.org/index.php?title=Tutorials)
  * [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR)
  * [Contributing](https://wiki.gnuradio.org/index.php?title=Development)


###  Wiki Tools
  * [Recent changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChanges "A list of recent changes in the wiki \[alt-shift-r\]")
  * [Random page](https://wiki.gnuradio.org/index.php?title=Special:Random "Load a random page \[alt-shift-x\]")
  * [Help](https://www.mediawiki.org/wiki/Special:MyLanguage/Help:Contents "The place to find out")


###  Tools
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/YAML_GRC "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/YAML_GRC "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=YAML_GRC&oldid=15436 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=YAML_GRC&action=info "More information about this page")


  * This page was last edited on 7 November 2025, at 22:44.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


