# Creating Python OOT with gr-modtool
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#searchInput)
This tutorial describes how to create a custom Python block or Out-of-Tree (OOT) module and use it in a flowgraph with the following steps: 
  * Create an out-of-tree module using _gr_modtool_
  * Create a new Python block using _gr_modtool_
  * Modify the Python code in a text editor so the block will function
  * Modify the YAML file so it can be displayed in Gnuradio Companion (GRC)
  * Install and run the block in a flowgraph


An Out-Of-Tree (OOT) module is a GNU Radio component that does not live within the GNU Radio source tree. The tree is the group of blocks already provided by GNU Radio. Thus, an OOT block is a custom block created to extend GNU Radio with specific functions desired. OOT blocks allow you to maintain the code yourself and have additional functionality alongside the main code. Their functionality can be defined in Python or in C++. Their configuration is described via a yaml file. 
A tutorial on using the _Embedded Python Block_ in GRC is on the [Creating Your First Block](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block "Creating Your First Block") page. There are also additional tutorials [on using tags](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing "Python Block Message Passing"), how to do [message passing](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing "Python Block Message Passing") and [adding vector inputs and outputs](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors "Python Block with Vectors") in Python blocks that will work for both the _Embedded Python Block_ and OOT Python blocks. 
The next tutorial, [Creating C++ OOT with gr-modtool](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool "Creating C++ OOT with gr-modtool"), describes how to build a custom C++ OOT module. The C++ OOT tutorial builds upon this Python one, so it is is suggested to at least complete the _Creating an OOT Module_ portion before moving on. 
## Contents
  * [1 Installation Note](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Installation_Note)
  * [2 Creating an OOT Module](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Creating_an_OOT_Module)
  * [3 Creating an OOT Block](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Creating_an_OOT_Block)
  * [4 Modifying the Python .py File](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Modifying_the_Python_.py_File)
  * [5 Modifying YAML .yml File](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Modifying_YAML_.yml_File)
  * [6 Compiling and Installing the Block](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Compiling_and_Installing_the_Block)
  * [7 Using the Custom Block in a Flowgraph](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Using_the_Custom_Block_in_a_Flowgraph)
  * [8 Running the Flowgraph](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Running_the_Flowgraph)
  * [9 Making Changes](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Making_Changes)
  * [10 Related Tutorial](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Related_Tutorial)
  * [11 Next Tutorial](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Next_Tutorial)


## Installation Note
This tutorial was written using GNU Radio v3.10.1.1 on Ubuntu 21.10, installed using the Ubuntu PPA from the [Installation Wiki Page](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR"). The basic GNU Radio install using: 

```
$ sudo apt-get install gnuradio
```

does not come with the proper libraries needed to compile and install OOT modules. Consider installing the following packages before continuing: 

```
$ sudo apt-get install gnuradio-dev cmake libspdlog-dev clang-format
```

## Creating an OOT Module
Open a terminal and navigate to an appropriate directory for writing software, such as the home directory: 

```
$ cd $HOME
```

GNU Radio comes packaged with _gr_modtool_ , a software tool used to create out-of-tree (OOT) modules. An OOT module can be thought of as a collection of custom GNU Radio blocks. Create an OOT module named _customModule_ using _gr_modtool_ : 

```
$ gr_modtool newmod customModule
```

The directory _gr-customModule_ is created which contains all of the skeleton code for an OOT module, however it does not yet have any blocks. Move into the _gr-customModule_ directory: 

```
$ cd gr-customModule
```

List all of the files and directories within the OOT module: 

```
$ ls
```

The directory listing will be as follows: 

```
apps/  cmake/  CMakeLists.txt  docs/  examples/  grc/  include/  lib/  MANIFEST.yml  python/
```

## Creating an OOT Block
Now a block needs to be created within gr-customModule. The custom block will either add or subtract based on an input parameter, so the block is named _addSubSelect_ : 

```
$ gr_modtool add addSubSelect
```

The command will start a questionnaire about how to the block is to be defined: what block type, language and parameters: 

```
GNU Radio module name identified: customModule
('sink', 'source', 'sync', 'decimator', 'interpolator', 'general', 'tagged_stream', 'hier', 'noblock')
```

Select the _sync_ block, which produces an output for every input: 

```
Enter block type: sync
```

Enter _python_ as the language: 

```
Language (python/cpp): python
Language: Python
Block/code identifier: addSubSelect
```

Enter the name or organization of the copyright holder: 

```
Please specify the copyright holder: YourName
```

Now enter the argument list: 

```
Enter valid argument list, including default arguments:
```

Enter the argument list as if writing the Python code directly. In this case the _selector_ will determine whether or not the block performs addition or subtraction. A default argument of _True_ is given: 

```
selector=True
```

Determine whether or not you want the Python quality assurance (QA) code: 

```
Add Python QA code? [Y/n] n
```

New files will be generated: 

```
Adding file 'python/customModule/addSubSelect.py'...
Adding file 'grc/customModule_addSubSelect.block.yml'...
Editing grc/CMakeLists.txt...
```

Two new files were created, _addSubSelect.py_ which defines the operation of the block and _customModule_addSubSelect.block.yml_ which defines the interface of the block for GNU Radio Companion (GRC). The _CMakeLists.txt_ file was modified so the two files will be installed when the module is compiled and installed. 
## Modifying the Python .py File
Open the python file with a text editor: 

```
python/customModule/addSubSelect.py 
```

The following code will be listed: ‎

```
#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2022 YourName.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


import numpy
from gnuradio import gr

class addSubSelect(gr.sync_block):
    """
    docstring for block addSubSelect
    """
    def __init__(self, selector=True):
        gr.sync_block.__init__(self,
            name="addSubSelect",
            in_sig=[<+numpy.float32+>, ],
            out_sig=[<+numpy.float32+>, ])


    def work(self, input_items, output_items):
        in0 = input_items[0]
        out = output_items[0]
        # <+signal processing here+>
        out[:] = in0
        return len(output_items[0])‎

```

Change the _import_ statement: 

```
import numpy as np

```

Both the ___init__()_ function and _work()_ function need to be modified. The ___init__()_ function is modified to define the input type. The _addSubSelect_ block will accept two complex inputs and produce a complex output, therefore the _in_sig_ and _out_sig_ parameters need to be changed. The _selector_ parameter also needs to be saved as a member variable: 

```
    def __init__(self, selector=True):
        gr.sync_block.__init__(self,
            name="addSubSelect",
            in_sig=[np.complex64,np.complex64],
            out_sig=[np.complex64])
        self.selector = selector

```

The _work()_ function is modified to either add or subtract the two inputs based on the _selector_ parameter: 

```
    def work(self, input_items, output_items):
        in0 = input_items[0]
        in1 = input_items[1]

        if (self.selector):
            output_items[0][:] = in0 + in1
        else:
            output_items[0][:] = in0 - in1

        return len(output_items[0])

```

Save the file. 
## Modifying YAML .yml File
Open the .yml file with a text editor: 

```
grc/customModule_addSubSelect.block.yml 
```

The following YAML is displayed: ‎

```
id: customModule_addSubSelect
label: addSubSelect
category: '[customModule]'

templates:
  imports: from gnuradio import customModule
  make: customModule.addSubSelect(${selector})

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
  default: You need to fill in your grc/customModule_addSubSelect.block.yaml
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
file_format: 1‎

```

The YAML file needs to be updated to match the _addSubSelector.py_ file that was just modified. There is a single parameter, _selector_. Enter the parameter values according to: 
‎

```
parameters:
- id: selector
  label: Add (True) or Subtract (False) Selector
  dtype: bool
  default: True‎

```

The two inputs need to be defined in the YAML: 
‎

```
inputs:
- label: in0
  domain: stream
  dtype: complex
- label: in1
  domain: stream
  dtype: complex
‎

```

The single output needs to be defined: 
‎

```
outputs:
- label: out0
  domain: stream
  dtype: complex
‎

```

Save the file. 
## Compiling and Installing the Block
In the top level directory of _gr_customModule_ , create a _build_ directory: 

```
$ mkdir build
```

The directory should look like the following: 

```
apps/  build/  cmake/  CMakeLists.txt  docs/  examples/  grc/  include/  lib/  MANIFEST.yml  python/
```

Move into the build directory, 

```
$ cd build
```

Then run CMake which will prepare the makefiles: 

```
cmake ..
```

Compile the module: 

```
$ make
```

Install the module with sudo: 

```
sudo make install
```

Multiple files will now be installed: 

```
[  0%] Built target pygen_apps_9a6dd
[  0%] Built target copy_module_for_tests
[100%] Built target pygen_python_customModule_f524f
Install the project...
-- Install configuration: "Release"
-- Installing: /usr/local/lib/cmake/gnuradio-customModule/gnuradio-customModuleConfig.cmake
-- Installing: /usr/local/include/gnuradio/customModule/api.h
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/__init__.py
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/addSubSelect.py
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/__init__.pyc
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/addSubSelect.pyc
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/__init__.pyo
-- Installing: /usr/local/lib/python3.9/dist-packages/gnuradio/customModule/addSubSelect.pyo
-- Installing: /usr/local/share/gnuradio/grc/blocks/customModule_addSubSelect.block.yml
```

Run _ldconfig_ to update the linking for the customModule library: 

```
$ sudo ldconfig 
```

## Using the Custom Block in a Flowgraph
Start GNU Radio Companion (GRC): 

```
$ gnuradio-companion &
```

The _addSubSelect_ block will be available under the _customModule_ drop down in the block library: 
[![](https://wiki.gnuradio.org/images/thumb/7/7c/AddSubSelectInGRC.png/900px-AddSubSelectInGRC.png)](https://wiki.gnuradio.org/index.php?title=File:AddSubSelectInGRC.png)
Drag the block into the workspace: 
[![](https://wiki.gnuradio.org/images/thumb/6/66/DragInAddSubSelectBlock.png/900px-DragInAddSubSelectBlock.png)](https://wiki.gnuradio.org/index.php?title=File:DragInAddSubSelectBlock.png)
The block shows the properties as defined in the YAML file: 
  * Two complex inputs, _in0_ and _in1_
  * One complex output, _out0_
  * A selector parameter


Double-click the block to open the properties. The _selector_ parameter is described by the label _Add (True) or Subtract (False) Selector_ with _True_ as the default value: 
[![](https://wiki.gnuradio.org/images/thumb/8/8e/AddSubSelectProperties.png/500px-AddSubSelectProperties.png)](https://wiki.gnuradio.org/index.php?title=File:AddSubSelectProperties.png)
Click OK. 
Add the following blocks to the workspace: 
  * Signal Source
  * Constant Source
  * Throttle
  * QT GUI Time Sink


Update the following parameters 
  * Signal Source 
    * Frequency: 100
    * Amplitude: 0.5
  * Constant Source 
    * Constant: 0.5+0.5j


Connect the blocks according to the following flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/8/84/ConnectAddSubSelectFlowgraph.png/800px-ConnectAddSubSelectFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:ConnectAddSubSelectFlowgraph.png)
## Running the Flowgraph
Run the flowgraph. The _QT GUI Time Sink_ will display the following output, showing the average level of the sinusoid has been raised by _0.5+0.5j_ : 
[![](https://wiki.gnuradio.org/images/thumb/f/fd/AddSubSelectPositiveOffset.png/700px-AddSubSelectPositiveOffset.png)](https://wiki.gnuradio.org/index.php?title=File:AddSubSelectPositiveOffset.png)
Edit the properties off the _addSubSelect_ block and enter False which enables the subtraction mode: 
[![](https://wiki.gnuradio.org/images/thumb/1/1e/AddSubSelectFalse.png/500px-AddSubSelectFalse.png)](https://wiki.gnuradio.org/index.php?title=File:AddSubSelectFalse.png)
Run the flowgraph. The _QT GUI Time Sink_ will display the following output, showing the average level of the sinusoid has been reduced by _0.5+0.5j_ : 
[![](https://wiki.gnuradio.org/images/thumb/e/e2/AddSubSelectNegativeOffset.png/700px-AddSubSelectNegativeOffset.png)](https://wiki.gnuradio.org/index.php?title=File:AddSubSelectNegativeOffset.png)
## Making Changes
It is suggested to recompile and reinstall the module any time a change is made, followed by reloading the block library in GRC. This includes changes such as: 
  * Number of parameters
  * Type of parameters
  * Number of input ports or output ports
  * Types of input ports or output ports
  * Modifying the YAML .yml file
  * Modifying the Python .py file


Removing and re-creating the _build/_ directory may be necessary before recompiling and reinstalling the module depending on the scope of the change: 

```
$ rm -rf gr-customModule/build
$ mkdir gr-customModule/build
```

## Related Tutorial
The tutorial [Python Block with Vectors](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors "Python Block with Vectors") describes how to work with vectors in a Python block. 
## Next Tutorial
The next tutorial, [Creating C++ OOT with gr-modtool](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool "Creating C++ OOT with gr-modtool"), describes how to build a custom C++ OOT module. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool&oldid=14464](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool&oldid=14464)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Creating+Python+OOT+with+gr-modtool "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Creating_Python_OOT_with_gr-modtool&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool)
  * [View source](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Creating_Python_OOT_with_gr-modtool "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Creating_Python_OOT_with_gr-modtool "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool&oldid=14464 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool&action=info "More information about this page")


  * This page was last edited on 20 June 2024, at 18:27.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


