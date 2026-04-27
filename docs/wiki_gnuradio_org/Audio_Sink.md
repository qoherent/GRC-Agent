# Audio Sink
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Audio_Sink#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Audio_Sink#searchInput)
  
Allows a signal to be played through your speakers. See [Audio Source](https://wiki.gnuradio.org/index.php?title=Audio_Source "Audio Source") for a block that inputs from an audio device such as a microphone. 
Not all sampling rates will be supported by your hardware. The audio sink can have multiple inputs depending upon your hardware. 
## Contents
  * [1 Parameters](https://wiki.gnuradio.org/index.php?title=Audio_Sink#Parameters)
    * [1.1 Sample Rate](https://wiki.gnuradio.org/index.php?title=Audio_Sink#Sample_Rate)
    * [1.2 Device Name](https://wiki.gnuradio.org/index.php?title=Audio_Sink#Device_Name)
    * [1.3 OK to Block](https://wiki.gnuradio.org/index.php?title=Audio_Sink#OK_to_Block)
    * [1.4 Num Inputs](https://wiki.gnuradio.org/index.php?title=Audio_Sink#Num_Inputs)
  * [2 Operating System](https://wiki.gnuradio.org/index.php?title=Audio_Sink#Operating_System)
    * [2.1 OSX](https://wiki.gnuradio.org/index.php?title=Audio_Sink#OSX)
    * [2.2 Linux](https://wiki.gnuradio.org/index.php?title=Audio_Sink#Linux)
    * [2.3 Windows](https://wiki.gnuradio.org/index.php?title=Audio_Sink#Windows)
  * [3 Example Flowgraph](https://wiki.gnuradio.org/index.php?title=Audio_Sink#Example_Flowgraph)


## Parameters
### Sample Rate
To set the Audio sampling rate, click the drop-down menu to see popular rates. Note: not all sampling rates will be supported by your hardware. For typical applications, this should be set to 48kHz. 
### Device Name
Leave the device name blank to choose the default audio device. 
To select a particular output device, a name (`[string]`) or index number (`[int]`) can be specified. The exact name or index number depends on the Operating System and the audio system in use (see below). 
### OK to Block
On by default, which should be used when this sink is not throttled by any other block. 
### Num Inputs
The audio sink can have multiple inputs depending upon your hardware. For example, set the inputs to 2 for stereo or 1 for mono. 
## Operating System
#### OSX
On OSX, go into the System Preferences, click on "Sound", and then the "Output" tab. The listings under "Name" contain the exact device names currently available; if a new audio device is attached to the computer then a new name will appear -- for example "Headphones" for some Macs. Since most such device names contain spaces, make sure to put quotes around the name argument, for example: 

```
   spectrum_inversion.py -O "MacBook Pro Speakers"

```

#### Linux
On Linux, the device is selected via ALSA, where typical choices include: 
  * `default` (selected if left empty)

    This will use the default device. Note that in most desktop systems this is actually managed by PipeWire or PulseAudio, to check this, you can execute `aplay -L | grep -A1 ^default`.
  * `hw:0,0`

    This will select the hardware card 0, device 0. To check the list of available cards/devices, issue the command `aplay -l` (note that `-l` is lower case here).
  * `plughw:0,0`

    This is the same as `hw:0,0` but enables software processing, which allows e.g. selecting a sample rate not natively supported by the sound card.
  * `pipewire` (to explicitly use PipeWire)
  * `pulse` (to explicitly use PulseAudio)


  

For ALSA users with audio trouble, follow this procedure: 
  * from a terminal window enter `aplay -L`


  * find the desired entry such as:


```
hw:CARD=Generic,DEV=0
    HD-Audio Generic, ALC662 rev3 Analog
    Direct hardware device without any conversions

```
    from the list which matches your device. To use an HDMI monitor with speakers, find an appropriate entry with "HDMI" in it.
  * use the first line of that entry (e.g. "hw:CARD=Generic,DEV=0") as the device name. Unless the name has spaces in it, the quotes are optional.


  * For issues or debugging, see [ALSAPulseAudio](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio "ALSAPulseAudio").


#### Windows
On Windows, go into the Settings, cllck on "System", click on "Sound", and then the "Output" tab. The listings under "Name" contain the exact device names currently available; if a new audio device is attached to the computer then a new name will appear, for example "Headphones". Since most of the device names contain spaces, make sure to put quotes around the name argument, for example: 

```
   "Speakers (Realtek High Definition Audio)"

```

  * **portaudio**

    When this backend module is in use (see `gr-audio.conf`), the names given to audio devices are adopted from Windows.     An alternate method to see the names of output devices is to use the Multimedia System Control Panel.     Type the `Windows Key`, then type `mmsys.cpl`, and hit `Enter`. The output devices are found in the `Playback` tab.     To see the index numbers of output devices, ensure the `python-sounddevice` package is installed in the `radioconda` environment,     after which the command `python -m sounddevice` will produce a complete list.
  * **windows**

    When this backend module is in use, the names and index numbers are as assigned by Windows.     Windows PowerShell must be configured in advance just once to list the names and index numbers.     Start PowerShell using `Run as Administrator` and issue the command `Install-Module -Name AudioDeviceCmdlets`.     After that, run PowerShell normally and the cmdlet `get-audiodevice -list` will produce the list.
## Example Flowgraph
This flowgraph should play a 1 kHz tone out of your speakers. Note that you don't need a throttle block, the Audio Sink should throttle for you. If you do end up using an already-throttled signal source, then set "OK to Block" to No. 
[![](https://wiki.gnuradio.org/images/thumb/0/07/Audio-sink-ex.png/400px-Audio-sink-ex.png)](https://wiki.gnuradio.org/index.php?title=File:Audio-sink-ex.png)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Audio_Sink&oldid=14697](https://wiki.gnuradio.org/index.php?title=Audio_Sink&oldid=14697)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Audio+Sink "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Audio_Sink "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Audio_Sink "Discussion about the content page \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Audio_Sink)
  * [View source](https://wiki.gnuradio.org/index.php?title=Audio_Sink&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Audio_Sink&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Audio_Sink "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Audio_Sink "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Audio_Sink&oldid=14697 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Audio_Sink&action=info "More information about this page")


  * This page was last edited on 29 March 2025, at 01:34.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


