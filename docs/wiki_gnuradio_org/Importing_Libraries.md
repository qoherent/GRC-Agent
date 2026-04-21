# Importing Libraries
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Importing_Libraries#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Importing_Libraries#searchInput)
The import block enables calls to Python-based libraries such as NumPy within GRC, allowing for more sophistication in the use and creation of variables and parameters for blocks. 
  

## Import Block
Drag the **Import** block into the flowgraph. 
[![](https://wiki.gnuradio.org/images/c/c3/Importing_libraries_import_block.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_import_block.png)
  
Double-click the **Import** block to bring up the properties. The _Import_ field receives any legal Python import statement. For this example, the Numpy library is imported as _np_. 
[![](https://wiki.gnuradio.org/images/e/eb/Importing_libraries_import_statement.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_import_statement.png)
  
The **Import** block then displays the imported library as _np_. 
[![](https://wiki.gnuradio.org/images/5/5c/Importing_libraries_after_numpy_import.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_after_numpy_import.png)
  
Running the flowgraph generates a Python flowgraph of the same name, ending in .py. Opening the .py file displays the import statement: 
[![](https://wiki.gnuradio.org/images/a/a2/Importing_libraries_numpy_import_in_python.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_numpy_import_in_python.png)
## Variables Using Libraries
Variables and parameters in GRC can now use the NumPy library directly, both for the creation and manipulation of values. For example, the _sequentialArray_ variable is created using NumPy’s arange() function: 
[![](https://wiki.gnuradio.org/images/5/5e/Importing_libraries_sequential_array_variable.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_sequential_array_variable.png)
  
The _sequentialArray_ variable is then manipulated by the _reversedArray_ variable, reversing the order of the array: 
[![](https://wiki.gnuradio.org/images/a/a9/Importing_libraries_reversed_array_variable.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_reversed_array_variable.png)
  

## Randomized Variables
NumPy’s random number generators can be used to randomize values within a flowgraph. Create a variable named _randomAmplitude_ and assign the value using a uniform random variable from 0.1 to 1: 
[![](https://wiki.gnuradio.org/images/1/12/Importing_libraries_random_amplitude_variable.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_random_amplitude_variable.png)
  
Add a Signal Source to the flowgraph and use the randomAmplitude variable as the Amplitude: 
[![](https://wiki.gnuradio.org/images/b/be/Importing_libraries_signal_source_random_amplitude.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_signal_source_random_amplitude.png)
  
Connect the **Signal Source** to a **QT GUI Time Sink**. Note that the _randomAmplitude_ value is different between the **Variable** and **Signal Source** blocks. This difference in values is because GRC evaluates the variable once the block property window is closed, and since the two windows were closed at different times it evaluated to two different values. 
[![](https://wiki.gnuradio.org/images/f/f8/Importing_libraries_random_amplitude_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_random_amplitude_flowgraph.png)
  
However, this is not reflected in the underlying Python code. Instead, the variable text is unevaluated until run-time, allowing for proper randomization. 
[![](https://wiki.gnuradio.org/images/1/10/Importing_libraries_random_amplitude_in_python.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_random_amplitude_in_python.png)
  
Running the flowgraph demonstrates that the amplitude is randomized. The following two **QT GUI Time Sink** plots shows two different amplitude values for the **Signal Source** output. 
[![](https://wiki.gnuradio.org/images/1/13/Importing_libraries_random_amplitude_example_1.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_random_amplitude_example_1.png)
[![](https://wiki.gnuradio.org/images/f/fd/Importing_libraries_random_amplitude_example_2.png)](https://wiki.gnuradio.org/index.php?title=File:Importing_libraries_random_amplitude_example_2.png)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Importing_Libraries&oldid=13996](https://wiki.gnuradio.org/index.php?title=Importing_Libraries&oldid=13996)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Importing+Libraries "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Importing_Libraries "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Importing_Libraries&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Importing_Libraries)
  * [View source](https://wiki.gnuradio.org/index.php?title=Importing_Libraries&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Importing_Libraries&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Importing_Libraries "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Importing_Libraries "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Importing_Libraries&oldid=13996 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Importing_Libraries&action=info "More information about this page")


  * This page was last edited on 10 April 2024, at 21:43.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


