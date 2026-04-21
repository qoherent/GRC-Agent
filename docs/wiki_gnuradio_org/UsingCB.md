# UsingCB
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=UsingCB#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=UsingCB#searchInput)
# Using Code::Blocks for editing GNU Radio modules
[Code::Blocks](http://www.codeblocks.org/) is a widely used Integrated Development Environment (IDE) to develop C++ applications. This tutorial is supposed to give a howto get started using Code::Blocks with GNU Radio.  
It is assumed you are using gr-modtool as described [here](https://wiki.gnuradio.org/index.php?title=OutOfTreeModules "OutOfTreeModules") and Code::Blocks is installed on your machine. 
## Getting Started
After you created your project folder etc. with gr-modtools there are a few steps to do. 
  * Run _cmake-gui_ and add a new entry called CMAKE_CODEBLOCKS_EXECUTABLE  



Its value is a string with the path to your codeblocks executable. 
  * Finish this CMake preperations by generating the CMake files. (Click on Generate Button)
  * Open the Shell of your choice. Change directory to your project folder and create a new subdirectory.  



Maybe something like _build-cb_
  * execute 

```
cmake -G "CodeBlocks - Unix Makefiles" ..

```

  



The _-G_ option together with _"CodeBlocks - Unix Makefiles"_ specifies the CMake Generator.  
Possible Generators are described [here](http://cmake.org/cmake/help/v2.8.8/cmake.html#section_Generators). 
  * Now you can find a _your-project.cbp_ file in the current directory.
  * Open it using Code::Blocks


[![codeblocks_screenshot2.jpg](https://wiki.gnuradio.org/images/a/ac/Codeblocks_screenshot2.jpg)](https://wiki.gnuradio.org/index.php?title=File:Codeblocks_screenshot2.jpg "codeblocks_screenshot2.jpg")
## Using Code::Blocks
In the previous section it is described howto generate a Code::Blocks project with CMake. In this section there are some basic hints how to use Code::Blocks and thus have a quick start. In order to run a flowgraph use the your shell or GRC. Nevertheless there are some advantages if you Code::Blocks. 
  * You can compile/install your project and in case of an error the erroneous line will have automatic focus.
  * Switch between _.cc_ and _.h_ file by hitting _F11_.
  * There is a project explorer with all files.
  * You may configure and use source code formatting.
  * There is auto-completion available.
  * Code folding
  * Switch between methods by selecting them from a drop-down menu.
  * etc.


Retrieved from "[https://wiki.gnuradio.org/index.php?title=UsingCB&oldid=13102](https://wiki.gnuradio.org/index.php?title=UsingCB&oldid=13102)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=UsingCB "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=UsingCB "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:UsingCB "Discussion about the content page \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=UsingCB)
  * [View source](https://wiki.gnuradio.org/index.php?title=UsingCB&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=UsingCB&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/UsingCB "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/UsingCB "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=UsingCB&oldid=13102 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=UsingCB&action=info "More information about this page")


  * This page was last edited on 27 April 2023, at 09:04.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


