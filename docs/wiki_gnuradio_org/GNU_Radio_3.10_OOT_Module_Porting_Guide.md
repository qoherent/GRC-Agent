# GNU Radio 3.10 OOT Module Porting Guide
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#searchInput)
The major changes in the GNU Radio 3.10 release that impact OOTs are: 
  * C++ modernization (C++17)
  * Introduction of gr-pdu
  * ATSC Block Refactoring
  * New Logging Infrastructure
  * OOT structure
  * QT Frequency/Waterfall block ranges


There is a decent chance that your OOT doesn't actually require changes to be transitioned to 3.10. 
## Contents
  * [1 Porting Guide](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#Porting_Guide)
    * [1.1 C++ modernization](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#C++_modernization)
      * [1.1.1 Prerequisites](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#Prerequisites)
    * [1.2 Versioning Your shared object files](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#Versioning_Your_shared_object_files)
    * [1.3 OOT Structure](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#OOT_Structure)
    * [1.4 Logging](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#Logging)
    * [1.5 QT FFT Size Ranges](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#QT_FFT_Size_Ranges)
    * [1.6 API Changes](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#API_Changes)
      * [1.6.1 Removal of Decimation Parameter from FIR objects](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#Removal_of_Decimation_Parameter_from_FIR_objects)
      * [1.6.2 ATSC Blocks](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide#ATSC_Blocks)


# Porting Guide
## C++ modernization
GR 3.10 uses C++17 which allows some nice language features. Some might affect your OOT. Please document any needed changes here 
### Prerequisites
The version of pybind on Ubuntu 20.04 (2.4.3) is too old to bind against gnuradio 3.10, there are other tools that are also too old, (such as pygccxml). Ubuntu 22.04 has current versions of all these tools and thus avoids many issues. You can find the version of pybind your gnuradio installation references: 

```
$ gnuradio-config-info --pybind

```

You can find the installed version of pybind on your system: 

```
$ apt policy pybind11-dev

```

The following have been noted as being too old on Ubuntu 20.04: 
1. Binding your OOT module with "gr_modtool bind modulename" requires that pygccxml be more current than is installed by default on many systems. Ubuntu 20.04 installs pygccxml version 1.9.1 by default which does not work. To display the versions of installed pip packages: 

```
$ pip list

```

You can update to the latest pygccxml via: 

```
$ python3 -m pip install --upgrade pygccxml

```

2. spdlog is required to be installed (CMAKE fails otherwise as it cannot find the package): 

```
$ sudo apt install libspdlog-dev

```

## Versioning Your shared object files
You may want to edit the /lib/CMakeList.txt file in order to set a version. The default VERSION_PATCH is set to git, you may want to edit it to 0 for your first version. Then when you need to push out a modified version remember to edit the version numbers before building. If you leave the VERSION_PATCH at git the install directory may eventually become littered with old libgnuradio-yourproject.so.git-commit-number files and soft links. 
## OOT Structure
3.10 restructures the OOT file structure to more closely resemble the in-tree components. OOTs created with 3.9 should have no issue being interpreted by modtool and the macros to do python bindings. But creating a new OOT will be slightly different and have a different structure. OOTs created with 3.10 will not work with modtool from 3.9, but they still should be able to compile with 3.9 
  

## Logging
The logging backend was overhauled, but most of the old logging API remains in place. So, `GR_LOG_WARN(d_logger, "Warning!");` still works. 
You are, however, encouraged to use the newer API that allows for in-line format string usage (which is only evaluated if the logging at the given level is actually activated). Benefit: This might remove the need to use Boost just to get `boost::format`; it's also faster when active, and the overhead when inactive is minimal (TODO: link to PR comment where speed was benchmarked). 

```
d_logger->warn("Unable to read {:d} items from buffer '{:s}'; resetting to default value {:f}",
               num_items,
               name,
               default_float_value);

```

Outside of blocks, where you might not have access to a readily set up logger: 

```
#include <gnuradio/logger.h>
…
  gr::logger logger("my thing that logs");
  logger.info("Setting up this rather complicated thing");

```

Notice that this is really a logger object, not a smart pointer, as the use cases for non-block loggers aren't always clearly cut. If you have a (non-block) class that needs to log something: 

```
// test.cc
#include <gnuradio/logger.h>

class my_thing
{
private:
    gr::logger _logger;

public:
    my_thing(const std::string& name)
        : _logger("my thing " + name)
    {
        _logger.info("constructed");
    }
    ~my_thing() { _logger.warn("I don't like being destructed!"); }
};

int main() { my_thing thing("gizmo"); }

```

yielding (after a `$CXX $(pkg-config --cflags --libs gnuradio-runtime)  $(pkg-config --cflags --libs spdlog) -o test test.cc && ./test`) 

```
my thing gizmo :info: constructed
my thing gizmo :warning: I don't like being destructed!

```

## QT FFT Size Ranges
For QT Frequency Sink and QT Waterfall Sink: because of the interaction between the block and some of the graphical elements such as the control panel, we have limited the available fft sizes to powers of 2 between 32 and 32768 to keep the widget in a well defined state. Flowgraphs with values for fft size that are not powers of 2 will show an error in GRC, but for python flowgraphs, will force it to a default value. 
## API Changes
### Removal of Decimation Parameter from FIR objects
There was previously a decimation value in the constructor of a kernel::fir_xxx object that has now been removed 
### ATSC Blocks
The inputs and outputs of the ATSC blocks previously passed structs that contained data and metadata. These have now been broken out into separate streams. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide&oldid=12281](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide&oldid=12281)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [3.10](https://wiki.gnuradio.org/index.php?title=Category:3.10&action=edit&redlink=1 "Category:3.10 \(page does not exist\)")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=GNU+Radio+3.10+OOT+Module+Porting+Guide "You are encouraged to log in; however, it is not mandatory \[o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide "View the content page \[c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:GNU_Radio_3.10_OOT_Module_Porting_Guide&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide)
  * [View source](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide&action=edit "This page is protected.
You can view its source \[e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide&action=history "Past revisions of this page \[h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/GNU_Radio_3.10_OOT_Module_Porting_Guide "A list of all wiki pages that link here \[j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/GNU_Radio_3.10_OOT_Module_Porting_Guide "Recent changes in pages linked from this page \[k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide&oldid=12281 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.10_OOT_Module_Porting_Guide&action=info "More information about this page")


  * This page was last edited on 16 May 2022, at 17:10.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


