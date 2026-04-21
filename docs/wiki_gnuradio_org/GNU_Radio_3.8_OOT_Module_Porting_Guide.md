# GNU Radio 3.8 OOT Module Porting Guide
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#searchInput)
Brace yourself! 3.8 is here. This document, hopefully, eases the transition of your OOT to our new major version. Furthermore, we think it is a good occasion to establish some standards on how an OOT module should be structured and maintained. More uniformity is an advantage for the users, for you as a OOT maintainer, for the GR devs, since it will be easier to integrate it into PyBOMBS, and for the packagers to ship your OOT module. 
## Contents
  * [1 Development Model](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#Development_Model)
  * [2 Install GNU Radio 3.8](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#Install_GNU_Radio_3.8)
  * [3 CMake Updates](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#CMake_Updates)
    * [3.1 Summary](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#Summary)
    * [3.2 Look at Examples](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#Look_at_Examples)
    * [3.3 CMake Modules](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#CMake_Modules)
    * [3.4 GNU Radio Components](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#GNU_Radio_Components)
  * [4 Python Blocks](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#Python_Blocks)
  * [5 Versioning](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#Versioning)
  * [6 Porting QA Tests](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#Porting_QA_Tests)
  * [7 API Changes](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#API_Changes)
  * [8 WX is Gone](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#WX_is_Gone)
  * [9 Update PyBombs Recipe](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#Update_PyBombs_Recipe)
  * [10 Still not Working?](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#Still_not_Working?)
  * [11 Notes](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide#Notes)


## Development Model
GNU Radio recently changed the development model. The longstanding next branch was finally merged in to master. From now on, there will only be a maint-X.Y and a master branch. Releasing 3.8 basically means master will be spin off maint-3.8 with the 3.8.0.0 release commit. After that API-breaking changes for 3.9 will end up in master and 3.8 will be maintained in maint-3.8. 
We suggest, you adapt a similar model in your OOT. That means your maint-3.8 branch is compatible with GNU Radio maint-3.8 and your master branch is compatible GNU Radio master. Trying to maintain a single branch that works with all future, present, and past versions of GNU Radio versions is mess and you’d have to differentiate between too many cases (API changes, XML vs YAML GRC bindings, Python 2 vs 3, QT4 vs QT5, log4cpp support or not, CPPUnit or Boost unit tests, etc.). 
Starting your maint-3.7 branch and tagging the current state as the last one that’s compatible with GR 3.7 could be done with: 

```
git tag v3.7
git checkout -b maint-3.7

```

## Install GNU Radio 3.8
Start by installing the most recent version of GNU Radio. At the moment, there are no pre-built packages, so checkout the most recent master branch and compile from source. This also updates gr_modtool, which we will use in the following. 
  

## CMake Updates
GNU Radio 3.8 comes with changes to the CMake build system. Going forward CMake syntax in GNU Radio core adheres to "Modern CMake". This means no functions setting global variables like `include_directories` and `add_definition` will be used. Instead only functions which operate on targets and are prefixed with `target_` are used. A reference on "Modern CMake" can be found [here](https://cliutils.gitlab.io/modern-cmake/). 
### Summary
  * `find_package` is only required for dependencies you directly depend on. E.g. Boost, log4cpp will be handled by the GNU Radio Cmake configuration and linker flags and defines are provided transitively.
  * GNU Radio CMake adheres to all CMake policy changes up to CMake 3.11. Remove `cmake_policy` function calls which set CMake policy to old.
  * `GR_` variables won't pollute your environment, only the minimum necessary variables are defined by the GNU Radio CMake configuration
  * CppUnit in GNU Radio core has been removed and replaced by Boost UTF, same can be done for your OOT. If you like you can still keep CppUnit though.
  * Functions setting global include paths and link_directories should be removed
  * Custom CMake modules previously located in cmake/Modules in your tree, especially CMakeParseArgumentsCopy.cmake, FindCppUnit.cmake FindGnuradioRuntime.cmake GrPlatform.cmake GrTest.cmake
  * `target_link_libraries` can use `gnuradio::gnuradio-$component` instead of `${GNURADIO_ALL_LIBRARIES`
  * Instead of `include_directories(${CMAKE_CURRENT_SOURCE_DIR)` a special cased target_include_directories can specify include directories for build time and for install time


The easiest way to update CMake for your OOT might be to use gr_modtool and generate a module with the same name and then copy your source files over. If you have multiple GR versions installed, make sure that gr_modtool uses the correct module template, which is defined in the [modtool] section in ~/.gnuradio/config.conf. 
### Look at Examples
If you have doubts about any of the following steps, you might want to have a look at OOTs that were already ported. We know of: <https://github.com/ghostop14/gr-grnet> (master branch) 
Updating, for example, gr-foo could be done with 

```
gr_modtool newmod foo

```

To see how OOT blocks are handled, you can create a C++ and a Python block to see examples of how they are integrated into build system. 

```
cd gr-foo
gr_modtool add

```

Then use a diff tool like meld to compare the differences and apply changes as needed. 

```
meld <path-to-your-actual-gr-foo-module> gr-foo

```

### CMake Modules
One thing you might notice is that there are now less FindXXX.cmake modules needed in your OOT (under cmake/Modules). If your module did not require any custom CMake changes (which is likely), you can just go ahead and delete these modules. They now installed as part of GNU Radio and are available in your CMAKE_MODULE_PATH once GNU Radio was loaded through find_package(Gnuradio [...]). 
Note: In the unlikely case that you module requires custom cmake modules, there’s one tricky bit: At the top of your main CMakeLists.txt file the local cmake modules are put at the beginning of the search path and, therefore, take precedence over the ones installed on your system. However, once GNU Radio is loaded, it will put its cmake path at the front, potentially shadowing local modules. If that’s a problem, put your local path again to the front after GNU Radio is loaded like so: 

```
#make sure our local CMake Modules path comes first
list(INSERT CMAKE_MODULE_PATH 0 ${CMAKE_SOURCE_DIR}/cmake/Modules)

```

### GNU Radio Components
GNU Radio now uses CMake components. So if your OOT depends on GNU Radio modules other than runtime and pmt, you have to import these components through the find_package call. For example, change: 

```
set(GR_REQUIRED_COMPONENTS RUNTIME PMT BLOCKS ANALOG FILTER)
find_package(Gnuradio  “3.7” REQUIRED)

```

to: 

```
find_package(Gnuradio "3.8" REQUIRED COMPONENTS blocks analog filter fft)

```

Moreover, and differently to 3.7, `lib/CMakeLists.txt` should also be changed, in particular the call to `target_link_libraries`. For example, it could look something like this: 

```
target_link_libraries(gnuradio-tempest gnuradio::gnuradio-runtime gnuradio::gnuradio-blocks gnuradio::gnuradio-fft gnuradio::gnuradio-filter Volk::volk)

```

Note: Currently the build system doesn’t resolve internal dependent components automatically. The filter component, for example, depends on fft, so it had to be added manually to the list of components above. 
## Python Blocks
In python/__init__.py each block must be changed to relative import by putting a period before the block name 

```
# import any pure python here
from .foo import foo

```

## Versioning
Version your OOT similar to GR? 
## Porting QA Tests
## API Changes
At this point, you should be able to recompile the module. Here you can test if you run into any issues with C++ API changes. 
XML to YAML Conversion The GNU Radio Companion (GRC) bindings are no longer defined in XML but in YAML. To ease the transition, GNU Radio comes with a converter that should do 95% of the work. Make sure your grc bindings are in the grc folder and use the <module name>_<block name>.xml naming scheme. Then, from the root directory of your module, do: 

```
gr_modtool update --complete

```

If there are any problems reported you’ll have to fix the by hand. 
Todo: link to examples and documentation 
Don’t forget to update the file names in the grc/CMakeLists.txt file to make sure that the YAML files are installed. 
## WX is Gone
GNU Radio 3.8 drops support for WX GUI widgets in favor of QT. If you are using WX widgets in your flow graph, you’d have to replace them with the corresponding QT widgets. For most flow graphs that should be a straight forward drop in replacement. 
## Update PyBombs Recipe
If your OOT module is listed in a receipt file for PyBombs, please update, if needed, your Manifest.md file and the receipt. 
## Still not Working?
If you have any issues porting your OOT, please join our Slack channel or write to the GNU Radio mailing list. We are happy to help and to improve this guide. 
## Notes
Make sure gr-dev package installs cmake modules Delete unnecessary cmake modules in OOT Make sure local CMake module path is not shadowed by GR. (does GR have to insert cmake to the front?) use grcc (through cmake command) to build and install hier blocks gr_modtool uses ENABLE_GRC how, where, when is this supposed to be set? There does not seem to be a GRC component that can be imported. Where is Gnuradio_FIND_COMPONETS supposed to be set. It required to make targets available in OOTs. / Discuss What is the PyBombs way to migrate GR and OOTs to 3.8? Recommendation for branches/development concept 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide&oldid=13106](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide&oldid=13106)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [3.8](https://wiki.gnuradio.org/index.php?title=Category:3.8 "Category:3.8")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=GNU+Radio+3.8+OOT+Module+Porting+Guide "You are encouraged to log in; however, it is not mandatory \[o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide "View the content page \[c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:GNU_Radio_3.8_OOT_Module_Porting_Guide&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide)
  * [View source](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide&action=edit "This page is protected.
You can view its source \[e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide&action=history "Past revisions of this page \[h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/GNU_Radio_3.8_OOT_Module_Porting_Guide "A list of all wiki pages that link here \[j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/GNU_Radio_3.8_OOT_Module_Porting_Guide "Recent changes in pages linked from this page \[k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide&oldid=13106 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.8_OOT_Module_Porting_Guide&action=info "More information about this page")


  * This page was last edited on 27 April 2023, at 09:25.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


