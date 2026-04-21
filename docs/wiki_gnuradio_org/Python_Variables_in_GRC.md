# Python Variables in GRC
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC#searchInput)  
|  **Beginner Tutorials** Introducing GNU Radio 
  1. [What is GNU Radio?](https://wiki.gnuradio.org/index.php?title=What_Is_GNU_Radio "What Is GNU Radio")
  2. [Installing GNU Radio](https://wiki.gnuradio.org/index.php?title=InstallingGR "InstallingGR")
  3. [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph")

Flowgraph Fundamentals 
  1. Python Variables in GRC
  2. [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs")
  3. [Runtime Updating Variables](https://wiki.gnuradio.org/index.php?title=Runtime_Updating_Variables "Runtime Updating Variables")
  4. [Signal Data Types](https://wiki.gnuradio.org/index.php?title=Signal_Data_Types "Signal Data Types")
  5. [Converting Data Types](https://wiki.gnuradio.org/index.php?title=Converting_Data_Types "Converting Data Types")
  6. [Packing Bits](https://wiki.gnuradio.org/index.php?title=Packing_Bits "Packing Bits")
  7. [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors")
  8. [Hier Blocks and Parameters](https://wiki.gnuradio.org/index.php?title=Hier_Blocks_and_Parameters "Hier Blocks and Parameters")

Creating and Modifying Python Blocks 
  1. [Creating Your First Block](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block "Creating Your First Block")
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
This tutorial describes how Python data types are used in GRC and how the variables are displayed. 
The previous tutorial, [Your First Flowgraph](https://wiki.gnuradio.org/index.php?title=Your_First_Flowgraph "Your First Flowgraph"), shows how to build a simple flowgraph. The next tutorial, [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs"), describes how to use and modify variables in a more sophisticated flowgraph. 
## Contents
  * [1 Floats and Integers in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC#Floats_and_Integers_in_GRC)
  * [2 Strings in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC#Strings_in_GRC)
  * [3 Lists and Tuples in GRC](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC#Lists_and_Tuples_in_GRC)
  * [4 List Comprehension](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC#List_Comprehension)
  * [5 Property Colors in GNU Radio Companion](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC#Property_Colors_in_GNU_Radio_Companion)


## Floats and Integers in GRC
GNU Radio Companion (GRC) uses Python data types to represent variables. The simplest data types describe numbers. Numbers in Python can be floating point or integers: 

```
floatNumber = 3.14
integerNumber = 2

```

Integers can be converted to floating point using _float()_ , and floating point numbers can be converted to integers using _int()_ : 

```
floatNumber = float(2)
integerNumber = int(3.14)

```

Type conversion can be done within the variable blocks: 
[![](https://wiki.gnuradio.org/images/thumb/7/7e/FloatToIntProperties.png/500px-FloatToIntProperties.png)](https://wiki.gnuradio.org/index.php?title=File:FloatToIntProperties.png)
  
The value is displayed as an integer: 
[![](https://wiki.gnuradio.org/images/2/29/FloatToIntVariable.png)](https://wiki.gnuradio.org/index.php?title=File:FloatToIntVariable.png)
  
GRC displays numbers differently than Python. For example, the _samp_rate_ block is added to every new flowgraph. 
[![](https://wiki.gnuradio.org/images/thumb/c/c6/SampRateVariable.png/300px-SampRateVariable.png)](https://wiki.gnuradio.org/index.php?title=File:SampRateVariable.png)
Double-click the _samp_rate_ variable to edit the properties: 
[![](https://wiki.gnuradio.org/images/thumb/5/59/SampRateProperties.png/500px-SampRateProperties.png)](https://wiki.gnuradio.org/index.php?title=File:SampRateProperties.png)
The value of _samp_rate_ is _32000_ yet GRC displays the value _32k_. GRC converts all numbers into [SI Units](https://en.wikipedia.org/wiki/International_System_of_Units). Note that GRC _may_ display a number in a different format than it is represented in Python. 
For another example, drag and drop a new variable into the workspace. Double-click to edit the properties: 
  * Id: _floatNumber_
  * Value: _0.25_


[![](https://wiki.gnuradio.org/images/thumb/c/c8/FloatNumberProperties.png/500px-FloatNumberProperties.png)](https://wiki.gnuradio.org/index.php?title=File:FloatNumberProperties.png)
  
GRC now displays the value _0.25_ as _250m_ because it has been converted to SI units (milli-) : 
[![](https://wiki.gnuradio.org/images/d/d0/FloatNumberVariable.png)](https://wiki.gnuradio.org/index.php?title=File:FloatNumberVariable.png)
## Strings in GRC
Python uses both single quotes ' and double quotes " to contain strings: 

```
singleQuoteString = 'string1'
doubleQuoteString = "string2"

```

Strings can be used as variables in GRC: 
[![](https://wiki.gnuradio.org/images/thumb/2/20/StringProperties.png/500px-StringProperties.png)](https://wiki.gnuradio.org/index.php?title=File:StringProperties.png)
  
The string is displayed in GRC: 
[![](https://wiki.gnuradio.org/images/3/3a/StringVariable.png)](https://wiki.gnuradio.org/index.php?title=File:StringVariable.png)
## Lists and Tuples in GRC
Variables in GRC can use Python lists: 
[![](https://wiki.gnuradio.org/images/thumb/b/be/ListProperties.png/500px-ListProperties.png)](https://wiki.gnuradio.org/index.php?title=File:ListProperties.png)
  
The list is displayed in GRC: 
[![](https://wiki.gnuradio.org/images/e/e2/ListVariable.png)](https://wiki.gnuradio.org/index.php?title=File:ListVariable.png)
  
Variables in GRC can use Python tuples: 
[![](https://wiki.gnuradio.org/images/thumb/6/60/TupleProperties.png/500px-TupleProperties.png)](https://wiki.gnuradio.org/index.php?title=File:TupleProperties.png)
  
The tuple is displayed in GRC: 
[![](https://wiki.gnuradio.org/images/c/ca/TupleVariable.png)](https://wiki.gnuradio.org/index.php?title=File:TupleVariable.png)
## List Comprehension
Each _Variable_ is a single line in Python: 
_Id = Value_
[List comprehension](https://www.w3schools.com/python/python_lists_comprehension.asp) can be used to write functions in a _Variable_. For example, list comprehension is used to loop through a list, add +1 to all entries, and then multiply each entry by 2: 

```
listVariable = [0, 1, 2, 3]
listComprehensionExample = [(i + 1) * 2 for i in listVariable]

```

This list comprehension example is used in GNU Radio by using two variables, _listVariable_ and _listComprehensionExample_ , and entering their associated _values_ : 
[![](https://wiki.gnuradio.org/images/thumb/5/53/ListVariableProperties.png/500px-ListVariableProperties.png)](https://wiki.gnuradio.org/index.php?title=File:ListVariableProperties.png)
[![](https://wiki.gnuradio.org/images/thumb/9/9b/ListComprehensionVariableProperties.png/500px-ListComprehensionVariableProperties.png)](https://wiki.gnuradio.org/index.php?title=File:ListComprehensionVariableProperties.png)
The lists are displayed in GRC: 
[![](https://wiki.gnuradio.org/images/a/a6/ListComprehensionVariables.png)](https://wiki.gnuradio.org/index.php?title=File:ListComprehensionVariables.png)
## Property Colors in GNU Radio Companion
GRC uses a color scheme to represent data types when editing block properties. The properties for the _QT GUI Frequency Sink_ block are as follows: 
[![](https://wiki.gnuradio.org/images/d/d6/QTGUIFrequencySinkBlock.png)](https://wiki.gnuradio.org/index.php?title=File:QTGUIFrequencySinkBlock.png)
[![](https://wiki.gnuradio.org/images/thumb/c/c6/QTGUIFrequencySinkPropertyColors.png/500px-QTGUIFrequencySinkPropertyColors.png)](https://wiki.gnuradio.org/index.php?title=File:QTGUIFrequencySinkPropertyColors.png)
There are a variety of colors for the _QT GUI Frequency Sink_ properties: **orange** , **green** and **purple**. Each color corresponds to a different data type: 
  * Floating Point: **orange**
  * Integer: **green**
  * String: **purple**


For example, the _bandwidth_ is **orange** because the bandwidth can be any floating point number. The _FFT Size_ must be an integer so it is colored in **green**. The _Y Label_ is a string because it contains words used to describe the vertical axis of the plot so it is colored in **purple**. 
The _Variable_ blocks do not have a color because they can be used to represent any data type or object. 
The next tutorial, [Variables in Flowgraphs](https://wiki.gnuradio.org/index.php?title=Variables_in_Flowgraphs "Variables in Flowgraphs"), describes how to use and modify variables in a more sophisticated flowgraph. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC&oldid=13490](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC&oldid=13490)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Python+Variables+in+GRC "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Python_Variables_in_GRC&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC)
  * [View source](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Python_Variables_in_GRC "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Python_Variables_in_GRC "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC&oldid=13490 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Python_Variables_in_GRC&action=info "More information about this page")


  * This page was last edited on 23 October 2023, at 21:02.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


