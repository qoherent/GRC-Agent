# CustomBuffers
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=CustomBuffers#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=CustomBuffers#searchInput)
## Contents
  * [1 GNU Radio Accelerator Device Support Project](https://wiki.gnuradio.org/index.php?title=CustomBuffers#GNU_Radio_Accelerator_Device_Support_Project)
    * [1.1 Description](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Description)
      * [1.1.1 Project Goals](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Project_Goals)
      * [1.1.2 High-level Plan](https://wiki.gnuradio.org/index.php?title=CustomBuffers#High-level_Plan)
    * [1.2 Overview and Usage](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Overview_and_Usage)
      * [1.2.1 Supporting Code](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Supporting_Code)
      * [1.2.2 Examples](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Examples)
      * [1.2.3 How to Use a Custom Buffer](https://wiki.gnuradio.org/index.php?title=CustomBuffers#How_to_Use_a_Custom_Buffer)
    * [1.3 Benchmarks](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Benchmarks)
    * [1.4 Detailed Design](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Detailed_Design)
      * [1.4.1 Single Mapped Buffer Abstraction](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Single_Mapped_Buffer_Abstraction)
      * [1.4.2 Device Buffer Encapsulation and Data Movement](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Device_Buffer_Encapsulation_and_Data_Movement)
      * [1.4.3 host_buffer Class](https://wiki.gnuradio.org/index.php?title=CustomBuffers#host_buffer_Class)
      * [1.4.4 Buffer Type](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Buffer_Type)
      * [1.4.5 Replace Upstream](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Replace_Upstream)
      * [1.4.6 Callback Functions](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Callback_Functions)
      * [1.4.7 Custom Lock Interface](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Custom_Lock_Interface)
    * [1.5 Acknowledgements](https://wiki.gnuradio.org/index.php?title=CustomBuffers#Acknowledgements)


# GNU Radio Accelerator Device Support Project
David Sorber - 2021 (copied from <https://github.com/gnuradio/gnuradio-ngsched/wiki>) 
## Description
GNU Radio provides a flexible block-based interface for signal processing tasks. Historically GNU Radio signal processing blocks have been written in software but there is increasing need to offload complex signal processing algorithms to accelerator devices including GPUs, FPGAs, and DSPs. Many accelerated blocks have been created using GNU Radio's block interface but these blocks require manual handling of data movement to and from the accelerator device. The purpose of this project is to add accelerator device support directly to GNU Radio. 
### Project Goals
  * Maintain backwards compatibility with all existing blocks (both in-tree and from OOT modules)
  * Create flexible interface for creating "custom buffers" to support accelerated devices 
    * Custom buffer interface provides necessary hooks to allow the scheduler to handle data movement
  * Provide infrastructure to support "_insert signal processing here_ " paradigm for common accelerator devices such as NVidia GPUs


### High-level Plan
  * **Milestone 1** - completed: May 11, 2021 
    * refactor existing code and create single mapped buffer abstraction
    * support single accelerated block (block responsible for data movement)
    * simple custom buffer interface
  * **Milestone 2** - completed: August 5, 2021 
    * support multiple accelerated blocks with zero-copy between
    * more flexible custom buffer interface (scheduler handles data movement)


## Overview and Usage
[![](https://wiki.gnuradio.org/images/thumb/e/e6/CustomBuffers_DoubleCopy.png/1024px-CustomBuffers_DoubleCopy.png)](https://wiki.gnuradio.org/index.php?title=File:CustomBuffers_DoubleCopy.png)
GNU Radio's block-based interface is very flexible and has allowed users to create their own accelerated blocks for some time. However, this approach has some limitations. In particular if the accelerator device requires special (DMA) buffers for data movement, the accelerator block must then copy data from the GNU Radio buffer into the device's buffer on the input path and vice versa on the output path. This process is inefficient and is known as the "double copy" problem as shown in the diagram above. Furthermore, in addition to the double copy inefficiency, accelerated blocks written in this fashion require the writer to manage data movement explicitly. While this is doable it may be challenging for a novice and off-putting for a user that wishes to concentrate on implementing a signal processing algorithm. The new accelerated block interface changes address both of these issues while (very importantly) maintaining backwards compatibility for all existing GNU Radio blocks. 
### Supporting Code
The accelerated block interface was initially developed in [this repository](https://github.com/gnuradio/gnuradio-ngsched) but as of release 3.10 is part of GNU Radio. The following repositories contain supporting code that is also intended to be upstreamed to the project but not directly into the main GNU Radio repository itself (**NOTE:** both of the repositories below require the accelerated block interface changes, GR >= 3.10 or master branch as of [this commit](https://github.com/gnuradio/gnuradio/commit/9147c6eb99862697b454471b7882853e355f84cb): 
  * [gr-cuda_buffer](https://github.com/BlackLynx-Inc/gr-cuda_buffer) - This repository contains an OOT module containing the `cuda_buffer` class which is a "custom buffer" supporting the CUDA runtime for NVidia GPUs. This module is intended to be a base CUDA buffer implementation for CUDA blocks and can be used directly when writing CUDA accelerated blocks for NVidia GPUs.
  * [gr-blnxngsched](https://github.com/BlackLynx-Inc/gr-blnxngsched) - This repository contains an OOT module containing various examples of the accelerated block interface (aka "ngsched") changes. These blocks are described in a additional detail in the "Examples" section below. Note that the CUDA-related blocks in this OOT require `cuda_buffer` from gr-cuda-buffer.


### Examples
  * `custom_buffer` - A buffer object that shows a simple example for creating a custom buffer object. It uses normal host buffers and does not require any specific accelerator hardware.
  * `custom_buf_loopback` - A loopback block that uses the `custom_buffer` class from above. It shows a very simple example of how to use a custom buffer object defined within an OOT module.
  * `cuda_fanout` - (**NOTE:** requires CUDA and gr-cuda-buffer) A simple CUDA-based fanout block that utilizes block history.
  * `cuda_loopback` - (**NOTE:** requires CUDA and gr-cuda-buffer) A CUDA-based loopback block that uses the `cuda_buffer` class from gr-cuda-buffer.
  * `cuda_mult` - (**NOTE:** requires CUDA and gr-cuda-buffer) A CUDA-based complex multiplication block. This block has two inputs and one output.
  * `mixed_2_port_loopback` - (**NOTE:** requires CUDA and gr-cuda-buffer) A loopback block that combines a CUDA-based loopback with a simple host loopback. This block has two inputs and two outputs. One input/output pair uses `cuda_buffer` while the other uses default GNU Radio host buffers.


### How to Use a Custom Buffer
The following instructions illustrate how to write a block using a "custom buffer". These instructions use `cuda_buffer` from gr-cuda-buffer for example purposes but the same general concepts can be applied to any custom buffer class. 
  1. If the custom buffer class exists in a separate OOT module, install that OOT module in the same path prefix as the OOT module containing the block which will use the buffer class. For example, to use `cuda_buffer`, the gr-cuda-buffer OOT module must be installed in the same prefix.
  2. In the implementation source file include the appropriate header file for the buffer class. For example, in `new_block_impl.cc`:



```
#include <cuda_buffer/cuda_buffer.h>

```

  1. Next, within the block's constructor update the `gr::io_signature` to include the desired buffer's type. For example:



```
new_block_impl::new_block_impl()
    : gr::block("my_new_block",
                gr::io_signature::make(1 /* min inputs */, 1 /* max inputs */, 
                                       sizeof(input_type), cuda_buffer::type),
                gr::io_signature::make(1 /* min outputs */, 1 /*max outputs */, 
                                       sizeof(output_type), cuda_buffer::type))
{
    . . . 
}

```

  1. Finally, the pointers passed to the block's work function will now be of the selected type. For the `cuda_buffer` class used in this example the pointers passed to the work function are not host accessible and should not be dereferenced on the host. Instead the pointers should be passed to a kernel invocation. Note that the pointer usage restrictions depend on the buffer class being used.



```
int new_block_impl::general_work(int noutput_items,
                                 gr_vector_int& ninput_items,
                                 gr_vector_const_void_star& input_items,
                                 gr_vector_void_star& output_items)
{
    // NOTE: in and out are *not* host accessible 
    const auto in = reinterpret_cast<const input_type*>(input_items[0]);
    auto out = reinterpret_cast<output_type*>(output_items[0]);

    // Launch kernel passing in and out as parameters
    . . .
}

```

## Benchmarks
Benchmark methodology and results are presented [Benchmark-Data](https://wiki.gnuradio.org/index.php?title=Here&action=edit&redlink=1 "Here \(page does not exist\)"). 
## Detailed Design
This section contains detailed design information for the changes introduced in the accelerated block interface. 
### Single Mapped Buffer Abstraction
The ability of the GNU Radio runtime to directly manipulate device-specific (aka "custom") buffers is a key aspect of the accelerated block interface changes. Integrating device-specific buffers into the runtime required several changes, the most significant of which is the refinement of the runtime `buffer` class and the creation of the single mapped buffer abstraction. 
A GNU Radio flow graph contains a series of blocks where each pair of blocks is connected by a buffer. The "upstream" block writes (produces) to the buffer while the "downstream" block reads (consumes) from the buffer. 
[![](https://wiki.gnuradio.org/images/thumb/f/f1/CustomBuffers_BlockAndBuffer.png/1024px-CustomBuffers_BlockAndBuffer.png)](https://wiki.gnuradio.org/index.php?title=File:CustomBuffers_BlockAndBuffer.png)
A GNU Radio buffer may have only one writer but multiple readers. The `buffer` class provides an abstraction over top of the underlying buffer itself. Likewise the `buffer_reader` class provides an abstraction for each reader, one or more `buffer_reader` instances are attached to each `buffer` instance. The underlying buffers, which the `buffer` class wraps, are very elegantly implemented circular buffers. The `vmcircbuf` class provides the circular buffer interface although several implementations exist. Fundamentally the `vmcircbuf` interface relies on virtual memory "double mapping" to provide the illusion of an automatically wrapping memory buffer (see <https://www.gnuradio.org/blog/2017-01-05-buffers/> for additional details). This approach works very well for host buffers where the virtual memory mapping to the underlying physical memory can be manipulated but does not work well for device-specific buffers where the virtual memory mapping cannot be manipulated. 
The single mapped buffer abstraction was created to provide a similar encapsulation for underlying buffers whose virtual memory mapping cannot be manipulated. This applies to the majority, if not all, device-specific buffers. One side effect of the single mapped buffer abstraction is that, unlike the traditional double mapped buffers, single mapped buffers require explicit management to handle wrapping from the end of the buffer back to the beginning. For trivial cases where the item consumption or production size aligns with the size of the buffer the `index_add()` and `index_sub()` functions handle wrapping automatically. However for more complex wrapping cases two callback functions are used to handle wrapping; they are described in detail in the [Callback Functions](https://wiki.gnuradio.org/index.php?title=CustomBuffers#callback-functions) section below. 
Some refactoring was necessary to accommodate the new single mapped buffer abstraction alongside the existing double mapped buffer abstraction. The existing `buffer` class was refactored to be an abstract base class for underlying buffer wrapper abstractions. It provides the interface that those abstractions use to hook into the GNU Radio runtime. The `buffer_double_mapped` and `buffer_single_mapped` classes now derive from the `buffer` class and implement its interface (see the simplified class diagram below). The `buffer_double_mapped` class, as its name suggests, contains the double mapped buffer abstraction that was previously contained within the `buffer` class. The `buffer_single_mapped` class contains the new single mapped buffer abstraction that was added as part of the accelerated block interface changes. 
[![](https://wiki.gnuradio.org/images/thumb/6/65/CustomBuffers_BufferHierarchy.png/1024px-CustomBuffers_BufferHierarchy.png)](https://wiki.gnuradio.org/index.php?title=File:CustomBuffers_BufferHierarchy.png)
The `buffer_single_mapped` class is itself an abstract class that represents the interface for single mapped buffers. In the diagram above the interface functions are listed in the gray box. Functions that are pure virtual, that is functions that must be overridden, are listed in bold. The remaining non-bold functions are all virtual, that is they may be overridden if specific customization is necessary. Device-specific "custom buffers" must derive from the `buffer_single_mapped` class and implement the interface, which includes the functions listed in bold at a minimum. Note that the `host_buffer` class is an example implementation of the interface using host buffers. Additional information about it is available in the [host_buffer Class](https://wiki.gnuradio.org/index.php?title=CustomBuffers#host_buffer-class) section below. The `cuda_buffer` and `hip_buffer` classes also derive from the `buffer_single_mapped` class but they reside externally in separate OOT modules. 
[![](https://wiki.gnuradio.org/images/thumb/a/a1/CustomBuffers_BufferReaderHierarchy.png/1024px-CustomBuffers_BufferReaderHierarchy.png)](https://wiki.gnuradio.org/index.php?title=File:CustomBuffers_BufferReaderHierarchy.png)
Additional minor refactoring of the `buffer_reader` class was also necessary to support the single mapped buffer abstraction. The `buffer_reader_sm` ("sm" for single mapped) was created to customize the behavior of several `buffer_reader` functions when used with `buffer_single_mapped` derived classes. To support this, several functions in the `buffer_reader` class were marked virtual. The `buffer_reader_sm` then derives from `buffer_reader` and overrides those virtual functions. The `buffer_reader` class was also slightly refactored to redirect most calls back to the corresponding `buffer` object where possible. This refactoring eliminated the need to create a specific `buffer_reader` derived class to accompany each `buffer_single_mapped` derived class. That is, it should be possible to use `buffer_reader_sm` with any `buffer_single_mapped` derived class. For example, the `host_buffer`, `cuda_buffer`, and `hip_buffer` classes all utilized `buffer_reader_sm` without requiring their own corresponding specific derived classes. 
### Device Buffer Encapsulation and Data Movement
The single mapped buffer abstraction described above provides a suitable abstraction for managing device-specific buffers. However additional functionality to support data movement is needed to incorporate accelerated blocks into GNU Radio. This functionality was also added to the `buffer_single_mapped` class. 
[![](https://wiki.gnuradio.org/images/thumb/d/d0/CustomBuffers_Flowgraph.png/1024px-CustomBuffers_Flowgraph.png)](https://wiki.gnuradio.org/index.php?title=File:CustomBuffers_Flowgraph.png)
The diagram above shows a conceptual representation of an accelerated block within a simplified GNU Radio flowgraph. In the lower portion of the diagram the underlying buffer contained within the `buffer` object spanning the host to accelerator device boundary is shown as split into two pieces with a red arrow connecting the two. Each piece represents an underlying buffer residing on the host and accelerator device respectively. The red arrow represents the data movement path between underlying buffers across the host to accelerator device (and vice versa) boundary. `buffer_single_mapped` derived classes use the same arrangement shown in the lower portion of the diagram; they wrap two underlying buffers, a host buffer and a device-specific buffer, and the newly added `post_work()` function is used to explicitly move data between the two buffers. Note that the host buffer is provided directly by default by the `buffer_single_mapped` class while the device-specific buffer must be implemented in the derived class. 
The `post_work()` function was added to the `buffer` class interface. It is called in the `block_executor::run_one_iteration()` function after a block's `work()` function has been called and is responsible for performing the necessary device-specific data movement between the encapsulated underlying buffers. The `post_work()` function uses a buffer's assigned "context" (see [Replace Upstream](https://wiki.gnuradio.org/index.php?title=CustomBuffers#replace-upstream) for details on buffer context) in order to conditionally move data between the buffers. Also note that the accelerated block interface supports back-to-back accelerated blocks provided that they reside on the same accelerator device. In this case the `post_work()` may simply perform a "no-op" as data may not need to be moved at all in order to be accessible by the downstream block (this concept is also called "zero copy"). 
Below is a simplified (error checking removed for brevity) version of the `post_work()` function from the `cuda_buffer` class: 

```
void cuda_buffer::post_work(int nitems)
{
    if (nitems <= 0) {
        return;
    }

    // NOTE: when this function is called the write pointer has not yet been
    // advanced so it can be used directly as the source ptr
    switch (d_context) {
    case transfer_type::HOST_TO_DEVICE: {
        // Copy data from host buffer to device buffer
        void* dest_ptr = &d_cuda_buf[d_write_index * d_sizeof_item];
        cudaMemcpy(dest_ptr, write_pointer(), nitems * d_sizeof_item, cudaMemcpyHostToDevice);
    } break;

    case transfer_type::DEVICE_TO_HOST: {
        // Copy data from device buffer to host buffer
        void* dest_ptr = &d_base[d_write_index * d_sizeof_item];
        cudaMemcpy(dest_ptr, write_pointer(), nitems * d_sizeof_item, cudaMemcpyDeviceToHost);
    } break;

    case transfer_type::DEVICE_TO_DEVICE:
        // No op FTW!
        break;

    default:
        std::ostringstream msg;
        msg << "Unexpected context for cuda_buffer: " << d_context;
        GR_LOG_ERROR(d_logger, msg.str());
        throw std::runtime_error(msg.str());
    }
}

```

### host_buffer Class
The `host_buffer` class is included as an example `buffer_single_mapped` derived class that is intended to be used for testing in the `qa_host_buffer.cc` test module. As its name suggests the `host_buffer` class implements the `buffer_single_mapped` interface but using only host buffers and therefore does not require any specific accelerator device hardware. 
### Buffer Type
The `buffer_type` type (see `include/gnuradio/buffer_type.h`) was created to provide an easy mechanism to connect blocks with the potentially custom buffers that they intend to use. Furthermore `buffer_type` is intended do this in a type consistent way such that arbitrary `buffer_single_mapped` derived classes implementing "custom buffers" can be added easily via OOT modules. In addition to providing type information `buffer_type` instances also provide a mechanism, via a pointer to a simple factory function, to create the corresponding `buffer` derived class instance. 
The `buffer_type` type itself is defined as: 

```
typedef const buffer_type_base& buffer_type;

```

Each `buffer_type` instance is therefore a constant reference to singleton instance of a class derived from `buffer_type_base`. A macro function: `MAKE_CUSTOM_BUFFER_TYPE(<classname>, <factory function pointer>)` was created to facilitate easy creation of `buffer_type` clasess. The first argument is the desired name of the derived class, it must be unique for each unique buffer type. Within the macro function the class name argument will be prefixed with `buftype_` resulting in a class named `buftype_<classname>`. The second argument to the macro function is a pointer of type `factory_func_ptr` to a simple factory function for creating `buffer` derived class instances of the corresponding type. Although not required it is recommended that the factory function be made a static function within the `buffer` derived class. Likewise it is also recommended that the `buffer_type` be included as a static member (called `type` by convention) within the `buffer` derived class. 
Below is a simple example showing usage of `MAKE_CUSTOM_BUFFER_TYPE` for the fictional `my_device_buffer` class (`my_device_buffer.h`): 

```
class my_device_buffer : public buffer_single_mapped
{
public:

    static buffer_type type;

    virtual ~my_device_buffer();

    // Required buffer_single_mapped functions omitted for brevity

    /*!
     * \brief Creates a new my_device_buffer object
     *
     * \return pointer to buffer base class
     */
    static buffer_sptr make_my_device_buffer(int nitems,
                                             std::size_t sizeof_item,
                                             uint64_t downstream_lcm_nitems,
                                             uint32_t downstream_max_out_mult,
                                             block_sptr link,
                                             block_sptr buf_owner);

private:

    /*!
     * \brief constructor is private.  Use the static make_my_device_buffer
     * function to create instances.
     */
    my_device_buffer(int nitems,
                     size_t sizeof_item,
                     uint64_t downstream_lcm_nitems,
                     uint32_t downstream_max_out_mult,
                     block_sptr link,
                     block_sptr buf_owner);
};

// Create a buffer type
MAKE_CUSTOM_BUFFER_TYPE(MY_DEVICE, &my_device_buffer::make_my_device_buffer)

```

Within `my_device_buffer.cc`: 

```
buffer_type my_device_buffer::type(buftype_MY_DEVICE{});

```

Note for interface consistency `buffer_type` is used by the `buffer_double_mapped` class too. The `buffer_double_mapped` class's buffer type value is `buftype_DEFAULT_NON_CUSTOM`. It is used transparently by the runtime for all existing blocks. 
### Replace Upstream
By convention GNU Radio blocks are responsible for allocating their (downstream) output buffer(s). Downstream blocks then attach their own `buffer_reader` instances to the corresponding output buffer. This presents a problem for accelerated blocks because most require use of a device-specific buffer for both the input data path and the output data path. To solve this problem additional logic was added to the `flat_flowgraph::connect_block_inputs()` function. The `connect_block_inputs` function iterates over the input connections for a given block and adds the necessary `buffer_reader` instances. It was modified to perform two additional actions: 
  1. First the upstream buffer type is compared to the block's output buffer type. If the buffers' types match or if the blocks's output buffer type is the default (`buffer_double_mapped::type`), no further action is taken. If the block's output buffer and the upstream buffer are both not the default type and not the same then a runtime error is produced. This case means that two incompatible accelerated blocks are connected to each other, which is not permitted. Finally, if the block's output buffer is not the default type but the upstream buffer is the default type, then the upstream block's output buffer (i.e. the upstream buffer) is replaced with a new buffer of the block's own output buffer type. The `replace_buffer()` function was added to the `block` class to handle buffer replacement when appropriate.
  2. In addition to potentially replacing the upstream buffer the `connect_block_inputs()` function also calculates and set each buffer's "context". A buffer's context is represented by the `transfer_type` enumeration and represents the direction of data movement for data within the buffer. There are four possible values:
     * **HOST_TO_HOST** - both the upstream and downstream blocks are normal non-accelerated host blocks; this is the default context for blocks
     * **HOST_TO_DEVICE** - the upstream block is a normal host block while the downstream block is an accelerated block; within the buffer wrapper object data must be moved from the host to a device in order to be consumed by the downstream block
     * **DEVICE_TO_HOST** - the upstream block is an accelerated block while the downstream block is a normal host block; within the buffer wrapper object data must be moved from the device to the host in order to be consumed by the downstream block
     * **DEVICE_TO_DEVICE** - both the upstream and downstream blocks are accelerated blocks; within the buffer wrapper object some device-specific operation (possibly nothing) must be performed in order for the data to be consumed by the downstream block
Note that not all all usages of the accelerated block interface and custom buffers exactly fit the chosen nomenclature. The names chosen were meant to reflect the most common logical use cases.


Additional details about how buffer context is used are described in the [Data Movement](https://wiki.gnuradio.org/index.php?title=CustomBuffers#data-movement) section. 
### Callback Functions
As mentioned above the single mapped buffer abstraction requires explicit logic to handle wrapping from the end of the underlying buffer back to the beginning of the buffer. Two callback functions, `input_blocked_callback()` and `output_blocked_callback()`, were added to the `buffer` class interface to handle non-trivial wrapping cases. 
The "input blocked" case occurs when the desired number of input items cannot be read even though additional input items may be available back at the beginning of the buffer. The `input_blocked_callback()` function of the `buffer_reader` class is called when the number of items available between the current read pointer and the end of a single mapped buffer is less than the minimum number required. It attempts to realign the available input data to be contiguous beginning at the start of the buffer so that it can be read. 
[![](https://wiki.gnuradio.org/images/thumb/e/e0/CustomBuffers_InputBlockedCallback.png/1024px-CustomBuffers_InputBlockedCallback.png)](https://wiki.gnuradio.org/index.php?title=File:CustomBuffers_InputBlockedCallback.png)
In the above example, the reader needs to read three items however only two items are available until the end of the buffer is reached (shown in blue). Note that there are additional items ready to be read located at the beginning of the buffer (shown in green). The first step of the `input_blocked_callback()` is to copy the "down" to make room for the items located at the end of the buffer. Next, the items at the end of the buffer are copied into the now free space at the beginning of the buffer. Finally the read and write pointers are updated to reflect the changes. After the `input_blocked_callback()` has run the input reader is no longer blocked and can now read the three items it needs. 
Similarly the "output blocked" case occurs when the desired number of output items cannot be written to the buffer. The `output_blocked_callback()` function of the `buffer` class is called when the space available between the current write pointer and the end of the buffer is less than the space required. It attempts to copy any unread data back to the beginning of the buffer and then reset the write pointer immediately after the data such that additional space is available for writing. 
[![](https://wiki.gnuradio.org/images/thumb/8/82/CustomBuffers_OutputBlockedCallback.png/1024px-CustomBuffers_OutputBlockedCallback.png)](https://wiki.gnuradio.org/index.php?title=File:CustomBuffers_OutputBlockedCallback.png)
In the above example, the writer needs to write three items however space is only available for two items before the end of the buffer is reached. Note that unread data exists between the read pointer and write pointer (shown in blue). First, the `output_blocked_callback()` copies any unread data from its current location back to the beginning of the buffer. Next, the read and write pointers are adjusted such that the read pointer points to the unread data aligned at the beginning of the buffer and the write pointer points immediately after it. After the `output_blocked_callback()` has run the output reader is no longer blocked and the next three items can be written after the unread data. 
Although it is possible to customize the implementation of the callback functions, it is not recommended. Instead, to connect `buffer_single_mapped` derived classes to the callback functions, two additional functions have been created `input_blocked_callback_logic()` and `output_blocked_callback_logic()`. Each function includes the logic to perform the general steps described in the examples above however the exact functions used for data movement within the buffer are abstracted and must be passed in as parameters (via function pointers) to each function. Specifically, `buffer_single_mapped` derived classes must implement two functions, `memcpy()` and `memmove()` that operate on the underlying device-specific buffer and behave in the same fashion as the standard C library functions of the same names. For convenience a `std::function` pointer type has been defined to be used for these two functions. 

```
typedef std::function<void*(void*, const void*, std::size_t)> mem_func_t;

```

In addition to the data movement functions, the underlying buffer on which to operate is also passed as an argument to the `input_blocked_callback_logic()` and `output_blocked_callback_logic()` functions. The `input_blocked_callback()` and `output_blocked_callback()` functions (which themselves must be implemented `buffer_single_mapped` derived class) must use a buffer's context to determine which underlying buffer pointer and corresponding data movement function pointers should be passed into the `input_blocked_callback_logic()` and `output_blocked_callback_logic()` functions. 
### Custom Lock Interface
Executing the callback functions described above potentially rearranges data and adjusts read and write pointers within a buffer. As such it is important to execute the callback functions safely so that they do not interfere with blocks attempting to read or write data to and from the buffer. Each buffer object is protected by a mutex while the runtime calculates the read and write pointers for a given block's next call to its `work()` function. However, the runtime releases the buffer's mutex and then hands the pointers to the block for use. This means that simply locking the buffer object's mutex is not sufficient to protect it as attached blocks could already be using previously calculated pointers into the buffer even if its mutex is locked. A more sophisticated locking mechanism is needed to protect buffers while callback functions are being executed. 
Several additions were added to the `buffer` class to build such a locking mechanism. First a count of the number of active pointers (either read or write) was added along with mutex protected functions for incrementing and decrementing the counts. The `block_executor::run_one_iteration()` function was then updated to call these functions in the appropriate places before and after the call to a block's `work()` function. Next, a simple flag indicating if a callback function is currently executing was added to the `buffer` class. Finally, the `custom_lock_if` was added to the `buffer` class to provide additional locking logic. The `custom_lock_if` class is an abstract class and the `buffer` class derives from it (this provides a C++-style "interface"). 
The `custom_lock` class implements an [RAII style](https://en.wikipedia.org/wiki/Resource_acquisition_is_initialization) lock (similar to `std::lock_guard`) using a mutex and a pointer to an object implementing the `custom_lock_if`. The `custom_lock_if` includes two functions `on_lock()` and `on_unlock()` that are called when the created `custom_lock` object is locked (constructed) and unlocked (destructed) respectively. The `buffer` class's implementation of `on_lock()` uses a condition variable to wait until the active pointer count is zero and the callback flag is false. The condition variable then locks the passed mutex when the condition is met. This ensures that no blocks have active pointers into the buffer and that no other callback are currently executing on the buffer. Furthermore locking the mutex prevents other blocks from calculating pointers into the buffer until the lock is unlocked and the mutex is released. The custom lock interface as implemented but the `buffer` class provides sufficient protection that allows the `input_blocked_callback()` and `output_blocked_callback()` functions to execute safely. 
## Acknowledgements
The work described herein was performed by David Sorber between August 2020 and August 2021. 
I would like to thank both Joshua Morman and Seth Hitefield for their support and feedback during this project. 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=CustomBuffers&oldid=9001](https://wiki.gnuradio.org/index.php?title=CustomBuffers&oldid=9001)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=CustomBuffers "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=CustomBuffers "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:CustomBuffers&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=CustomBuffers)
  * [View source](https://wiki.gnuradio.org/index.php?title=CustomBuffers&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=CustomBuffers&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/CustomBuffers "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/CustomBuffers "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=CustomBuffers&oldid=9001 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=CustomBuffers&action=info "More information about this page")


  * This page was last edited on 26 October 2021, at 20:45.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


