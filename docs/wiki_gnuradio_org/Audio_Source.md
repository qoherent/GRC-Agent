# Audio Source
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Audio_Source#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Audio_Source#searchInput)
  
Acts as a microphone input. See [Audio Sink](https://wiki.gnuradio.org/index.php?title=Audio_Sink "Audio Sink") for a block that outputs to an audio device such as a speaker. 
Not all sampling rates will be supported by your hardware. The audio source can have multiple outputs depending upon your hardware. 
## Contents
  * [1 Parameters](https://wiki.gnuradio.org/index.php?title=Audio_Source#Parameters)
    * [1.1 Sample Rate](https://wiki.gnuradio.org/index.php?title=Audio_Source#Sample_Rate)
    * [1.2 Device Name](https://wiki.gnuradio.org/index.php?title=Audio_Source#Device_Name)
    * [1.3 OK to Block](https://wiki.gnuradio.org/index.php?title=Audio_Source#OK_to_Block)
    * [1.4 Num Outputs](https://wiki.gnuradio.org/index.php?title=Audio_Source#Num_Outputs)
  * [2 Operating System](https://wiki.gnuradio.org/index.php?title=Audio_Source#Operating_System)
    * [2.1 OSX](https://wiki.gnuradio.org/index.php?title=Audio_Source#OSX)
    * [2.2 Linux](https://wiki.gnuradio.org/index.php?title=Audio_Source#Linux)
    * [2.3 Windows](https://wiki.gnuradio.org/index.php?title=Audio_Source#Windows)
  * [3 Example Flowgraphs](https://wiki.gnuradio.org/index.php?title=Audio_Source#Example_Flowgraphs)
    * [3.1 Audio Source to RTTY decoder](https://wiki.gnuradio.org/index.php?title=Audio_Source#Audio_Source_to_RTTY_decoder)
    * [3.2 Sound detector and notifier](https://wiki.gnuradio.org/index.php?title=Audio_Source#Sound_detector_and_notifier)


## Parameters
### Sample Rate
To set the Audio sampling rate, click the drop-down menu to see popular rates. Note: not all sampling rates will be supported by your hardware. For typical applications, this should be set to 48kHz. 
### Device Name
Leave the device name blank to choose the default audio device. 
To select a particular input device, a name (`[string]`) or index number (`[int]`) can be specified. The exact name or index number depends on the Operating System and the audio system in use (see below). 
### OK to Block
On by default, which should be used when this source is not throttled by any other block. 
### Num Outputs
The audio source can have multiple outputs depending upon your hardware. For example, set the outputs to 2 for stereo or 1 for mono. 
## Operating System
#### OSX
For OSX, Audio Source will return only zeros unless the GNU Radio binary is launched from an application that has been granted permission to use the microphone. For example, if you are launching GNU Radio Companion from the iTerm command line, go to System Preferences -> Security & Privacy -> Privacy -> Microphone and check the box for "iTerm2". 
Once the application has permission, leaving the 'Device Name' blank will connect to the current default audio input device. To see or change the current device, go into the System Preferences, click on "Sound", and then the "Input" tab. 
The listings under "Name" contain the exact device names currently available; if a new audio source is attached to the computer then a new name will appear -- for example "Line In" for some Macs. Since most such device names contain spaces, make sure to put quotes around the name argument, for example: 

```
   spectrum_inversion.py -I "MacBook Pro Microphone"

```

#### Linux
On Linux, the device is selected via ALSA, typical choices include: 
  * `default` (selected if left empty)

    This will use the default device. Note that in most desktop systems this is actually managed by PipeWire or PulseAudio, to check this, you can execute `arecord -L | grep -A1 ^default`.
  * `hw:0,0`

    This will select the hardware card 0, device 0. To check the list of available cards/devices, issue the command `arecord -l` (note that `-l` is lower case here).
  * `plughw:0,0`

    This is the same as `hw:0,0` but enables software processing, which allows e.g. using a sample rate not natively supported.
  * `pipewire` (to explicitly use PipeWire)
  * `pulse` (to explicitly use PulseAudio)


  
For ALSA users with audio trouble, follow this procedure: 
  * from a terminal window enter `arecord -L`


  * find the entry such as:


```
   hw:CARD=Device,DEV=0
       USB Audio Device, USB Audio
       Direct hardware device without any conversions

```
    from the list which matches your device.
  * use the first line of that entry (e.g. "hw:CARD=Device,DEV=0") as the device name (without the quotes).


  * For issues or debugging, see [ALSAPulseAudio](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio "ALSAPulseAudio").


#### Windows
On Windows, go into the Settings (`Windows Key + I`), cllck on "System", click on "Sound", and then the "Input" tab. The listings under "Name" contain the exact device names currently available; if a new audio source is attached to the computer then a new name will appear. Since most of the device names contain spaces, make sure to put quotes around the name argument, for example: 

```
   "Microphone (Realtek High Definition Audio)"

```

  * **portaudio**

    When this backend module is in use (see `gr-audio.conf`), the names given to audio devices are adopted from Windows.     An alternate method to see the names of input devices is to use the Multimedia System Control Panel.     Type the `Windows Key`, then type `mmsys.cpl`, and hit `Enter`. The input devices are found in the `Recording` tab.     To see the index numbers of input devices, ensure the `python-sounddevice` package is installed in the `radioconda` environment,     after which the command `python -m sounddevice` will produce a complete list.
  * **windows**

    When this backend module is in use, the names and index numbers are as assigned by Windows.     Windows PowerShell must be configured in advance just once to list the names and index numbers.     Start PowerShell using `Run as Administrator` and issue the command `Install-Module -Name AudioDeviceCmdlets`.     After that, run PowerShell normally and the cmdlet `get-audiodevice -list` will produce the list.
## Example Flowgraphs
### Audio Source to RTTY decoder
This flowgraph shows the Audio Source block feeding a radioteletype (RTTY) decoder. 
[![](https://wiki.gnuradio.org/images/thumb/8/8e/RTTY_rcv.png/800px-RTTY_rcv.png)](https://wiki.gnuradio.org/index.php?title=File:RTTY_rcv.png)
  

### Sound detector and notifier
This flowgraph takes audio from the audio source, squares it, to get something proportional to the instantaneous power, then low-pass filters it to approximate an averaged-out power, applies a [Threshold](https://wiki.gnuradio.org/index.php?title=Threshold "Threshold") to it. The latter starts outputting 1s as soon as the smoothed power crosses 10% of the maximum power, and turns back to generating 0s when it falls below 5%. 
The resulting stream of 1s and 0s is multiplied with a 440 Hz tone and then fed into an [Audio Sink](https://wiki.gnuradio.org/index.php?title=Audio_Sink "Audio Sink"), allowing the user to hear a tone for as long as there's enough sound at the input. 
  
[![](https://wiki.gnuradio.org/images/thumb/9/9b/Audio_passthrough.png/800px-Audio_passthrough.png)](https://wiki.gnuradio.org/index.php?title=File:Audio_passthrough.png)
[File:Audio passthrough.grc](https://wiki.gnuradio.org/index.php?title=File:Audio_passthrough.grc "File:Audio passthrough.grc")
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Audio_Source&oldid=14698](https://wiki.gnuradio.org/index.php?title=Audio_Source&oldid=14698)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Block Docs](https://wiki.gnuradio.org/index.php?title=Category:Block_Docs "Category:Block Docs")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Audio+Source "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Audio_Source "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Audio_Source&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Audio_Source)
  * [View source](https://wiki.gnuradio.org/index.php?title=Audio_Source&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Audio_Source&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Audio_Source "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Audio_Source "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Audio_Source&oldid=14698 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Audio_Source&action=info "More information about this page")


  * This page was last edited on 29 March 2025, at 17:32.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


