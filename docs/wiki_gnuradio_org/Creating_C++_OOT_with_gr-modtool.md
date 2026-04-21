# Creating C++ OOT with gr-modtool
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#searchInput)
This tutorial describes how to create a custom C++ block and use it in a flowgraph: 
  * Create a new C++ block using _gr_modtool_
  * Modify the C++ .h and .cc code so the block will function
  * Modify the YAML file so it can be read in GRC
  * Install and run the block in a flowgraph


An Out-Of-Tree (OOT) module is a GNU Radio component that does not live within the GNU Radio source tree. The tree is the group of blocks already provided by GNU Radio. Thus, an OOT block is a custom block created to extend GNU Radio with specific functions desired. OOT blocks allow you to maintain the code yourself and have additional functionality alongside the main code. Their functionality can be defined in Python or in C++. Their configuration is described via a yaml file. 
The previous tutorial, [Creating Python OOT with gr-modtool](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool "Creating Python OOT with gr-modtool"), describes how to create a Python block in an OOT module. This C++ OOT tutorial builds upon the previous Python one, so it is is suggested to at least complete the _Creating an OOT Module_ portion of that tutorial before completing this one. 
## Contents
  * [1 Installation Note](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#Installation_Note)
  * [2 Creating an OOT Block](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#Creating_an_OOT_Block)
  * [3 Modifying the C++ impl.h Header](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#Modifying_the_C++_impl.h_Header)
  * [4 Modifying the C++ impl.cc File](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#Modifying_the_C++_impl.cc_File)
  * [5 Modifying the YAML .yml File](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#Modifying_the_YAML_.yml_File)
  * [6 Compiling and Installing the Block](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#Compiling_and_Installing_the_Block)
  * [7 Using the Custom Block in a Flowgraph](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#Using_the_Custom_Block_in_a_Flowgraph)
  * [8 Running the Flowgraph](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#Running_the_Flowgraph)
  * [9 Making Changes](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#Making_Changes)


## Installation Note
This tutorial was written using GNU Radio v3.10.1.1 on Ubuntu 21.10, installed using the Ubuntu PPA from the [Installation Wiki Page](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR"). The basic GNU Radio install using: 

```
$ sudo apt-get install gnuradio
```

does not come with the proper libraries needed to compile and install OOT modules. Consider installing the following packages before continuing: 

```
$ sudo apt-get install gnuradio-dev cmake libspdlog-dev clang-format
```

## Creating an OOT Block
Move to the _gr-customModule_ directory created in the [Creating Python OOT with gr-modtool](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool "Creating Python OOT with gr-modtool") tutorial: 

```
cd your-path/gr-customModule
```

Add a new block named _multDivSelect_ : 

```
$ gr_modtool add multDivSelect
```

The types of blocks will be displayed: 

```
GNU Radio module name identified: customModule
('sink', 'source', 'sync', 'decimator', 'interpolator', 'general', 'tagged_stream', 'hier', 'noblock')
```

Enter _sync_ as the block type, because the block we're making will produce the same number of output items on the output port for each item it consumes from the input port. See [Types of Blocks](https://wiki.gnuradio.org/index.php?title=Types_of_Blocks "Types of Blocks") for more info on different block types that are available. 

```
Enter block type: sync
```

Enter _cpp_ as the language: 

```
Language (python/cpp): cpp
Language: C++
Block/code identifier: multDivSelect
```

Enter the name or organization of the copyright holder: 

```
Please specify the copyright holder: YourName
```

Our OOT block allows the _gnuradio-companion_ user to specify a selector value, which at the C++ level is a boolean (true/false) value. To enable this, we need to enter the C++ expression shown below that declares the _selector_ variable with a default value of _true_ as an argument to our OOT block: 

```
Enter valid argument list, including default arguments: 
bool selector=true
```

Select whether or not QA code is desired: 

```
Add Python QA code? [Y/n] n
Add C++ QA code? [Y/n] n
```

Multiple files will then be created or modified: 

```
Adding file 'lib/multDivSelect_impl.h'...
Adding file 'lib/multDivSelect_impl.cc'...
Adding file 'include/gnuradio/customModule/multDivSelect.h'...
Adding file 'python/customModule/bindings/docstrings/multDivSelect_pydoc_template.h'...
Adding file 'python/customModule/bindings/multDivSelect_python.cc'...
Adding file 'grc/customModule_multDivSelect.block.yml'...
Editing grc/CMakeLists.txt...
```

## Modifying the C++ impl.h Header
Many of the files are automatically generated wrapper code that do not need to be modified. However, the _multDivSelect_impl.h_ and _multDivSelect_impl.cc_ files defines the operation of the block and must be modified. Open the file with a text editor: 

```
$ gedit lib/multDivSelect_impl.h &
```

The following code will be displayed: 

```
/* -*- c++ -*- */
/*
 * Copyright 2022 YourName.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef INCLUDED_CUSTOMMODULE_MULTDIVSELECT_IMPL_H
#define INCLUDED_CUSTOMMODULE_MULTDIVSELECT_IMPL_H

#include <gnuradio/customModule/multDivSelect.h>

namespace gr {
namespace customModule {

class multDivSelect_impl : public multDivSelect
{
private:
    // Nothing to declare in this block.

public:
    multDivSelect_impl(bool selector);
    ~multDivSelect_impl();

    // Where all the action really happens
    int work(int noutput_items,
             gr_vector_const_void_star& input_items,
             gr_vector_void_star& output_items);
};

} // namespace customModule
} // namespace gr

#endif /* INCLUDED_CUSTOMMODULE_MULTDIVSELECT_IMPL_H */

```

Create a boolean private member __selector_ which will hold the value of the _selector_ parameter: 

```
class multDivSelect_impl : public multDivSelect
{
private:
    bool _selector;

```

Press CTRL + S to save the file. 
## Modifying the C++ impl.cc File
The _.cc_ file needs to be modified to define the desired operation of the block. Open the file with a text editor: 

```
$ gedit lib/multDivSelect_impl.cc &
```

The code will be as displayed: 

```
/* -*- C++ -*- */
/*
 * Copyright 2022 YourName.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#include "multDivSelect_impl.h"
#include <gnuradio/io_signature.h>

namespace gr {
namespace customModule {

#pragma message("set the following appropriately and remove this warning")
using input_type = float;
#pragma message("set the following appropriately and remove this warning")
using output_type = float;
multDivSelect::sptr multDivSelect::make(bool selector)
{
    return gnuradio::make_block_sptr<multDivSelect_impl>(selector);
}


/*
 * The private constructor
 */
multDivSelect_impl::multDivSelect_impl(bool selector)
    : gr::sync_block("multDivSelect",
                     gr::io_signature::make(
                         1 /* min inputs */, 1 /* max inputs */, sizeof(input_type)),
                     gr::io_signature::make(
                         1 /* min outputs */, 1 /*max outputs */, sizeof(output_type)))
{
}

/*
 * Our virtual destructor.
 */
multDivSelect_impl::~multDivSelect_impl() {}

int multDivSelect_impl::work(int noutput_items,
                               gr_vector_const_void_star& input_items,
                               gr_vector_void_star& output_items)
{
    auto in = static_cast<const input_type*>(input_items[0]);
    auto out = static_cast<output_type*>(output_items[0]);

#pragma message("Implement the signal processing in your block and remove this warning")
    // Do <+signal processing+>

    // Tell runtime system how many output items we produced.
    return noutput_items;
}

} /* namespace customModule */
} /* namespace gr */

```

Remove the _pragma_ messages and define the input and output type to be _gr_complex_ : 

```
using input_type = gr_complex;
using output_type = gr_complex;

```

Update to two inputs and store the value of the _selector_ parameter using the private member __selector_ as defined in _multDivSelector_impl.h_ : 

```
/*
 * The private constructor
 */
multDivSelect_impl::multDivSelect_impl(bool selector)
    : gr::sync_block("multDivSelect",
                     gr::io_signature::make(
                         2 /* min inputs */, 2 /* max inputs */, sizeof(input_type)),
                     gr::io_signature::make(
                         1 /* min outputs */, 1 /*max outputs */, sizeof(output_type)))
{
    _selector = selector;
}

```

Modify the _work()_ function by removing the _pragma_ message, defining the variables _in0_ and _in1_ corresponding to the two input ports, and multiply the two inputs if __selector_ is true and divide them if __selector_ is false: 

```
int multDivSelect_impl::work(int noutput_items,
                               gr_vector_const_void_star& input_items,
                               gr_vector_void_star& output_items)
{
    auto in0 = static_cast<const input_type*>(input_items[0]);
    auto in1 = static_cast<const input_type*>(input_items[1]);
    auto out = static_cast<output_type*>(output_items[0]);

    for (int index = 0; index < noutput_items; index++) {
        if (_selector) { out[index] = in0[index] * in1[index]; }
        else{ out[index] = in0[index] / in1[index]; }
    }


    // Tell runtime system how many output items we produced.
    return noutput_items;
}

```

Press CTRL + S to save the file. 
## Modifying the YAML .yml File
_gnuradio-companion_ uses files in the YAML (_yet another markup language_) format to learn about our OOT block and how to call it. More information about this kind of file may be found in the [YAML GRC](https://wiki.gnuradio.org/index.php?title=YAML_GRC "YAML GRC") page. 
Open our block's YAML file using a text editor: 

```
$ gedit grc/customModule_multDivSelect.block.yml &
```

The code will be displayed: 

```
id: customModule_multDivSelect
label: multDivSelect
category: '[customModule]'

templates:
  imports: from gnuradio import customModule
  make: customModule.multDivSelect(${selector})

#  Make one 'parameters' list entry for every parameter you want settable from the GUI.
#     Keys include:
#     * id (makes the value accessible as keyname, e.g. in the make entry)
#     * label (label shown in the GUI)
#     * dtype (e.g. int, float, complex, byte, short, xxx_vector, ...)
#     * default
parameters:
- id: parametername_replace_me
  label: FIX ME:
  dtype: string
  default: You need to fill in your grc/customModule_multDivSelect.block.yaml
#- id: ...
#  label: ...
#  dtype: ...

#  Make one 'inputs' list entry per input and one 'outputs' list entry per output.
#  Keys include:
#      * label (an identifier for the GUI)
#      * domain (optional - stream or message. Default is stream)
#      * dtype (e.g. int, float, complex, byte, short, xxx_vector, ...)
#      * vlen (optional - data stream vector length. Default is 1)
#      * optional (optional - set to 1 for optional inputs. Default is 0)
inputs:
#- label: ...
#  domain: ...
#  dtype: ...
#  vlen: ...
#  optional: ...

outputs:
#- label: ...
#  domain: ...
#  dtype: ...
#  vlen: ...
#  optional: ...

#  'file_format' specifies the version of the GRC yml format used in the file
#  and should usually not be changed.
file_format: 1

```

Update the parameter definition with the information for _selector_ : 

```
parameters:
- id: selector
  label: Selector, Multiply (true) or Divide (false)
  dtype: bool
  default: true

```

Update the input port and output port definitions: 

```
inputs:
- label: in0
  domain: stream
  dtype: complex
- label: in1
  domain: stream
  dtype: complex
outputs:
- label: out0
  domain: stream
  dtype: complex

```

Press CTRL + S to save the file. 
## Compiling and Installing the Block
The block needs to be compiled and installed. Ensure you are in the _gr-customModule_ directory: 

```
$ cd your-path/gr-customModule
```

If the _build/_ directory already exists, remove it: 

```
rm -rf build/
```

Create the build directory: 

```
$ mkdir build
```

Move into the build directory: 

```
cd build
```

Run cmake to build the makefiles: 

```
cmake ..
```

Compile the module: 

```
make
```

Install the module: 

```
sudo make install
```

The new files will then be installed: 

```
-- Install configuration: "Release"
-- Up-to-date: /usr/local/lib/cmake/gnuradio-customModule/gnuradio-customModuleConfig.cmake
-- Up-to-date: /usr/local/include/gnuradio/customModule/api.h
-- Installing: /usr/local/include/gnuradio/customModule/multDivSelect.h
-- Installing: /usr/local/lib/x86_64-linux-gnu/libgnuradio-customModule.so.1.0.0.0
-- Installing: /usr/local/lib/x86_64-linux-gnu/libgnuradio-customModule.so.1.0.0
-- Installing: /usr/local/lib/x86_64-linux-gnu/libgnuradio-customModule.so
-- Installing: /usr/local/lib/cmake/gnuradio-customModule/gnuradio-customModuleTargets.cmake
-- Installing: /usr/local/lib/cmake/gnuradio-customModule/gnuradio-customModuleTargets-release.cmake
-- Installing: /usr/local/lib/cmake/gnuradio-customModule/gnuradio-customModuleConfig.cmake
-- Up-to-date: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/__init__.py
-- Up-to-date: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/addSubSelect.py
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/__init__.pyc
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/addSubSelect.pyc
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/__init__.pyo
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/addSubSelect.pyo
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/customModule_python.cpython-39-x86_64-linux-gnu.so
-- Set runtime path of "/usr/local/lib/python3.9/dist-packages/gnuradio/customModule/customModule_python.cpython-39-x86_64-linux-gnu.so" to ""
-- Up-to-date: /usr/local/share/gnuradio/grc/blocks/customModule_addSubSelect.block.yml
-- Installing: /usr/local/share/gnuradio/grc/blocks/customModule_multDivSelect.block.yml
```

Some of the files are listed as _Up-to-date_ because they correspond to the _addSubSelect_ block created in the [Creating Python OOT with gr-modtool](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool "Creating Python OOT with gr-modtool") tutorial. All of the files corresponding to _multDivSelect_ are now installed to _/usr/local/_. 
Run _ldconfig_ to update the linking for the customModule library: 

```
$ sudo ldconfig 
```

## Using the Custom Block in a Flowgraph
If GRC is already running, press the _Reload_ button to refresh the list of blocks in the library: 
[![](https://wiki.gnuradio.org/images/thumb/4/41/ReloadBlockLibrary.png/800px-ReloadBlockLibrary.png)](https://wiki.gnuradio.org/index.php?title=File:ReloadBlockLibrary.png)
Otherwise, start GRC from the command line: 

```
$ gnuradio-companion &
```

The _multDivBlock_ can now be seen under the _customModule_ tab. The _addSubSelect_ is a Python block created in the [Creating Python OOT with gr-modtool](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool "Creating Python OOT with gr-modtool") tutorial. 
[![](https://wiki.gnuradio.org/images/thumb/6/62/MultDivBlockLibrary.png/800px-MultDivBlockLibrary.png)](https://wiki.gnuradio.org/index.php?title=File:MultDivBlockLibrary.png)
Drag the _multDivSelect_ block into the flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/3/32/AddMultDivToWorkspace.png/800px-AddMultDivToWorkspace.png)](https://wiki.gnuradio.org/index.php?title=File:AddMultDivToWorkspace.png)
Now drag in the following blocks and update their properties: 
  * Signal Source 
    * Frequency: 100
  * Constant Source 
    * Constant: 2
  * Throttle
  * QT GUI Time Sink 
    * Autoscale: Yes


Connect the flowgraph accordingly: 
[![](https://wiki.gnuradio.org/images/thumb/7/79/MultDivFlowgraph.png/800px-MultDivFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:MultDivFlowgraph.png)
## Running the Flowgraph
Run the flowgraph. The _QT GUI Time Sink_ will display the following output. Notice that the amplitude of the sinusoid is 2, due to the multiplication by 2 in the _multDivSelect_ block. 
[![](https://wiki.gnuradio.org/images/thumb/b/bf/MultDivMultOutput.png/700px-MultDivMultOutput.png)](https://wiki.gnuradio.org/index.php?title=File:MultDivMultOutput.png)
Edit the properties of the _multDivSelect_ block and enter _False_ for the _selector_ : 
[![](https://wiki.gnuradio.org/images/thumb/0/08/MultDivProperties.png/500px-MultDivProperties.png)](https://wiki.gnuradio.org/index.php?title=File:MultDivProperties.png)
Click _OK_ to save the property. 
Run the flowgraph. The _QT GUI Time Sink_ will display the following output. Notice that the amplitude of the sinusoid is 0.5, due to the division by 2 in the _multDivSelect_ block. 
[![](https://wiki.gnuradio.org/images/thumb/4/4c/MultDiv_DivOutput.png/700px-MultDiv_DivOutput.png)](https://wiki.gnuradio.org/index.php?title=File:MultDiv_DivOutput.png)
## Making Changes
It is suggested to recompile and reinstall the module any time a change is made, followed by reloading the block library in GRC. This includes changes such as: 
  * Number of parameters
  * Type of parameters
  * Number of input ports or output ports
  * Types of input ports or output ports
  * Modifying the YAML .yml file
  * Modifying any C++ .h or .cc files


Removing and re-creating the _build/_ directory may be necessary before recompiling and reinstalling the module depending on the scope of the change: 

```
$ rm -rf gr-customModule/build
$ mkdir gr-customModule/build
```

The previous tutorial, [Creating Python OOT with gr-modtool](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool "Creating Python OOT with gr-modtool"), describes how to build a custom Python OOT module. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool&oldid=14269](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool&oldid=14269)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Creating+C%2B%2B+OOT+with+gr-modtool "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Creating_C%2B%2B_OOT_with_gr-modtool&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool)
  * [View source](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Creating_C%2B%2B_OOT_with_gr-modtool "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Creating_C%2B%2B_OOT_with_gr-modtool "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool&oldid=14269 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool&action=info "More information about this page")


  * This page was last edited on 18 May 2024, at 15:13.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


