# ALSAPulseAudio
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio#searchInput)
## Contents
  * [1 Working with ALSA and Pulse Audio](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio#Working_with_ALSA_and_Pulse_Audio)
  * [2 Talking to ALSA](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio#Talking_to_ALSA)
  * [3 Monitoring the audio input of your system with PulseAudio](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio#Monitoring_the_audio_input_of_your_system_with_PulseAudio)
    * [3.1 Add ALSA Pseudodevice for monitor](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio#Add_ALSA_Pseudodevice_for_monitor)
    * [3.2 Using the newly created device](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio#Using_the_newly_created_device)


## Working with ALSA and Pulse Audio
  * Don't clip: The maximum amplitude in your signal **must not** exceed 1.0.
  * Set "OK to Block" to "No" when the flowgraph is throttled by another hardware device.
  * Sound cards don't support arbitrary sampling rates. If your audio is choppy, check the rate of your audio sink or source: 44100 Hz works under all known audio cards, 48000 Hz on most, others may not.
  * If you get lots of `aU` (audio underruns) on your terminal, try creating or editing ~/.gnuradio/config.conf as follows:


```
[audio_alsa]
nperiods = 32
period_time = 0.010
verbose = false

```

## Talking to ALSA
When used on Linux, the GNU Radio Audio Sink and Source blocks typically use the ALSA Application Programming Interface (API), unless ALSA support was disabled during build time. ALSA has been the standard audio API under Linux for a decade or more, so basically all programs that produce audio know how to deal with it. GNU Radio also supports other audio APIs, but this page focuses on ALSA. 
The ALSA system has a plug-in architecture, and one of these plug-ins allows applications that use the ALSA API such as GNU Radio to utilize the [PulseAudio](https://en.wikipedia.org/wiki/PulseAudio) _sound server_ , which supports the complex use cases required on a modern computer such as mixing sound from various sources into a single stream that can be controlled by a central volume control, remote/network audio streaming, etc. 
However, PulseAudio's device isn't always perfect. PulseAudio is capable of resampling internally, but the results aren't always predictable. Also, because of its complexity, PulseAudio can sometimes impact performance negatively, causing gaps in the audio being played back. These gaps can be quite long on slower computers. For some GNU Radio applications, it may be desirable to use the ALSA device directly, rather than using the PulseAudio path. 
To do so, you can obtain a list of the ALSA playback devices (for an Audio Sink) using the `aplay` program. 
  * from a terminal window enter:


`aplay -L`
  * a long list of options will be displayed, such as:


```
default
    Playback/recording through the PulseAudio sound server
null
    Discard all samples (playback) or generate zero samples (capture)
pulse
    PulseAudio Sound Server
hdmi:CARD=HDMI,DEV=0
    HDA ATI HDMI, HDMI 0
    HDMI Audio Output
hw:CARD=Generic,DEV=0
    HD-Audio Generic, ALC662 rev3 Analog
    Direct hardware device without any conversions
plughw:CARD=Generic,DEV=0
    HD-Audio Generic, ALC662 rev3 Analog
    Hardware device with all software conversions
...

```

  * find the entry such as:


```
hw:CARD=Generic,DEV=0
    HD-Audio Generic, ALC662 rev3 Analog
    Direct hardware device without any conversions

```

in the list which matches your desired device. 
  * use the first line of that entry (e.g. "hw:CARD=Generic,DEV=0") as the device name (without the quotes). The device name can be set in the Audio Sink block in `gnuradio-companion`, or in the C++ source code of a GNU Radio C++ application, or in the `audio_alsa` stanza in the `$HOME/.gnuradio/config.conf` file if you want to set the default device for all GNU Radio applications running from your Linux login session.


For audio input devices (an Audio Source), use: 

```
arecord -L
```

to obtain a similar list. 
## Monitoring the audio input of your system with PulseAudio
**IMPORTANT: this procedure only applies to an Audio Source block!**
PulseAudio has its own monitor "ports". You can list all PulseAudio monitor sources by running this in a terminal: 

```
pactl list|grep "Monitor Source"|sed 's/^[[:space:]]*Monitor Source: //g'
```

This will give you one or more lines containing something like 

```
alsa_output.pci-0000_00_03.0.hdmi-stereo.monitor
alsa_output.pci-0000_00_1b.0.analog-stereo.monitor
alsa_output.pci-0000_06_00.1.hdmi-stereo.monitor
```

Each of these usually represents one of your computer's available audio output devices. In the example shown, there are two HDMI outputs and one analog output. Headphones often have their own entry. 
You need to determine which device is currently being used for audio output on your computer because you'll be copying that name in the next step. You can determine the device by unplugging devices from your computer and re-running the `pactl` command shown above to see how the list changes. If you don't know which item to pick, you can try each. In the steps below, we will use `alsa_output.pci-0000_00_1b.0.analog-stereo.monitor`. 
### Add ALSA Pseudodevice for monitor
Now, we need to edit (or create, if it doesn't already exist) the file `~/.asoundrc`. We will show how to do this using the text editor `nano`.  

Run this command in a terminal to edit/create `~/.asoundrc`: 

```
nano ~/.asoundrc
```

This will open the `nano` text editor in the terminal. When using `nano`, you'll need to use the keyboard arrows rather than the mouse to move your cursor. 
Paste the following, but replace `alsa_output.pci-0000_00_1b.0.analog-stereo.monitor` with your device name obtained in the previous step. 

```
pcm.pulse_monitor {
    type pulse
    device alsa_output.pci-0000_00_1b.0.analog-stereo.monitor
}

ctl.pulse_monitor {
    type pulse
    device alsa_output.pci-0000_00_1b.0.analog-stereo.monitor
}

```

Save the file using Ctrl + s, and then exit `nano` using Ctrl + x. 
### Using the newly created device
In the Audio Source block, use `pulse_monitor` as the device name:  

[![PulseAudio-ALSA-Monitoring.png](https://wiki.gnuradio.org/images/1/17/PulseAudio-ALSA-Monitoring.png)](https://wiki.gnuradio.org/index.php?title=File:PulseAudio-ALSA-Monitoring.png "PulseAudio-ALSA-Monitoring.png")
Retrieved from "[https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio&oldid=15406](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio&oldid=15406)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=ALSAPulseAudio "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:ALSAPulseAudio&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio)
  * [View source](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/ALSAPulseAudio "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/ALSAPulseAudio "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio&oldid=15406 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=ALSAPulseAudio&action=info "More information about this page")


  * This page was last edited on 1 October 2025, at 15:34.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


