# UsingVSCode
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=UsingVSCode#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=UsingVSCode#searchInput)
## Contents
  * [1 Prerequisites](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Prerequisites)
    * [1.1 Extensions for VSCode users](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Extensions_for_VSCode_users)
    * [1.2 Extensions for VSCodium users](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Extensions_for_VSCodium_users)
    * [1.3 Extension pack](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Extension_pack)
  * [2 Setting up for editing GNU Radio modules](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Setting_up_for_editing_GNU_Radio_modules)
    * [2.1 Step 1: Discover GNURadio](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Step_1:_Discover_GNURadio)
    * [2.2 Step 2: Open the module's directory](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Step_2:_Open_the_module's_directory)
    * [2.3 Step 3: Use the Module Explorer](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Step_3:_Use_the_Module_Explorer)
    * [2.4 Step 4: Edit the module's source code](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Step_4:_Edit_the_module's_source_code)
    * [2.5 Step 5: Build and test the module](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Step_5:_Build_and_test_the_module)
    * [2.6 Step 6 (Optional): Use additional features](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Step_6_\(Optional\):_Use_additional_features)
  * [3 Source level debugging](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Source_level_debugging)
    * [3.1 Launching from VSCode](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Launching_from_VSCode)
    * [3.2 Attaching to Process](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Attaching_to_Process)


# Prerequisites
Tools used in this tutorial: 
  * GNU Radio v3.10 or newer
  * [Visual Studio Code](https://code.visualstudio.com) or [VSCodium](https://vscodium.com)


The following instructions have been tested with GNU Radio v3.10.9 and VSCodium v1.92 in Ubuntu 24.04 LTS. 
## Extensions for VSCode users
  * "[Python](https://marketplace.visualstudio.com/items?itemName=ms-python.python)" - for working with Python code and debugging it;
  * "[C/C++](https://marketplace.visualstudio.com/items?itemName=ms-vscode.cpptools)" - for working with C++ code and debugging it;
  * "[CMake Tools](https://marketplace.visualstudio.com/items?itemName=ms-vscode.cmake-tools)" - for working with the CMake project, using tasks to configure, build and test it;
  * "[GNU Radio Integration](https://marketplace.visualstudio.com/items?itemName=ivannovak1004.gnuradio-integration)" - for working with GNU Radio modules, an interface to gr_modtool;
  * "[YAML](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml)" - for working with GRC files manually.


## Extensions for VSCodium users
  * "[Python](https://open-vsx.org/extension/ms-python/python)" - for working with Python code and debugging it;
  * "[clangd](https://open-vsx.org/extension/llvm-vs-code-extensions/vscode-clangd)" - language server for working with C++ code;
  * "[CodeLLDB](https://open-vsx.org/extension/vadimcn/vscode-lldb)" or "[Native Debug](https://open-vsx.org/extension/webfreak/debug)" - for debugging C++ code;
  * "[CMake Tools](https://open-vsx.org/extension/ms-vscode/cmake-tools)" - for working with the CMake project, using tasks to configure, build and test it;
  * "[GNU Radio Integration](https://open-vsx.org/extension/AsriFox/gnuradio-integration)" - for working with GNU Radio modules, an interface to gr_modtool;
  * "[YAML](https://open-vsx.org/extension/redhat/vscode-yaml)" - for working with GRC files manually.


## Extension pack
To get the extensions for editing, you can install "GNU Radio development pack" ([VSCode](https://marketplace.visualstudio.com/items?itemName=asrifox.gnuradio-extension-pack), [VSCodium](https://open-vsx.org/extension/AsriFox/gnuradio-extension-pack)). 
You will still need to install "CMake Tools" and all C++ extensions separately. 
# Setting up for editing GNU Radio modules
"GNURadio Integration" extension provides a graphical interface to _gr_modtool_ within VSCode. It allows you to: 
  * Create new OOT modules;
  * Query information about the module;
  * Discover the module's blocks in the **OOT Module Explorer** ;
  * Add, rename and remove blocks;
  * Create or update Python bindings to C++ blocks;
  * Update XML block definitions to YAML.


Additionaly, you can: 
  * Launch **GNURadio Companion** from the command palette;
  * Open the current GRC flowgraph file in **GNURadio Companion** ;
  * Compile the current GRC flowgraph into an application;
  * Run the current GRC flowgraph.


To properly set up the development environment, follow the procedure: 
### Step 1: Discover GNURadio
GNURadio can be installed system-wide (with the distribution's package manager, e.g. _apt_) or locally. You can check the installation prefix with "gnuradio-config-info --prefix" command. 
For [Conda-based installations](https://wiki.gnuradio.org/index.php?title=CondaInstall "CondaInstall") the prefix is related to the virtual environment, so it can be discovered using "Select Interpreter" command of the "Python" extension: 
[!["Select Interpreter" dialog](https://wiki.gnuradio.org/images/e/e0/Vscode_python_interpreters.png)](https://wiki.gnuradio.org/index.php?title=File:Vscode_python_interpreters.png ""Select Interpreter" dialog")
If there is a specific Python interpreter or environment you would like to use by default, you can specify **Default interpreter** and **Default PYTHONPATH** in the _Settings_. 
In Linux, the default system-wide install prefix is _/usr_ , but it can be defined by the user in [source builds](https://wiki.gnuradio.org/index.php?title=LinuxInstall#From_Source "LinuxInstall") (default: _/usr/local_). In [native Windows builds](https://wiki.gnuradio.org/index.php?title=WindowsInstall#Installation_Options "WindowsInstall"), the install prefix is always user-defined. To provide the editor with the required path to use GNURadio scripts, you will need to specify **GNURadio Prefix** in the _Settings_. 
### Step 2: Open the module's directory
You can create a new OOT module from VSCode using **Create OOT Module** command. It will prompt you for a name for the new module, and then prompt you to select the directory, in which the new module project will be created. For example, if you enter "sample" as the name and select _~/Documents_ as the location, the module project will be created in _~/Documents/sample_. After that, the editor will ask if you want to open this module's directory in the current editor. 
Alternatively, modules can be created with _gr_modtool_ (see [Creating an OOT Module](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool#Creating_an_OOT_Module "Creating Python OOT with gr-modtool")). You can open their directories manually, using **Open Directory** command (Ctrl+K -> Ctrl+O). The same is applicable to existing OOT modules. 
### Step 3: Use the Module Explorer
[![](https://wiki.gnuradio.org/images/thumb/1/15/Vscode_module_tree.png/300px-Vscode_module_tree.png)](https://wiki.gnuradio.org/index.php?title=File:Vscode_module_tree.png)Module Explorer view
If an OOT module is detected in the workspace, the **GNURadio Module** tree view will appear in the Explorer container in the sidebar. All blocks within that module are presented with the corresponding files: 
  * YAML GRC block definition;
  * Python implementation source or Python blocks;
  * C++ header and implementation source for C++ blocks;
  * Python and C++ QA (unit testing) source files.


Click the "+" button in the Module Explorer header to create a new block. The "Refresh" button is used to refresh the view after external changes. 
Context menus (Right mouse click) for blocks contain the module manipulation commands: 
  * Rename block;
  * Remove block;
  * Convert XML to YAML (if XML definition is present);
  * Create Python bindings (if the block's source code is C++).


### Step 4: Edit the module's source code
Related tutorials: [Creating Your First Block](https://wiki.gnuradio.org/index.php?title=Creating_Your_First_Block "Creating Your First Block"), [Creating Python OOT with gr-modtool](https://wiki.gnuradio.org/index.php?title=Creating_Python_OOT_with_gr-modtool "Creating Python OOT with gr-modtool"), [Creating C++ OOT with gr-modtool](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool "Creating C++ OOT with gr-modtool"). 
To enable syntax highlighting, autocompletion, documentation and other code editing features, you need to install the language server extension for the corresponding language: 
  * For Python, install "Python" extension.
  * For C++, install "C/C++" or "clangd" extension.


### Step 5: Build and test the module
You can [build and install](https://wiki.gnuradio.org/index.php?title=Creating_C%2B%2B_OOT_with_gr-modtool#Compiling_and_Installing_the_Block "Creating C++ OOT with gr-modtool") the module using CMake either from a terminal or with "CMake Tools" extension. Build flags can be set in the command line or in _build/CMakeCache.txt_. 
You can perform unit testing for your blocks with QA functions (_python/module/qa_block.py_ , _lib/qa_block.cc_). **Testing** panel provides an overview of all test fixtures in the module. 
After installing the module you can use your blocks in flowgraphs. It is considered a good practice to add examples for your module to demonstrate the usage of your blocks. Example flowgraphs can also be used for [#Source level debugging](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Source_level_debugging). 
### Step 6 (Optional): Use additional features
Some features of "GNURadio Integration" require presence of other extensions. 
  * "Python" extension is required to discover GNURadio installation in a Python virtual environment using "Select Interpreter" command.
  * "YAML" extension is required to use syntax highlighting and validation in GRC flowgraph files and YAML GRC block definition files. 
    * "YAML Embedded Languages" extension can provide additional highlighting for embedded code blocks (e.g. "make" templates).


# Source level debugging
The procedure described below allows source level debugging within VSCode through the use of the module's QA functions or flowgraphs. A debugger extension is required (see [#Extensions for VSCode users](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Extensions_for_VSCode_users) and [#Extensions for VSCodium users](https://wiki.gnuradio.org/index.php?title=UsingVSCode#Extensions_for_VSCodium_users)). 
To debug your blocks using flowgraphs: 
  1. Configure, build and install it using CMake; 
     * Be sure to set build type to "Debug" at the configure step as a cache variable (_build/CMakeCache.txt_) or as a CMake command flag: 
```
cmake .. -DCMAKE_BUILD_TYPE=Debug
```

  2. Create and compile a flowgraph that uses your module;
  3. Run the compiled flowgraph with a debugger.


### Launching from VSCode
The simplest way to achieve source level debugging is to launch the top level flowgraph from VSCode. To do that: 
  1. Set breakpoints in code that you want to hit.
  2. In the **Debug** panel (_Ctrl-Shift-D_), create a launch configuration; 
     * Create a configuration with "request": "launch";
     * Set "type" according to the target block type: 
       * "debugpy" for Python blocks;
       * "cppdbg" for C++ blocks with "C/C++" extension;
       * "lldb" for C++ blocks with "CodeLLDB" extension;
       * "gdb", "lldb-mi" or "mago-mi" for C++ blocks with "Native Debug" extension;
     * Set "program" as your Python interpreter, e.g. "/usr/bin/python3";
     * Set "args" as your compiled flowgraph: `["-u", "/path/to/flowgraph.py"]`;
     * Alternatively, if your flowgraph compiles to C++, set "program" as your compiled and built flowgraph: "/path/to/flowgraph/build/flowgraph".     Complete _launch.json_ example:     

```
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "(gdb) Launch",
            "type": "cppdbg",
            "request": "launch",
            "program": "/usr/bin/python3",
            "args": ["-u", "/path/to/flowgraph.py"],
            "stopAtEntry": false,
            "cwd": "${workspaceFolder}",
            "environment": [],
            "externalConsole": false,
            "MIMode": "gdb",
            "setupCommands": [
                {
                    "description": "Enable pretty-printing for gdb",
                    "text": "-enable-pretty-printing",
                    "ignoreFailures": true
                }
            ]
        },
    ]
}

```

  3. Start a debugging session with **"Debug: Start Debugging"** command (F5).
  4. The debugger will then land on your breakpoints.


### Attaching to Process
Another alternative is to attach to an already running process. This is useful for more complicated long-running systems and flowgraphs. To do that: 
  1. Set breakpoints in code that you want to hit.
  2. In the **Debug** panel (_Ctrl-Shift-D_), create a launch configuration; 
     * Create a configuration with "request": "attach";
     * Set "type" according to the target block type: 
       * "debugpy" for Python blocks;
       * "cppdbg" for C++ blocks with "C/C++" extension;
       * "lldb" for C++ blocks with "CodeLLDB" extension;
       * "gdb", "lldb-mi" or "mago-mi" for C++ blocks with "Native Debug" extension;
     * Set "program" as your Python interpreter, e.g. "/usr/bin/python3";
     * Set "processId" as "${command:pickProcess}" to pick from a list of running Python processes;     Complete _launch.json_ example:     

```
{
    "version": "0.2.0",
    "configurations": [
        { 
            "name": "(gdb) Attach",
            "type": "cppdbg",
            "request": "attach",
            "program": "/usr/bin/python3",
            "processId": "${command:pickProcess}",
            "MIMode": "gdb",
            "setupCommands": [
                {
                    "description": "Enable pretty-printing for gdb",
                    "text": "-enable-pretty-printing",
                    "ignoreFailures": true
                }
            ]
        },
    ]
}

```

  3. Launch your flowgraph from a terminal; 
     * If necessary, delay the startup in code (e.g. by adding `sleep` or `raw_input` and give the debugger time to attach:     

```
 if __name__ == "__main__":
     if GDB_ATTACH:
         print(f"Blocked waiting or GDB attach (pid = {os.getpid()}). "
               "Press ENTER after GDB is attached.", flush=True)
         raw_input()
     main()

```

  4. Start a debugging session with **"Debug: Start Debugging"** command (F5).
  5. You will be prompted to select the process to attach to; type "python" to filter Python instances and select the one running your flowgraph.
  6. The debugger will then attach and land on your breakpoints.


Retrieved from "[https://wiki.gnuradio.org/index.php?title=UsingVSCode&oldid=14584](https://wiki.gnuradio.org/index.php?title=UsingVSCode&oldid=14584)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=UsingVSCode "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=UsingVSCode "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:UsingVSCode "Discussion about the content page \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=UsingVSCode)
  * [View source](https://wiki.gnuradio.org/index.php?title=UsingVSCode&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=UsingVSCode&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/UsingVSCode "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/UsingVSCode "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=UsingVSCode&oldid=14584 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=UsingVSCode&action=info "More information about this page")


  * This page was last edited on 4 September 2024, at 08:59.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


