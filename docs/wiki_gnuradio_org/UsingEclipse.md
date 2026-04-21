# UsingEclipse
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=UsingEclipse#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=UsingEclipse#searchInput)
## Contents
  * [1 Building and source level debugging OOT C++ modules with eclipse](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Building_and_source_level_debugging_OOT_C++_modules_with_eclipse)
    * [1.1 PREREQUISITES:](https://wiki.gnuradio.org/index.php?title=UsingEclipse#PREREQUISITES:)
    * [1.2 PROCEDURE:](https://wiki.gnuradio.org/index.php?title=UsingEclipse#PROCEDURE:)
      * [1.2.1 Step 1 - Set up environment variables.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_1_-_Set_up_environment_variables.)
      * [1.2.2 Step 2 - Create Build Directories and run cmake.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_2_-_Create_Build_Directories_and_run_cmake.)
      * [1.2.3 Step 3 – Import the debug project into eclipse.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_3_%E2%80%93_Import_the_debug_project_into_eclipse.)
      * [1.2.4 Step 4 –Test that you can build the project from within eclipse.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_4_%E2%80%93Test_that_you_can_build_the_project_from_within_eclipse.)
      * [1.2.5 Step 5 - (optional) Modify the project properties to include additional preprocessor options.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_5_-_\(optional\)_Modify_the_project_properties_to_include_additional_preprocessor_options.)
      * [1.2.6 Step 6 - Set a breakpoint in the work function.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_6_-_Set_a_breakpoint_in_the_work_function.)
      * [1.2.7 Step 7 - Modify Python QA test.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_7_-_Modify_Python_QA_test.)
      * [1.2.8 Step 8 - Enable GDB_ATTACH.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_8_-_Enable_GDB_ATTACH.)
      * [1.2.9 Step 9 - Run the QA test from the terminal.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_9_-_Run_the_QA_test_from_the_terminal.)
      * [1.2.10 Step 10 - Attach the debugger in eclipse.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_10_-_Attach_the_debugger_in_eclipse.)
      * [1.2.11 Step 11 – Continue the QA test by pressing [ENTER] in the terminal window.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_11_%E2%80%93_Continue_the_QA_test_by_pressing_\[ENTER\]_in_the_terminal_window.)
      * [1.2.12 Step 12 – Perform Source level debugging.](https://wiki.gnuradio.org/index.php?title=UsingEclipse#Step_12_%E2%80%93_Perform_Source_level_debugging.)


# Building and source level debugging OOT C++ modules with eclipse
The procedure described below allows eclipse to be used for code syntax checking and building. A C++ out of tree module can also be source level debugged within eclipse through the use of the module’s QA functions. Note that this process does not allow you to source level debug a running flowgraph. 
## PREREQUISITES:
1. The following instructions have been tested with Eclipse CDT V 4.6.3 in Ubuntu LTS 16.04. The PyDev eclipse plugin was also installed. 
2. The following instructions and example assume that you have first created the example out of tree module as described in <https://wiki.gnuradio.org/index.php/OutOfTreeModules> and can build and run the QA test successfully. 
3. In order to have eclipse attach to the process with gdb, PTRACE scope must be set to 0. 
To check: 

```
cat /proc/sys/kernel/yama/ptrace_scope

```

If the above command returns a '1', edit /etc/sysctl.d/10-ptrace.conf and set: 

```
kernel.yama.ptrace_scope = 0

```

You will need to reboot to have this change take affect. 
## PROCEDURE:
### Step 1 - Set up environment variables.

```
user@machine:~/work/gr_demo$ source /data/work/gr/setup_env.sh

```

Note that the path will depend on where gnuradio was installed. In the below description the PREFIX for the installation is /data/work/gr/ 
### Step 2 - Create Build Directories and run cmake.
Eclipse expects the build directories to be siblings (not children) of the source directory. Accordingly, build directories should be set up as follows:           mymodule/gr-module <- source     mymodule /gr-module-debug     mymodule /gr-module-release
Assuming that gr-howto has been installed in ~/work/gr_demo/ : 

```
user@machine:~/work/gr_demo$ mkdir gr-howto-debug
user@machine:~/work/gr_demo$ mkdir gr-howto-release
user@machine:~/work/gr_demo$ cd gr-howto-debug/
user@machine:~/work/gr_demo/gr-howto-debug$ cmake -G "Eclipse CDT4 - Unix Makefiles" -D CMAKE_BUILD_TYPE=Debug ../gr-howto/
user@machine:~/work/gr_demo$ cd ../gr-howto-release/
user@machine:~/work/gr_demo/gr-howto-release$ cmake -G "Eclipse CDT4 - Unix Makefiles" ../gr-howto/

```

This above commands create debug and release build configurations for the module that can be imported into eclipse. Both can be imported into eclipse. Alternatively, only the debug configuration can be imported into eclipse and make can be run manually from within the gr-howto-release directory when the module is ready to be deployed. For example: 

```
user@machine:~/work/gr_demo$ cd ~/work/gr_demo/gr-howto-release
user@machine:~/work/gr_demo/gr-howto-release$ make
user@machine:~/work/gr_demo/gr-howto-release$ make install

```

Note that if you have previously run cmake with the build directory as a child of the source directory, you may need to clean up some of cmake's generated files for the above procedures to work. 
### Step 3 – Import the debug project into eclipse.
1. Run eclipse from within the same terminal so that the environment is set up properly. 

```
user@machine:~/work/gr_demo/gr-howto-debug$ eclipse &

```

2. Create a new workspace (or use a previously created one) 
[![](https://wiki.gnuradio.org/images/e/e0/Grdwe_fig_001.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_001.png)
3. Import the eclipse project from the gr-howto-debug directory created earlier. 
[![](https://wiki.gnuradio.org/images/5/54/Grdwe_fig_002.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_002.png)
  

[![](https://wiki.gnuradio.org/images/b/b2/Grdwe_fig_003.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_003.png)
  

[![](https://wiki.gnuradio.org/images/2/28/Grdwe_fig_004.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_004.png)
  

[![](https://wiki.gnuradio.org/images/4/46/Grdwe_fig_005.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_005.png)
  
Note that this creates a number of directories that mirror the project source tree. **The project source will all be under [Source Directory]**. 
### Step 4 –Test that you can build the project from within eclipse.
[![](https://wiki.gnuradio.org/images/4/47/Grdwe_fig_006.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_006.png)
  

[![](https://wiki.gnuradio.org/images/thumb/1/17/Grdwe_fig_007.png/1000px-Grdwe_fig_007.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_007.png)
  

### Step 5 - (optional) Modify the project properties to include additional preprocessor options.
Without this option, eclipse will sometimes flag valid code as having errors. It does not affect building or debugging, only code checking. 
[![](https://wiki.gnuradio.org/images/7/7c/Grdwe_fig_008.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_008.png)
  

### Step 6 - Set a breakpoint in the work function.
[![](https://wiki.gnuradio.org/images/thumb/3/30/Grdwe_fig_009.png/1000px-Grdwe_fig_009.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_009.png)
### Step 7 - Modify Python QA test.
Modify python/qa_square_ff.py as follows: 
1. Add imports of os and sys: 

```
import os
import sys

```

2. Add code to wait for GDB to attach: 

```
if __name__ == '__main__':
    GDB_ATTACH=1
    # ----------------------------------------------
    # GDB Attach
    # ----------------------------------------------
    # 1. Set a breakpoint in the c++ code.
    # 2. Run gr-howto/python/qa_square_ff_test.sh
    # 3. Attach gdb via the debug configurations in eclipse.
    if (GDB_ATTACH):
        print ('Blocked waiting for GDB attach (pid = %d) ' % (os.getpid(),) + '. Press ENTER after GDB is attached.')
        sys.stdout.flush()
        raw_input ()
        # Do not include XML or the test will run twice, which we do not want during debug.
        gr_unittest.run(qa_square_ff)
    # ----------------------------------------------
    else:    
        gr_unittest.run(qa_square_ff, "qa_square_ff.xml")

```

Below is the complete file: 

```
#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 
# Copyright 2017 <+YOU OR YOUR COMPANY+>.
# 
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# 
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this software; see the file COPYING.  If not, write to
# the Free Software Foundation, Inc., 51 Franklin Street,
# Boston, MA 02110-1301, USA.
# 

from gnuradio import gr, gr_unittest
from gnuradio import blocks
import howto_swig as howto
import os
import sys

class qa_square_ff (gr_unittest.TestCase):

    def setUp (self):
        self.tb = gr.top_block ()

    def tearDown (self):
        self.tb = None


    def test_001_square_ff(self):
        src_data = (-3, 4, -5.5, 2, 3)
        expected_result = (9, 16, 30.25, 4, 9)
        src = blocks.vector_source_f(src_data)
        sqr = howto.square_ff()
        dst = blocks.vector_sink_f()
        self.tb.connect(src, sqr)
        self.tb.connect(sqr, dst)
        self.tb.run()
        result_data = dst.data()
        self.assertFloatTuplesAlmostEqual(expected_result, result_data, 6)

if __name__ == '__main__':
    GDB_ATTACH=1
    # ----------------------------------------------
    # GDB Attach
    # ----------------------------------------------
    # 1. Set a breakpoint in the c++ code.
    # 2. Run gr-howto/python/qa_square_ff_test.sh
    # 3. Attach gdb via the debug configurations in eclipse.
    if (GDB_ATTACH):
        print ('Blocked waiting for GDB attach (pid = %d) ' % (os.getpid(),) + '. Press ENTER after GDB is attached.')
        sys.stdout.flush()
        raw_input ()
        # Do not include XML or the test will run twice, which we do not want during debug.
        gr_unittest.run(qa_square_ff)
    # ----------------------------------------------
    else:    
        gr_unittest.run(qa_square_ff, "qa_square_ff.xml")

```

This code will allow you to optionally halt the qa test and wait for the debugger to attach. 
### Step 8 - Enable GDB_ATTACH.
Set GDB_ATTACH=1 in qa_square_ff.py (set to 0 if not source level debugging) 
[![](https://wiki.gnuradio.org/images/thumb/d/db/Grdwe_fig_010.png/1000px-Grdwe_fig_010.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_010.png)
  

### Step 9 - Run the QA test from the terminal.

```
user@machine:~/work/gr_demo/gr-howto-debug$ cd ~/work/gr_demo/gr-howto-debug/python
user@machine:~/work/gr_demo/gr-howto-debug/python$ . qa_square_ff_test.sh 
Blocked waiting for GDB attach (pid = 14102) . Press ENTER after GDB is attached.

```

At this point the test will block so that the debugger can attach. 
### Step 10 - Attach the debugger in eclipse.
1. Attach the debugger in eclipse 
[![](https://wiki.gnuradio.org/images/6/60/Grdwe_fig_012.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_012.png)
  

[![](https://wiki.gnuradio.org/images/d/d3/Grdwe_fig_011.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_011.png)
  
2. Select the process ID indicated in Step 9. 
Hint: type “py” in the text box to limit the choices to python processes. 
[![](https://wiki.gnuradio.org/images/2/2d/Grdwe_fig_013.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_013.png)
3. Confirm the perspective switch 
[![](https://wiki.gnuradio.org/images/0/00/Grdwe_fig_014.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_014.png)
4. When GDB attaches, it will immediately break. Select Resume to continue. 
[![](https://wiki.gnuradio.org/images/5/52/Grdwe_fig_015.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_015.png)
  

### Step 11 – Continue the QA test by pressing [ENTER] in the terminal window.

```
user@machine:~/work/gr_demo/gr-howto-debug$ cd ~/work/gr_demo/gr-howto-debug/python
user@machine:~/work/gr_demo/gr-howto-debug/python$ . qa_square_ff_test.sh 
Blocked waiting for GDB attach (pid = 14102) . Press ENTER after GDB is attached.

```

### Step 12 – Perform Source level debugging.
[![](https://wiki.gnuradio.org/images/thumb/a/a5/Grdwe_fig_016.png/1000px-Grdwe_fig_016.png)](https://wiki.gnuradio.org/index.php?title=File:Grdwe_fig_016.png)
  
After [ENTER] is pressed, the QA test will continue. This should result in the process halting at the set breakpoint in the work function. 
You can now step through your code. Repeat steps as necessary. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=UsingEclipse&oldid=4030](https://wiki.gnuradio.org/index.php?title=UsingEclipse&oldid=4030)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=UsingEclipse "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=UsingEclipse "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:UsingEclipse&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=UsingEclipse)
  * [View source](https://wiki.gnuradio.org/index.php?title=UsingEclipse&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=UsingEclipse&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/UsingEclipse "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/UsingEclipse "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=UsingEclipse&oldid=4030 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=UsingEclipse&action=info "More information about this page")


  * This page was last edited on 2 October 2017, at 17:26.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


