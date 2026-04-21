# GNU Radio 3.9 OOT Module Porting Guide
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#searchInput)
The major changes in the (in-progress) GNU Radio 3.9 release that will impact OOTs are: 
  * C++ modernization (C++11/14?)
  * Replacement of SWIG with Pybind11
  * Cleanup of filter and fft APIs


  

## Contents
  * [1 Porting Guide](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Porting_Guide)
    * [1.1 Versioning Your shared object files](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Versioning_Your_shared_object_files)
  * [2 Details](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Details)
    * [2.1 C++ Modernization](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#C++_Modernization)
    * [2.2 Pybind11 Python Bindings](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Pybind11_Python_Bindings)
      * [2.2.1 Dependencies](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Dependencies)
      * [2.2.2 Components](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Components)
        * [2.2.2.1 blockname_python.cc](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#blockname_python.cc)
        * [2.2.2.2 python_bindings.cc](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#python_bindings.cc)
          * [2.2.2.2.1 Comment Block](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Comment_Block)
      * [2.2.3 Workflow](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Workflow)
        * [2.2.3.1 Out-of-Tree modules](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Out-of-Tree_modules)
      * [2.2.4 Docstrings](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Docstrings)
      * [2.2.5 OOT Migration](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#OOT_Migration)
      * [2.2.6 Caveats](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Caveats)
        * [2.2.6.1 Using default values](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Using_default_values)
      * [2.2.7 Troubleshooting](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Troubleshooting)
        * [2.2.7.1 Unable to find pydoc.h](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Unable_to_find_pydoc.h)
        * [2.2.7.2 TypeError: 'modulename_python.blockname' object is not subscriptable](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#TypeError:_'modulename_python.blockname'_object_is_not_subscriptable)
        * [2.2.7.3 Module is Empty](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Module_is_Empty)
    * [2.3 CMakeLists.txt changes to fix OOT module testing](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#CMakeLists.txt_changes_to_fix_OOT_module_testing)
    * [2.4 Python forecast() API change](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide#Python_forecast\(\)_API_change)


# Porting Guide
Porting from 3.8 to 3.9 can be achieved most simply by creating a new OOT module (with the same name as the 3.8 OOT but in a different directory), then performing some manual steps 
1. Use the 3.9 gr_modtool to generate a module with the same name (in another directory) 
2. Copy the python folder from 3.9 OOT into your 3.8 OOT 
3. (in 3.8 OOT) Add the bindings directory to the python directory CMakeLists 

```
   ./python/CMakeLists.txt  → add the line: 
           add_subdirectory(bindings)

```

4. (in 3.8 OOT) Call gr_modtool bind for each block in your OOT 

```
NOTE: If you are doing more than just make function in your public header, e.g. setters/getters or other callback functions, be sure to have pygccxml set up

```

5. c++ blocks that have python QA will need the import statements updated in that QA. 
6. python/bindings/python_bindings.cc needs to be updated for all C++ blocks (in two places!) 
7. Replace occurrences of boost::shared_ptr<blockname> with std::shared_ptr<blockname>
8. Though not necessary, boost::bind instances for message port handlers can be replaced with lambda functions for performance and simplicity as well as consistency with the in-tree modules 
  * The instructions below are primarily intended for C++ projects. Python projects may be slightly different.


9. Merge 3.8 /lib files (.cc, .h, etc) with the 3.9 lib file prototypes constructed by gr_modtool into 3.9 /lib. 
10. Copy the 3.8 /python/binding and /docstring files into 3.9 
11. Merge the 3.8 /grc yml files with the 3.9 prototypes into 3.9/grc 
12. Merge the 3.9 /include files with the 3.9 prototypes into 3.9/include. 
13. Edit 3.9 /lib/CMakeLists.txt to add all the targets needed as specified in the 3.8 /lib/CMakeLists.txt file. 
14. bind will compare date/time stamps of the files. If the target bindings are out of date, manually rebind each 3.9 block with gr_modtool bind block. 
Porting from 3.7 to 3.9 should follow the 3.8 Porting Guide, but basically create a new OOT using 3.9 and add blocks from there, then copy in code. 
## Versioning Your shared object files
You may want to edit the /lib/CMakeList.txt file in order to set a version. The default VERSION_PATCH is set to git, you may want to edit it to 0 for your first version. Then when you need to push out a modified version remember to edit the version numbers before building. If you leave the VERSION_PATCH at git the install directory may eventually become littered with old libgnuradio-yourproject.so.git-commit-number files and soft links. 
# Details
## C++ Modernization
Boost shared pointers have been replaced with std:: shared pointers and memory management. At the top level of each block, the instantiation will need to change, e.g. 
In include/blockname_xx.h: 

```
typedef std::shared_ptr<blockname_xx> sptr;

```

Inbound message ports receive an update too. We move from boost::function to std::function. This affects how message handlers are registered. The preferred style is to use lambdas which is already compatible with GNU Radio 3.8: 

```
set_msg_handler(pmt::mp("message"), [this](pmt::pmt_t msg) { this->handle_msg(msg); });

```

## Pybind11 Python Bindings
As of the GNU Radio 3.9 release, python bindings are handled using pybind11, which is inherently different than they were in previous releases 
### Dependencies
  * pybind11 > 2.4.3 <https://pybind11.readthedocs.io/>
    * pip does not provide the proper cmake (<https://github.com/pybind/pybind11/issues/1379>)
    * gnuradio 3.9 was built using pybind11 version 2.5.0. Your OOT should be built against that same version. (Version 2.4.3 will not work).
    * The Ubuntu 20.04 package manager has referenced different versions of pybind11-dev, 2.4.3 and 2.5.0. It seems to have reverted to 2.4.3 as of April 2022.


You can identify what version is or would be installed with: 

```
apt policy pybind11-dev

```

If it identifies 2.5.0 then install with: 

```
sudo apt install pybind11-dev

```

  *     * Else this will need to be installed from source as 2.5.0 is not the supplied version with package managers


```
curl -Lo pybind11.tar.gz https://github.com/pybind/pybind11/archive/v2.5.0.tar.gz 
mkdir pybind11 && tar xzf pybind11.tar.gz -C pybind11 --strip-components=1 && cd pybind11
mkdir build && cd build 
cmake .. -DCMAKE_BUILD_TYPE=Release -DPYBIND11_TEST=OFF 
make
sudo make install 

```

  * pygccxml <https://pygccxml.readthedocs.io/en/develop/install.html>
    * This is an optional dependency and basic functionality for OOT generation can be performed without pygccxml
    * It is required for automatically generating bindings for most of the GR source tree


### Components
Python bindings are contained in the `python/.../bindings` directory 

```
./python
└── module_name
    ├── bindings
    │   ├── blockname1_python.cc
    │   ├── blockname2_python.cc
    │   ├── CMakeLists.txt
    |   ├── python_bindings.cc
    │   ├── docstrings
    │   │   ├── blockname1_pydoc_template.h
    │   │   ├── blockname1_pydoc_template.h

```

The bindings for each block exist in blockname_python.cc under the `python/bindings` directory. Additionally, a template header file for each block that is used as a placeholder for the scraped docstrings lives in the `docstrings/` dir 
#### blockname_python.cc
This is the class function enum variable bindings for everything that needs to be exposed through the Python API 
#### python_bindings.cc
The structure of this file is 

```
// Headers for binding functions
/**************************************/
/* The following comment block is used for
/* gr_modtool to insert function prototypes
/* Please do not delete
/**************************************/
// BINDING_FUNCTION_PROTOTYPES(

void bind_blockname1(py::module&);

// ) END BINDING_FUNCTION_PROTOTYPES


PYBIND11_MODULE(module_name__python, m)
{

{
    // Initialize the numpy C API
    // (otherwise we will see segmentation faults)
    init_numpy();

    // Allow access to base block methods
    py::module::import("gnuradio.gr");
    /**************************************/
    /* The following comment block is used for
    /* gr_modtool to insert binding function calls
    /* Please do not delete
    /**************************************/
    // BINDING_FUNCTION_CALLS(
    bind_blockname1(m);
    // ) END BINDING_FUNCTION_CALLS
}

```

##### Comment Block
Each block binding file contains an automatically generated and maintained comment block that informs CMake when the bindings are out of sync with the header file they refer to, and what to do about it 

```
/***********************************************************************************/
/* This file is automatically generated using bindtool and can be manually edited  */
/* The following lines can be configured to regenerate this file during cmake      */
/* If manual edits are made, the following tags should be modified accordingly.    */
/* BINDTOOL_GEN_AUTOMATIC(0)                                                       */
/* BINDTOOL_USE_PYGCCXML(0)                                                        */
/* BINDTOOL_HEADER_FILE(basic_block.h)                                             */
/* BINDTOOL_HEADER_FILE_HASH(549c06530e2afdf6f2c989017cb5f36e)                     */
/***********************************************************************************/

```

`BINDTOOL_GEN_AUTOMATIC`: Many times for complex in-tree blocks, the automated tools are not entirely sufficient to generate all of the bindings in an automated fashion. In this case, the flag should be set to 0, and the bindings need to be updated manually. If the flag is set to 1, CMake will override the binding file _in the source tree_ when it detects out of sync bindings. This should only be done in simple cases. 
`BINDTOOL_USE_PYGCCXML`: Currently there are limitations on the amount of code generation that can be accomplished without the `pygccxml` dependency. If a block needs pygccxml for the bindings to be properly generated automatically, this should be set to `1`
`BINDTOOL_HEADER_FILE`: The header file that bindings are based on, filename only 
`BINDTOOL_HEADER_FILE_HASH`: The MD5 hash of the header file that the bindings were built on 
### Workflow
#### Out-of-Tree modules
The steps for creating an out of tree module with pybind11 bindings are as follows: 
  1. Use `gr_modtool` to create an out of tree module and add blocks



```
gr_modtool newmod foo
gr_modtool add bar

```

  1. Update the parameters or functions in the public include file and rebind with `gr_modtool bind bar`


**NOTE** : without pygccxml, only the make function is currently accounted for, similar to `gr_modtool makeyaml`
If the public API changes, just call `gr_modtool bind [blockname]` to regenerate the bindings 
When the public header file for a block is changed, CMake will fail as it checks the hash of the header file compared to the hash stored in the bindings file until the bindings are updated 
  1. Build and install


### Docstrings
If Doxygen is enabled in GNU Radio and/or the OOT, Docstrings are scraped from the header files, and placed in auto-generated `[blockname]_pydoc.h` files in the build directory on compile. Generated templates (via the binding steps described above) are placed in the `python/bindings/docstrings` directory and are used as placeholders for the scraped strings 
Upon compilation, docstrings are scraped from the module and stored in a dictionary (using `update_pydoc.py scrape`) and then the values are substituted in the template file (using `update_pydoc.py sub`) 
  

### OOT Migration
The easiest way to migrate an OOT to 3.9 is to use `gr_modtool` to create a new OOT, use `gr_modtool add` to create the blocks, and copy code from the previous OOT. 
Steps to do this without regenerating a new module are TBD 
### Caveats
Pybind11 bound methods do not implicitly convert int to enum, so blocks that take enum as input, must have either "raw" or "enum" in the grc yml definition of the block. "Raw" will allow the value to be changed by another variable in the flowgraph. 
Block inheritance must be specified completely in the python bindings in order to use the inherited methods. For instance, if a block inherits from sync_block, both block and basic_block must be included in the inheritance specification of the class: 

```
    py::class_<atsc_interleaver,
               gr::sync_block,
               gr::block,
               gr::basic_block,
               std::shared_ptr<atsc_interleaver>>(
        m, "atsc_interleaver", D(atsc_interleaver)) 

```

  
If your OOT module uses types from or its classes derive from another gr module, it is necessary in `python_bindings.cc` to specify these modules. 
For instance, since all OOT modules require the base block types, there is a line `py::module::import("gnuradio.gr");`. 
If you wanted to utilize `digital::constellation` objects in your OOT, it would be necessary to add `py::module::import("gnuradio.digital");` so that pybind knows to use the bindings already compiled into gnuradio.digital for the constellation objects 
If your OOT module uses other classes as parameter, you must either setup a python binding for this class or use an existing one. An example, how to setup a binding for the QWidget class of QT5 used in gr-qtgui, can be found here[[1]](https://github.com/gnuradio/gnuradio/blob/master/gr-qtgui/python/qtgui/bindings/QWidget_python.cc)
If you want to use this binding in your OOT module, you have to import it. This has to be done in the python_bindings.cc inside the **PYBIND11_MODULE** macro. In case of the QWidget usage this would be `py::module::import("gnuradio.qtgui.qtgui_python");`
#### Using default values
For standard types like int,float, etc. you can set default values as usual. `gr_modtool bind blockname` will setup the correct bindings in blockname_python.cc. 
But if you use something like ` QWidget* parent = NULL ` gr_modtool generates code like `py::arg("parent") = __null, `
But at this point pybind does not know the type of parent, so you have to modify the binding code and cast the type. `py::arg("parent") = (QWidget *) __null, `
Another way to come across this issue is to modify your header file from ` QWidget* parent = NULL ` to ` QWidget* parent = nullptr `
pybind generates `py::arg("parent") = nullptr, `
which will be handled correctly 
### Troubleshooting
#### Unable to find pydoc.h

```
fatal error: blockname_pydoc.h: No such file or directory
   28 | #include <blockname_pydoc.h>
      |          ^~~~~~~~~~~~~~~~~~~~~~~~~
compilation terminated.

```

blockname_pydoc.h is generated during compilation based on the template in the docstring directory. When the block is first created in blocktool, this template does not exist. Run `gr_modtool bind` inside `build/gnuradio-runtime/python/gnuradio/gr` to generate the appropriate template used as a placeholder for the scraped docstrings 
Also, the scraping of docstrings only takes place at CMake time, so it may be necessary to do a `make clean` to re-trigger the scraping 
You can also try 

```
rm python/bindings/docstring_status

```

which will reset the docstring scraping target in cmake and re-copy the docstring templates 
#### TypeError: 'modulename_python.blockname' object is not subscriptable
This is caused by an incomplete inheritance chain specified in the binding declaration of the block. 
Instead of 

```
   py::class_<blockname,
              std::shared_ptr<blockname>>(m, "blockname", D(blockname))

```

Try something like (taking into account your block type) 

```
   py::class_<blockname,
              gr::sync_block,
              gr::block,
              gr::basic_block,
              std::shared_ptr<blockname>>(m, "blockname", D(blockname))

```

#### Module is Empty
This is usually caused by linker errors that prevent the binding module from being loaded. When you see something like: 

```
   >>> import foo
   >>> dir(foo)
   ['__builtins__', '__cached__', '__doc__', '__file__', '__loader__', '__name__', '__package__', '__path__', '__spec__']

```

But you expect foo to have other C++ blocks, try loading the pybind module separately 

```
   cd build/python/bindings
   python3
   >>> import foo_python
   [linker error should be evident here in mangled symbol name]

```

## CMakeLists.txt changes to fix OOT module testing
To fix testing in existing OOT modules, add the following to your `python/CMakeLists.txt` file (replace `howto` with your module name in three places): 

```
   # Create a module directory that tests can import. It includes everything
   # from `python/` and the built bindings shared lib.
   add_custom_target(
     copy_module_for_tests ALL
     COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_CURRENT_SOURCE_DIR}
             ${CMAKE_BINARY_DIR}/test_modules/howto/
     COMMAND
       ${CMAKE_COMMAND} -E copy_directory ${CMAKE_CURRENT_BINARY_DIR}/bindings/
       ${CMAKE_BINARY_DIR}/test_modules/howto/
     DEPENDS howto_python)

```

  
New modules already include this code. For more information, [see the relevant PR](https://github.com/gnuradio/gnuradio/pull/5279). 
  

## Python `forecast()` API change
Starting in GNU Radio 3.9, the API for the `forecast()` method of `basic_block` has changed (see diff [here](https://github.com/gnuradio/gnuradio/commit/ba16fdf0a0e9052163a5dd00b5927b2eccc0683f#diff-1f0d79983918943595d54e4654b719cfb42a3eb42eb77ab8a274caed1ce5722cL233-R165)). Prior to GNU Radio 3.9, the output for the `forecast()` method was written to the elements of the second argument named `ninput_items_required`, which was a list of integers. Now the second argument is an integer named `ninputs` which gives the number of input ports. The body of the method is now expected to generate a list of integers representing `ninput_items_required` and return it. If you do not make the required update you will enjoy seeing the following error message when `forecast()` is called: 

```
   TypeError: 'int' object does not support item assignment

```

Retrieved from "[https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide&oldid=13105](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide&oldid=13105)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [3.9](https://wiki.gnuradio.org/index.php?title=Category:3.9 "Category:3.9")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=GNU+Radio+3.9+OOT+Module+Porting+Guide "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:GNU_Radio_3.9_OOT_Module_Porting_Guide&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide)
  * [View source](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/GNU_Radio_3.9_OOT_Module_Porting_Guide "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/GNU_Radio_3.9_OOT_Module_Porting_Guide "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide&oldid=13105 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=GNU_Radio_3.9_OOT_Module_Porting_Guide&action=info "More information about this page")


  * This page was last edited on 27 April 2023, at 09:18.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


