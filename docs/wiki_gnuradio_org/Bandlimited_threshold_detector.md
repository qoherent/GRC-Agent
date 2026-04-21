# Bandlimited threshold detector
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#searchInput)
  

## Contents
  * [1 Introduction](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Introduction)
  * [2 Prerequisites](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Prerequisites)
  * [3 Goals](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Goals)
    * [3.1 Generate Synthetic RF Spectrum with Intermittent Carriers](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Generate_Synthetic_RF_Spectrum_with_Intermittent_Carriers)
    * [3.2 Set Visual Boundary Lines around a segment of the Frequency Spectrum and a Threshold Level](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Set_Visual_Boundary_Lines_around_a_segment_of_the_Frequency_Spectrum_and_a_Threshold_Level)
    * [3.3 Detection](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Detection)
  * [4 Content](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Content)
  * [5 Generate 'Synthetic RF Spectrum'](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Generate_'Synthetic_RF_Spectrum')
  * [6 Create A Visual Utility to Set Detection Boundaries for Frequency and Level](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Create_A_Visual_Utility_to_Set_Detection_Boundaries_for_Frequency_and_Level)
    * [6.1 Dumb Threshold](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Dumb_Threshold)
    * [6.2 Frequency Boundary Box](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Frequency_Boundary_Box)
    * [6.3 Add the synthetic signal to the Display](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Add_the_synthetic_signal_to_the_Display)
    * [6.4 Detection](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Detection_2)
    * [6.5 Embedded Python Block to Record Detections](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Embedded_Python_Block_to_Record_Detections)
    * [6.6 Embedded Block Code](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector#Embedded_Block_Code)


## Introduction
Sometimes a DSP application will call for signal detection of an intermittent signal (only present in the spectrum part of the time). A simplistic way to detect signal is by way of a frequency domain threshold, when an FFT bin exceeds that threshold the signal is 'detected'. Using a 'dumb' threshold would be a first obvious choice for triggering a detection event. The threshold level is the same across the entire spectral window and it is set to a value above the observed noisefloor, but below the minimum level of a particular signal we are trying to detect. With this simple approach (a straight line across the spectrum), a detection event is triggered anytime the threshold is exceeded. Ideally our threshold would only trigger on the specific signals we want to detect, those signals may be clustered together in frequency in a range or band contained within the spectral observation window. If we restrict the detection criteria to only a simple level threshold, some signals within our observation window may trigger a detection in error (since we only want to detect signals in a subband of our observation window) If we can restrict the threshold to not only power, but also frequency, we can still look at a large frequency range and see what else is present, but only trigger a detection if the threshold is crossed within the defined portion of spectrum. 
## Prerequisites
  * [Intro to GR usage: GRC and flowgraphs](https://wiki.gnuradio.org/index.php?title=Guided_Tutorial_GRC "Guided Tutorial GRC")


  * [Streams and Vectors](https://wiki.gnuradio.org/index.php?title=Streams_and_Vectors "Streams and Vectors")


## Goals
#### Generate Synthetic RF Spectrum with Intermittent Carriers
  * broadband noise
  * narrowband signals with intermittent behavior
  * a large wideband signal


#### Set Visual Boundary Lines around a segment of the Frequency Spectrum and a Threshold Level
  * Visualize the Synthetic RF Spectrum in the Frequency Domain
  * Create an adjustable threshold (horizontal line) that is displayed in the frequency window and also can be manually adjusted by the user.
  * Add upper and lower frequency boundaries (vertical lines) which will restrict the threshold trigger to signals within the boundary box.


#### Detection
  * instantiate logic using in-tree blocks to compare the incoming signal's spectrum to the upper/lower frequency boundaries and only display


the portion of the spectrum contained within the frequency boundary lines 
  * instantiate logic to compare the spectrum contained within the frequency boundary lines to the threshold and only display the portion that


crosses the threshold. 
  * use a custom python block to trigger a file recording of the bin number and indices of any threshold crossings we detect


## Content
The flowgraph for this tutorial is shown below along with the GRC file needed if you would like to test it out. 
[![](https://wiki.gnuradio.org/images/6/68/Whole_flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:Whole_flowgraph.png)
[File:Bandlimited threshold detector.grc](https://wiki.gnuradio.org/index.php?title=File:Bandlimited_threshold_detector.grc "File:Bandlimited threshold detector.grc")
## Generate 'Synthetic RF Spectrum'
In the following example we will: Generate a synthetic signal for testing. It is assumed that you are comfortable enough in GNURadio to understand what these blocks are doing. 
  
[![](https://wiki.gnuradio.org/images/4/41/Synth_spectrum.png)](https://wiki.gnuradio.org/index.php?title=File:Synth_spectrum.png)
This portion of the flowgraph: 
  * generates gaussian noise for the overall noisefloor (simulating environmental broadband noise)
  * Simulates a wideband carrier by lowpass filtering a noise source that is uncorrelated with the noise in the overall spectrum
  * Creates two narrowband carriers which are each modulated by square waves of different frequencies to simulate intermittent transmissions


## Create A Visual Utility to Set Detection Boundaries for Frequency and Level
This portion of the flowgraph is where we get creative with Vectors. 
For this Flowgraph, we will have an overall FFT size used to display our synthetic frequency spectrum. In this example it will be 8192 FFT bins. 
#### Dumb Threshold
For the 'dumb threshold', a vector where all values are adjustable will allow us to use a QT GUI Range Widget to dynamically raise and lower the threshold. This can be done by creating a QT GUI Range Widget `thresh_adj` and entering `(thresh_adj,)*full_band_size` where `full_band_size` has the value 8192, the overall FFT size for the flowgraph. 
At runtime, the `thresh_adj` QT GUI Range Widget will set all the indices of this vector to the same value which will display as a horizontal line spanning the entire frequency window. When the variable is adjusted, the line moves up/down. 
#### Frequency Boundary Box
For the frequency boundary box, it gets a little more complicated. Consider a simple case of a vector with length 9, where the vector values are `[-1000,-1000,-1000,-1000,+1000,-1000,-1000,-1000,-1000]` with indices `[0,1,2,3,4,5,6,7,8,9]`. 
On a plot, we get a shape like this ____|____ where the flat parts across the bottom are 4 values of -1000 (index 0-3) on the left and 4 values of -1000 on the right (index 5-8) with one value in the middle (index 4) with value +1000. In GNURadio when we represent baseband samples as RF signals in the frequency domain (QT Frequency Sink) we limit the y-axis of the observation window to defaults of +10dB and -140dB, because we won't likely be able to receive signals greater than say +20 on a relative scale with common A/D's in SDR's. Therefore, if we insert a vector into a QT Vector GUI with values that exceed our viewing window, we will only see a vertical line in the window for the value of +1000. We can use that line as a boundary using some array logic in with python expressions. 
For the Upper/Lower boundaries we will create vector sources where each vector will adjust it's left and right sides so that both of them combined will equal _819**1**_ , adding the vertical line's index to the left and right sides will make the total vector length add up to the FFT length **8191+1=8192**
This section of the flowgraph is shown here: 
[![](https://wiki.gnuradio.org/images/8/84/Two_vert_one_horiz_vectors.png)](https://wiki.gnuradio.org/index.php?title=File:Two_vert_one_horiz_vectors.png)
The parameters for the 3 Vector Source blocks are shown here: 
[![](https://wiki.gnuradio.org/images/9/94/Adjustable_threshold_vector.png)](https://wiki.gnuradio.org/index.php?title=File:Adjustable_threshold_vector.png) [![](https://wiki.gnuradio.org/images/f/f8/Adjustable_upper_bound_vector.png)](https://wiki.gnuradio.org/index.php?title=File:Adjustable_upper_bound_vector.png) [![](https://wiki.gnuradio.org/images/9/93/Adjustable_lower_bound_vector.png)](https://wiki.gnuradio.org/index.php?title=File:Adjustable_lower_bound_vector.png)
`fft_size` = 8192 
`below_zero` is the extremely low value from our simple length 9 vector example (-1000) 
`vec_height` is the extremely high value from our simple length 9 vector example (+1000) 
`low_line_adj` is a QT GUI Range Widget that we use to adjust the position of the vertical line that indicates the lower frequency boundary of the frequency boundary box 
`up_line_adj` is a QT GUI Range Widget that we use to adjust the position of the vertical line that indicates the upper frequency boundary of the frequency boundary box 
The upper and lower frequency boundary vectors will be constructed to expand or contract in length based on the desired position of their vertical boundary lines. 
Both vertical boundary lines follow the same logic. In the case of the lower boundary line: 
The left half of the vector can be expressed as `(low_line_adj)*(below_zero,)`, which says that the number of bins to the left of the vertical line's position (the 'left side' of the vector) will be equal to the _position_ of the vertical line. So if the vertical line's position is index: 512, there will be 512 values to it's left (0-511) 
The right half of the vector can be expressed as `(fft_size-low_line_adj-1)*(below_zero,)`, which says that the number of bins to the right of the vertical line's position (the 'right side' of the vector) will be equal to the number of bins between the vertical line's position index (512) and the rest of the total vector length 8192 (512-8192). We subtract 1 to account for the vertical line's position itself. 
When the vertical line position is adjusted with the QT GUI Range Widget, the left/right sides will adjust accordingly in real-time. The total will always be the overall FFT Length (8192). 
[![](https://wiki.gnuradio.org/images/3/30/Low_bound_vec_top_half.png)](https://wiki.gnuradio.org/index.php?title=File:Low_bound_vec_top_half.png)
[![](https://wiki.gnuradio.org/images/1/18/Low_bound_vec_bottom_half.png)](https://wiki.gnuradio.org/index.php?title=File:Low_bound_vec_bottom_half.png)
  

  

#### Add the synthetic signal to the Display
Since the incoming signal is really the main event in spectrum monitoring, we should probably add that to the spectrum window with the threshold and frequency boundary lines. The 4th input (input 3) on the QT GUI Vector Sink is where we add our synthetic signal. 
In the section above, we showed how the synthetic signal can be constructed from several different sources and summed together to create one stream of data. Before we put it into our frequency display we need to also convert the time domain to a spectral representation. In this example, we do this by using the **Log Power FFT** block, which is a combination of several GNURadio blocks in one: 
  * stream to vector
  * vector decimation (frame rate)
  * complex to mag squared


The output is the same type as the threshold and frequency boundary lines (float32) and is also a vector. 
This part of the flowgraph is shown here: 
  
[![](https://wiki.gnuradio.org/images/7/72/Synth_signal_logpwrfft.png)](https://wiki.gnuradio.org/index.php?title=File:Synth_signal_logpwrfft.png)
  

When we display these, we will see our synthetic signal's spectral representation, our threshold and frequency boundary lines superimposed on top of the spectrum, show here: 
[![](https://wiki.gnuradio.org/images/a/a2/Synth_signal_two_vert_one_horiz_GUI_GIF.gif)](https://wiki.gnuradio.org/index.php?title=File:Synth_signal_two_vert_one_horiz_GUI_GIF.gif)
#### Detection
Now that we have lines we can move around our signal, we can also use the variables to do some comparison to only display/passthrough signal if it's within the frequency boundary box and above the threshold. 
By identifying which FFT indices (bins) that fall between the lower/upper frequency boundary lines, we can create a value to compare against our incoming signal's spectral representation. 
This is done here in the flowgraph: 
[![](https://wiki.gnuradio.org/images/4/44/Synth_signal_spectrum_inside_upper_lower.png)](https://wiki.gnuradio.org/index.php?title=File:Synth_signal_spectrum_inside_upper_lower.png)
The value of `in_box_spec_len` represents a run-time callback, where the value changes as the upper/lower frequency boundary lines are adjusted. 
[![](https://wiki.gnuradio.org/images/4/43/In_box_spec_len.png)](https://wiki.gnuradio.org/index.php?title=File:In_box_spec_len.png)
This is used to create a vector of length 8192, where all vector indices that are either to the left of the lower frequency boundary line OR to the right of the upper frequency boundary line are an extremely low number. The vector indices in between the upper/lower frequency boundary positions are 0. 
[![](https://wiki.gnuradio.org/images/a/a3/Between_upper_lower_detect.png)](https://wiki.gnuradio.org/index.php?title=File:Between_upper_lower_detect.png)
When added to the incoming signal's spectral representation, this results in a vector of length 8192 which preserves the synthetic signal's vector indices between the lower/upper frequency boundary lines. 
If the upper/lower frequency boundary lines are set just below and just above the two narrow band carriers, the displayed result is shown below: 
[![](https://wiki.gnuradio.org/images/7/76/All_spectrum_in_freq_bound_box.png)](https://wiki.gnuradio.org/index.php?title=File:All_spectrum_in_freq_bound_box.png)
Now the output of the lower/upper frequency boundary check is passed to one input of a `max` block where it is compared against the threshold. 
[![](https://wiki.gnuradio.org/images/d/d9/Detection_logic.png)](https://wiki.gnuradio.org/index.php?title=File:Detection_logic.png)
The output is a vector where every index is greater than or equal to the threshold value. 
[![](https://wiki.gnuradio.org/images/f/f0/Threshold_xings.png)](https://wiki.gnuradio.org/index.php?title=File:Threshold_xings.png)
  

#### Embedded Python Block to Record Detections
Up until this point, only **in-tree** blocks have been used. At this point, if we would like to write all values above the threshold, a very simple custom block can be used to extract them from the output of the max block above by comparing against the threshold value as it changes. 
[![](https://wiki.gnuradio.org/images/0/00/Detection_file_write_embedded_block.png)](https://wiki.gnuradio.org/index.php?title=File:Detection_file_write_embedded_block.png)
The following Embedded Python block will determine if the incoming data is _greater than_ the threshold, thus rejecting the threshold itself and recording only the spectra from the synthetic signal that is above the threshold _AND_ within the frequency boundary. The output file contains the timestamp of the detection, a list of bin numbers and a list of corresponding magnitudes. 
Example: 

```
1661155430.9426177[5323 5324 5325 5326],[-58.806225 -49.62006  -47.60839  -52.316525]
1661155431.0243776[5323 5324 5325 5326],[-58.734993 -49.58642  -47.59074  -52.312286]
1661155431.1061163[5323 5324 5325 5326],[-58.690277 -49.577198 -47.589127 -52.305576]
1661155431.1887715[5323 5324 5325 5326],[-58.769714 -49.585815 -47.57643  -52.278713]
1661155431.2700336[5323 5324 5325 5326],[-58.6992   -49.582836 -47.58903  -52.30743 ]
1661155431.3521397[5323 5324 5325 5326],[-58.765884 -49.599594 -47.594917 -52.30714 ]
1661155431.4342203[5323 5324 5325 5326],[-58.766293 -49.591564 -47.59711  -52.32647 ]

```

#### Embedded Block Code

```
"""
Embedded Python Blocks:

Each time this file is saved, GRC will instantiate the first class it finds
to get ports and parameters of your block. The arguments to __init__  will
be the parameters. All of them are required to have default values!
"""

import numpy as np
from gnuradio import gr
import time

class blk(gr.sync_block):  # other base classes are basic_block, decim_block, interp_block
    """Embedded Python Block example - a simple multiply const"""

    def __init__(self, vec_len=8192, peak_detect_file="/tmp/indexes.data"):  # only default arguments here
        """arguments to this function show up as parameters in GRC"""
        gr.sync_block.__init__(
            self,
            name='Embedded Python Block',   # will show up in GRC
            in_sig=[(np.float32,vec_len),(np.float32,vec_len)],
            out_sig=None
        )
        # if an attribute with the same name as a parameter is found,
        # a callback is registered (properties work, too).
        self.peak_detect_file=peak_detect_file

    def work(self, input_items,output_items):
        for vecindx in range(len(input_items[0])):
            if len(np.nonzero(input_items[0][vecindx] > input_items[1][vecindx][0])[0])>0:
                #print("number of crossings: ", len(np.nonzero(input_items[0][vecindx] > input_items[1][vecindx][0])[0]))
                #print(" level of crossings: ", np.nonzero(input_items[0][vecindx] > input_items[1][vecindx][0]))
                #print(" index of crossings: ", input_items[0][vecindx][np.nonzero(input_items[0][vecindx] > input_items[1][0])])
                with open(self.peak_detect_file,'a') as fobj:
                    fobj.write(str(time.time())+str(np.nonzero(input_items[0][vecindx] > input_items[1][vecindx][0])[0])+","+str(input_items[0][vecindx][np.nonzero(input_items[0][vecindx] > input_items[1][0])])+'\n')
        return len(input_items[0])

```

**NOTE:** It is true that the last step above could be skipped and this custom block used instead **if** all we want is the values written to file, however the above step allows a simple way to clearly visualize which values are above the threshold. 
  
Here is a brief demo of the flowgraph in action: 
[![](https://wiki.gnuradio.org/images/b/b3/Freq_bound_thresh_complete_demo_with_fileoutput.gif)](https://wiki.gnuradio.org/index.php?title=File:Freq_bound_thresh_complete_demo_with_fileoutput.gif)
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector&oldid=12602](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector&oldid=12602)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Bandlimited+threshold+detector "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Bandlimited_threshold_detector&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector)
  * [View source](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Bandlimited_threshold_detector "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Bandlimited_threshold_detector "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector&oldid=12602 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Bandlimited_threshold_detector&action=info "More information about this page")


  * This page was last edited on 3 October 2022, at 02:44.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


