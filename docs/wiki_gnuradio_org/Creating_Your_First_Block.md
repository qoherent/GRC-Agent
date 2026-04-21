# Creating Your First Block
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block#searchInput)  
|  **Beginner Tutorials** Introducing GNU Radio 
  1. [What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "What Is GNU Radio")
  2. [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR")
  3. [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph")

Flowgraph Fundamentals 
  1. [Python Variables in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "Python Variables in GRC")
  2. [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs")
  3. [Runtime Updating Variables](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables "Runtime Updating Variables")
  4. [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types")
  5. [Converting Data Types](https://wiki.gnuradio.org/index.php?title=Converting_Data_Types "Converting Data Types")
  6. [Packing Bits](https://wiki.gnuradio.org/index.php?title=Packing_Bits "Packing Bits")
  7. [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors")
  8. [Hier Blocks and Parameters](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters "Hier Blocks and Parameters")

Creating and Modifying Python Blocks 
  1. Creating Your First Block
  2. [Python Block With Vectors](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors "Python Block with Vectors")
  3. [Python Block Message Passing](https://wiki.gnuradio.org/index.php?title=Python_Block_Message_Passing "Python Block Message Passing")
  4. [Python Block Tags](https://wiki.gnuradio.org/index.php?title=Python_Block_Tags "Python Block Tags")

DSP Blocks 
  1. [Low Pass Filter Example](https://wiki.gnuradio.org/index.php?title=Low_Pass_Filter_Example "Low Pass Filter Example")
  2. [Designing Filter Taps](https://wiki.gnuradio.org/index.php?title=Designing_Filter_Taps "Designing Filter Taps")
  3. [Sample Rate Change](https://wiki.gnuradio.org/index.php?title=Sample_Rate_Change "Sample Rate Change")
  4. [Frequency Shifting](https://wiki.gnuradio.org/index.php?title=Frequency_Shifting "Frequency Shifting")
  5. [Reading and Writing Binary Files](https://wiki.gnuradio.org/index.php?title=Reading_and_Writing_Binary_Files "Reading and Writing Binary Files")

SDR Hardware 
  1. [RTL-SDR FM Receiver](https://wiki.gnuradio.org/index.php?title=RTL-SDR_FM_Receiver "RTL-SDR FM Receiver")
  2. [B200-B205mini FM Receiver](https://wiki.gnuradio.org/index.php?title=B200-B205mini_FM_Receiver "B200-B205mini FM Receiver")
  3. [E310 FM Receiver](https://wiki.gnuradio.org/index.php?title=E310_FM_Receiver "E310 FM Receiver")

 |  
| --- |  
This tutorial shows how to create a signal processing block with the _Embedded Python Block_. The example block either adds or multiplys the two inputs based on a parameter. 
This tutorial uses the _Embedded Python Block'_ which can only be used in the flowgraph it was created in. The tutorial [Creating Python OOT with gr-modtool](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool "Creating Python OOT with gr-modtool") demonstrates how to create a custom Python block as an out-of-tree (OOT) module which can be installed and used in any flowgraph. 
The previous tutorial, [Hier Blocks and Parameters](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters "Hier Blocks and Parameters"), describes how to create a _hierarchical block_ and how to use _parameters_. The next tutorial, [Python Block with Vectors](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors "Python Block with Vectors"), demonstrates how to write an _Embedded Python Block_ with vector inputs and outputs. 
## Contents
  * [1 Opening Code Editor](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block#Opening_Code_Editor)
  * [2 Components of a Python Block](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block#Components_of_a_Python_Block)
  * [3 Changing Parameter Name](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block#Changing_Parameter_Name)
  * [4 Editing Block Inputs](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block#Editing_Block_Inputs)
  * [5 Editing Work Function](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block#Editing_Work_Function)
  * [6 Connecting the Flowgraph](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block#Connecting_the_Flowgraph)
  * [7 Running the Flowgraph](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block#Running_the_Flowgraph)


## Opening Code Editor
The _Embedded Python Block_ is a tool to quickly prototype a block within a flowgraph. Search for the _Python Block_ and add it to the workspace: 
[![](https://wiki.gnuradio.org/images/thumb/5/52/AddPythonBlockToWorkspace.png/700px-AddPythonBlockToWorkspace.png)](https://wiki.gnuradio.org/index.php?title=File:AddPythonBlockToWorkspace.png)
  
Double-click the block to edit the properties. The _Embedded Python Block_ has two properties, 
  1. _Code_ , a click-box which contains a link to the Python code for the block and
  2. _Example_Param_ , an input parameter to the block.


  
Click on _Open in Editor_ to edit the Python code: 
[![](https://wiki.gnuradio.org/images/thumb/f/f1/EmbeddedPythonBlockProperties.png/500px-EmbeddedPythonBlockProperties.png)](https://wiki.gnuradio.org/index.php?title=File:EmbeddedPythonBlockProperties.png)
  
A prompt is displayed with a choice of which text editor to use to write the Python code. Click _Use Default_ : 
[![](https://wiki.gnuradio.org/images/thumb/f/f6/ClickUseDefault.png/500px-ClickUseDefault.png)](https://wiki.gnuradio.org/index.php?title=File:ClickUseDefault.png)
  
An editor window displays the Python code for the _Embedded Python Block_ : 
[![](https://wiki.gnuradio.org/images/thumb/1/12/PythonCodeGedit.png/500px-PythonCodeGedit.png)](https://wiki.gnuradio.org/index.php?title=File:PythonCodeGedit.png)
## Components of a Python Block
There are three important sections in the Python block code: 
  1. _import_ statements in a **green box**
  2. ___init___ method in a **blue box**
  3. _work_ method in a **red box**

  
| Screenshot of editor window   | Program code from editor window   |  
| --- | --- |  
|  [![height=500px](https://wiki.gnuradio.org/images/e/eb/PythonBlockCodeFunctions.png)](https://wiki.gnuradio.org/index.php?title=File:PythonBlockCodeFunctions.png "height=500px")  |  
```
"""
Embedded Python Blocks:

Each time this file is saved, GRC will instantiate the first class it finds
to get ports and parameters of your block. The arguments to __init__  will
be the parameters. All of them are required to have default values!
"""

import numpy as np
from gnuradio import gr


class blk(gr.sync_block):  # other base classes are basic_block, decim_block, interp_block
    """Embedded Python Block example - a simple multiply const"""

    def __init__(self, example_param=1.0):  # only default arguments here
        """arguments to this function show up as parameters in GRC"""
        gr.sync_block.__init__(
            self,
            name='Embedded Python Block',   # will show up in GRC
            in_sig=[np.complex64],
            out_sig=[np.complex64]
        )
        # if an attribute with the same name as a parameter is found,
        # a callback is registered (properties work, too).
        self.example_param = example_param

    def work(self, input_items, output_items):
        """example: multiply with constant"""
        output_items[0][:] = input_items[0] * self.example_param
        return len(output_items[0])

```
 |  
  
The `import` statement includes the NumPy and GNU Radio libraries. 
The `__init__` method: 
  1. Accepts the `example_param` parameter with a default argument of 1.0
  2. Declares the block to have a `np.complex64` input and output, which is the GNU Radio `Complex Float 32` data type
  3. Stores the `self.example_param` variable from the input parameter


The `work` method: 
  1. Has the input `input_items` and output `output_items` parameters
  2. Applies a mathematical operation to `input_items` and stores the result in `output_items`
  3. Returns the number of samples produced


The remainder of this tutorial will describe modifications to both the _init()_ and _work()_ functions to demonstrate how to provide custom functionality. The _init()_ and _work()_ functions cannot be changed arbitrarily as they must conform to the rules and expectations of the broader GNU Radio software framework which controls transferring data between block inputs and outputs. For example, the number of input parameters to the _init()_ function can be changed to include the different number of variables being passed into the block, however the _work()_ function must use the pre-existing function definition which includes _input_items_ and _output_items_ and in the correct order. Additionally, the **Embedded Python Block** must return the number of output samples produced which must be equivalent to the number of input samples produced. If you are creating your first block the suggested path is to follow this tutorial exactly, step by step, and then afterward attempt to modify the working block in small incremental ways as you build up new functionality. 
More sophisticated blocks can be produced such as those which incorporate sampling rate change (produce more or less output samples than the number of input samples), blocks which do not have an input or output, or blocks which only produce or consume messages. These blocks can be created through the use of gr_modtool in both [c++](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool "Creating C++ OOT with gr-modtool") and [Python](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool "Creating Python OOT with gr-modtool"). It is also recommended to review the [Types of Blocks](https://wiki.gnuradio.org/index.php?title=Types_of_Blocks "Types of Blocks") and [Blocks Coding Guide](https://wiki.gnuradio.org/index.php?title=BlocksCodingGuide "BlocksCodingGuide") pages. 
## Changing Parameter Name
The code is modified to add the custom behavior. 
The first step is to rename _example_param_ to _additionFlag_ to be more descriptive. Assuming your editor is a bit like the GNOME `gedit` program shown in the screenshots here, from the editor menu select _Find and Replace_ : 
[![](https://wiki.gnuradio.org/images/thumb/2/29/SelectFindAndReplace.png/700px-SelectFindAndReplace.png)](https://wiki.gnuradio.org/index.php?title=File:SelectFindAndReplace.png)
Enter: 
  * _Find_ > _example_param_
  * _Replace with_ > _additionFlag_
  * Click _Replace All_


[![](https://wiki.gnuradio.org/images/9/96/FindReplaceExampleParam.png)](https://wiki.gnuradio.org/index.php?title=File:FindReplaceExampleParam.png)
  
The parameter is changed. The Python code is updated: 

```
"""
Embedded Python Blocks:

Each time this file is saved, GRC will instantiate the first class it finds
to get ports and parameters of your block. The arguments to __init__  will
be the parameters. All of them are required to have default values!
"""

import numpy as np
from gnuradio import gr


class blk(gr.sync_block):  # other base classes are basic_block, decim_block, interp_block
    """Embedded Python Block example - a simple multiply const"""

    def __init__(self, additionFlag=1.0):  # only default arguments here
        """arguments to this function show up as parameters in GRC"""
        gr.sync_block.__init__(
            self,
            name='Embedded Python Block',   # will show up in GRC
            in_sig=[np.complex64],
            out_sig=[np.complex64]
        )
        # if an attribute with the same name as a parameter is found,
        # a callback is registered (properties work, too).
        self.additionFlag = additionFlag

    def work(self, input_items, output_items):
        """example: multiply with constant"""
        output_items[0][:] = input_items[0] * self.additionFlag
        return len(output_items[0])

```

Change the default value to be `True` (so a truth value instead of the floating point number `1.0`): 

```
"""
Embedded Python Blocks:

Each time this file is saved, GRC will instantiate the first class it finds
to get ports and parameters of your block. The arguments to __init__  will
be the parameters. All of them are required to have default values!
"""

import numpy as np
from gnuradio import gr


class blk(gr.sync_block):  # other base classes are basic_block, decim_block, interp_block
    """Embedded Python Block example - a simple multiply const"""

    def __init__(self, additionFlag=True):  # only default arguments here
        """arguments to this function show up as parameters in GRC"""
        gr.sync_block.__init__(
            self,
            name='Embedded Python Block',   # will show up in GRC
            in_sig=[np.complex64],
            out_sig=[np.complex64]
        )
        # if an attribute with the same name as a parameter is found,
        # a callback is registered (properties work, too).
        self.additionFlag = additionFlag

    def work(self, input_items, output_items):
        """example: multiply with constant"""
        output_items[0][:] = input_items[0] * self.additionFlag
        return len(output_items[0])

```

  

Save the file: 
[![](https://wiki.gnuradio.org/images/thumb/f/f8/SaveButtonGedit.png/500px-SaveButtonGedit.png)](https://wiki.gnuradio.org/index.php?title=File:SaveButtonGedit.png)
  
Return back to the GRC window. 
The _Embedded Python Block_ displays the _Additionflag_ parameter instead of _example_param_ : 
[![](https://wiki.gnuradio.org/images/thumb/e/ea/AdditionFlagUpdatedBlock.png/500px-AdditionFlagUpdatedBlock.png)](https://wiki.gnuradio.org/index.php?title=File:AdditionFlagUpdatedBlock.png)
## Editing Block Inputs
The default block has a single input and a single output, however we need two inputs for the block. To add an input, add a second `np.complex64`np.complex64 to the `in_sig` list: 

```
"""
Embedded Python Blocks:

Each time this file is saved, GRC will instantiate the first class it finds
to get ports and parameters of your block. The arguments to __init__  will
be the parameters. All of them are required to have default values!
"""

import numpy as np
from gnuradio import gr


class blk(gr.sync_block):  # other base classes are basic_block, decim_block, interp_block
    """Embedded Python Block example - a simple multiply const"""

    def __init__(self, additionFlag=True):  # only default arguments here
        """arguments to this function show up as parameters in GRC"""
        gr.sync_block.__init__(
            self,
            name='Embedded Python Block',   # will show up in GRC
            in_sig=[np.complex64, np.complex64],
            out_sig=[np.complex64]
        )
        # if an attribute with the same name as a parameter is found,
        # a callback is registered (properties work, too).
        self.additionFlag = additionFlag

    def work(self, input_items, output_items):
        """example: multiply with constant"""
        output_items[0][:] = input_items[0] * self.additionFlag
        return len(output_items[0])

```

Change the block name to _Add or Multiply Block_ : 

```
"""
Embedded Python Blocks:

Each time this file is saved, GRC will instantiate the first class it finds
to get ports and parameters of your block. The arguments to __init__  will
be the parameters. All of them are required to have default values!
"""

import numpy as np
from gnuradio import gr


class blk(gr.sync_block):  # other base classes are basic_block, decim_block, interp_block
    """Embedded Python Block example - a simple multiply const"""

    def __init__(self, additionFlag=True):  # only default arguments here
        """arguments to this function show up as parameters in GRC"""
        gr.sync_block.__init__(
            self,
            name='Add or Multiply Block',   # will show up in GRC
            in_sig=[np.complex64, np.complex64],
            out_sig=[np.complex64]
        )
        # if an attribute with the same name as a parameter is found,
        # a callback is registered (properties work, too).
        self.additionFlag = additionFlag

    def work(self, input_items, output_items):
        """example: multiply with constant"""
        output_items[0][:] = input_items[0] * self.additionFlag
        return len(output_items[0])

```

  
Save the file. GRC displays the block with a second input and the block name is updated: 
[![](https://wiki.gnuradio.org/images/thumb/b/b1/SecondInputOnBlockGRC.png/500px-SecondInputOnBlockGRC.png)](https://wiki.gnuradio.org/index.php?title=File:SecondInputOnBlockGRC.png)
## Editing Work Function
The _work_ function needs to be modified. 
The pseudo-code for the Python block is: 

```
if (additionFlag is True)
    then add the two inputs
else
    then multiply the two inputs
```

Modify the _work_ function so it has the following code: 

```
"""
Embedded Python Blocks:

Each time this file is saved, GRC will instantiate the first class it finds
to get ports and parameters of your block. The arguments to __init__  will
be the parameters. All of them are required to have default values!
"""

import numpy as np
from gnuradio import gr


class blk(gr.sync_block):  # other base classes are basic_block, decim_block, interp_block
    """Embedded Python Block example - a simple multiply const"""

    def __init__(self, additionFlag=True):  # only default arguments here
        """arguments to this function show up as parameters in GRC"""
        gr.sync_block.__init__(
            self,
            name='Add or Multiply Block',   # will show up in GRC
            in_sig=[np.complex64, np.complex64],
            out_sig=[np.complex64]
        )
        # if an attribute with the same name as a parameter is found,
        # a callback is registered (properties work, too).
        self.additionFlag = additionFlag

    def work(self, input_items, output_items):
        """example: add or multiply based on flag"""
        if self.additionFlag:
            output_items[0][:] = input_items[0][:] + input_items[1][:]
        else:
            output_items[0][:] = input_items[0][:] * input_items[1][:]
        return len(output_items[0])

```

Remember to indent with multiples of 4 spaces (4, 8, 12, etc.) when starting new lines in Python! 
Save the the code. 
## Connecting the Flowgraph
Return to GRC. Double-click the _Add or Multiply Block_. Enter _True_ for the _Additionflag_ property: 
[![](https://wiki.gnuradio.org/images/thumb/9/9b/SetAdditionFlagProperty.png/500px-SetAdditionFlagProperty.png)](https://wiki.gnuradio.org/index.php?title=File:SetAdditionFlagProperty.png)
Click _OK_ to save. 
Drag and drop two _Signal Source_ blocks, a _Throttle_ block, a _QT GUI Time Sink_ and a _QT GUI Frequency Sink_ block into the GRC workspace and connect them according to the following flowgraph. Set the _frequency_ of the second _Signal Source_ to 3000: 
[![](https://wiki.gnuradio.org/images/thumb/8/84/ConnectAddMultiplyFlowgraph.png/700px-ConnectAddMultiplyFlowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:ConnectAddMultiplyFlowgraph.png)
## Running the Flowgraph
Selecting _True_ in the _Add or Multiply Block_ performs the addition of the two _Signal Sources_. Running the flowgraph gives the following two plots: 
[![](https://wiki.gnuradio.org/images/thumb/d/d1/SinusoidAddition.png/700px-SinusoidAddition.png)](https://wiki.gnuradio.org/index.php?title=File:SinusoidAddition.png)
  
The plots show the summation of the two sinusoids, one at a frequency of 1,000 and another at 3,000. The y-axis in the _QT GUI Time Sink_ plot is partially cutting off the amplitude of the sinusoids. Click the scroll-wheel button to bring up the display menu and select _Auto Scale_ : 
[![](https://wiki.gnuradio.org/images/thumb/7/77/SelectAutoScaleTimeSink.png/700px-SelectAutoScaleTimeSink.png)](https://wiki.gnuradio.org/index.php?title=File:SelectAutoScaleTimeSink.png)
  
The full amplitude of the two sinusoids can then be seen: 
[![](https://wiki.gnuradio.org/images/thumb/c/c0/TimeSinkFullAmplitude.png/700px-TimeSinkFullAmplitude.png)](https://wiki.gnuradio.org/index.php?title=File:TimeSinkFullAmplitude.png)
Stop the flowgraph by closing the _QT GUI Time Sink_ or by pressing the square button in GRC: 
[![](https://wiki.gnuradio.org/images/1/16/StopFlowgraphButtons.png)](https://wiki.gnuradio.org/index.php?title=File:StopFlowgraphButtons.png)
  
Enter _False_ for the _Additionflag_ property: 
[![](https://wiki.gnuradio.org/images/thumb/0/00/SetFalseAdditionFlag.png/500px-SetFalseAdditionFlag.png)](https://wiki.gnuradio.org/index.php?title=File:SetFalseAdditionFlag.png)
Click _OK_ to save. 
By definition, the multiplication of two complex sinusoids produces a sinusoid at the summation of the two frequencies. Therefore, the multiplication of the _Signal Source_ of frequency 1,000 and frequency 3,000 is a complex sinusoid of frequency 4,000. This complex sinusoid is seen when running the flowgraph: 
[![](https://wiki.gnuradio.org/images/thumb/3/38/MultiplicationSinusoids.png/700px-MultiplicationSinusoids.png)](https://wiki.gnuradio.org/index.php?title=File:MultiplicationSinusoids.png)
  
The next tutorial, [Python Block With Vectors](https://wiki.gnuradio.org/index.php?title=Python_Block_with_Vectors "Python Block with Vectors") describes how the _Python Embedded Block_ can be modified to accept vector inputs and outputs, and how the _input_items_ vector indexing is different between vectors and streams. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block&oldid=14246](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block&oldid=14246)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Creating+Your+First+Block "You are encouraged to log in; however, it is not mandatory \[o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block "View the content page \[c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Creating_Your_First_Block&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block)
  * [View source](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block&action=edit "This page is protected.
You can view its source \[e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block&action=history "Past revisions of this page \[h\]")


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
  * [Recent changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChanges "A list of recent changes in the wiki \[r\]")
  * [Random page](https://wiki.gnuradio.org/index.php?title=Special:Random "Load a random page \[x\]")
  * [Help](https://www.mediawiki.org/wiki/Special:MyLanguage/Help:Contents "The place to find out")


###  Tools
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Creating_Your_First_Block "A list of all wiki pages that link here \[j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Creating_Your_First_Block "Recent changes in pages linked from this page \[k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block&oldid=14246 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block&action=info "More information about this page")


  * This page was last edited on 4 May 2024, at 14:13.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


