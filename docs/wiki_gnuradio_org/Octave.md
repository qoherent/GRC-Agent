# Octave
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Octave#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Octave#searchInput)
## Contents
  * [1 Octave and Matlab](https://wiki.gnuradio.org/index.php?title=Octave#Octave_and_Matlab)
    * [1.1 Installing](https://wiki.gnuradio.org/index.php?title=Octave#Installing)
    * [1.2 Parsing Data](https://wiki.gnuradio.org/index.php?title=Octave#Parsing_Data)
    * [1.3 Plotting](https://wiki.gnuradio.org/index.php?title=Octave#Plotting)
    * [1.4 Using Python as an alternative to Octave and Matlab](https://wiki.gnuradio.org/index.php?title=Octave#Using_Python_as_an_alternative_to_Octave_and_Matlab)


# Octave and Matlab
[Octave](http://www.gnu.org/software/octave/) is the most popular analysis tool with GNU Radio, as the GNU Radio package includes its own set of [scripts](https://github.com/gnuradio/gnuradio/tree/master/gr-utils/octave) for reading and parsing output. 
Matlab is a closed source tool, and very expensive--but if you already have it installed, you might prefer it to Octave. 
## Installing
Installing Octave can be done from [source](http://www.gnu.org/software/octave/download.html), or in Ubuntu using: 

```
 sudo apt-get install octave
```

To use the GNU Radio octave scripts, you must add the path to your Octave path variable. This is easily done using your local ~/.octaverc configuration file. If you check out the GNU Radio trunk to /home/username/gnuradio/, you can add the following to ~/.octaverc: 

```
 addpath("/home/username/gnuradio/gr-utils/octave")
```

## Parsing Data
To parse data output from GNU Radio, the easiest thing to do is use the provided scripts. Ensure that you have added the GNU Radio script path to your octave path, as described in the [installing](https://wiki.gnuradio.org/index.php?title=Octave#Installing) guide. These help you read data that you may have dumped to disk using gr.file_sink(_size_ , _filename_). 
You want to use one of the following methods, based on the _size_ parameter used in gr.file_sink(). Each method takes a filename as the first parameter, and an optional second parameter which is the number of items to read from the file: 
'**__'_ read_complex_binary()_**: gr.sizeof_gr_complex 
'**__'_ read_float_binary()_**: gr.sizeof_float 
'**__'_ read_int_binary()_**: gr.sizeof_int 
'**__'_ read_short_binary()_**: gr.sizeof_short 
'**__'_ read_char_binary()_**: gr.sizeof_char 
For example, after capturing 64-bit complex using _gr.file_sink(gr.sizeof_gr_complex, "capture.dat")_ in a Python script: 

```
 c=read_complex_binary('capture.dat');
```

Data captured directly from the USRP is stored as 32-bit complex, rather than 64-bit complex (gr.sizeof_gr_complex). To read this data, first use _read_short_binary()_ and then split it into a two dimensional vector: 

```
 d=read_short_binary(data);
 c=split_vect(d,2);
```

This works for both Octave and Matlab. 
## Plotting
To plot data using octave, it is easiest to do with [gnuplot](http://www.gnuplot.info/). You can install GNU plot from [source](http://www.gnuplot.info/download.html) or using the Ubuntu repository: 

```
 sudo apt-get install gnuplot
```

To plot I and Q separately over time, graph each component separately: 

```
 plot([real(c), imag(c)])
```

Generating an I/Q plot (x-axis I, y-axis Q) can be done using: 

```
 plot(c)
```

## Using Python as an alternative to Octave and Matlab
Most likely you have several scientific Python libraries installed, such as SciPy and NumPy components (in particular, [Matplotlib](http://matplotlib.sourceforge.net/)). With these tools, you can use Python to plot and analyse data. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Octave&oldid=7403](https://wiki.gnuradio.org/index.php?title=Octave&oldid=7403)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Octave "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Octave "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Octave&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Octave)
  * [View source](https://wiki.gnuradio.org/index.php?title=Octave&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Octave&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Octave "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Octave "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Octave&oldid=7403 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Octave&action=info "More information about this page")


  * This page was last edited on 25 July 2020, at 21:44.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


