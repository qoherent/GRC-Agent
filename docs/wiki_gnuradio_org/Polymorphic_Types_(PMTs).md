# Polymorphic Types (PMTs)
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#searchInput)
## Contents
  * [1 Introduction](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Introduction)
    * [1.1 More Complicated Types](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#More_Complicated_Types)
  * [2 PMT Data Type](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#PMT_Data_Type)
  * [3 Inserting and Extracting Data](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Inserting_and_Extracting_Data)
  * [4 Strings](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Strings)
  * [5 Tests and Comparisons](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Tests_and_Comparisons)
  * [6 Dictionaries](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Dictionaries)
  * [7 Vectors](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Vectors)
    * [7.1 BLOB](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#BLOB)
  * [8 Pairs](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Pairs)
  * [9 PDUs](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#PDUs)
  * [10 Serializing and Deserializing](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Serializing_and_Deserializing)
  * [11 Printing](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Printing)
    * [11.1 Collection Notation](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Collection_Notation)
  * [12 Conversion between Python Objects and PMTs](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_%28PMTs%29#Conversion_between_Python_Objects_and_PMTs)


## Introduction
If you came here from a search engine, but are actually looking for documentation, you probably want [this](https://www.gnuradio.org/doc/doxygen/). 
Polymorphic Types (PMTs) are used as the carrier of data, from one block/thread to another, for such things as stream tags and message passing interfaces. PMTs can represent a variety of data ranging from Boolean values to dictionaries. In a sense, PMTs are a way to extend the strict typing of C++ with something more flexible. This page summarizes the most important features and points of Polymorphic Types. For an exhaustive list of PMT features check the source code, specifically the header file [pmt.h](https://gnuradio.org/doc/doxygen/pmt_8h.html). 
Let's dive straight into some Python code and see how we can use PMTs: 

```
 >>> import pmt
 >>> P = pmt.from_long(23)
 >>> type(P)
 <class 'pmt.pmt_python.pmt_base'>
 >>> print P
 23
 >>> P2 = pmt.from_complex(1j)
 >>> type(P2)
 <class 'pmt.pmt_python.pmt_base'>
 >>> print P2
 0+1i
 >>> pmt.is_complex(P2)
 True

```

First, the `pmt` module is imported. We assign two values (`P` and `P2`) with PMTs using the `from_long()` and `from_complex()` calls, respectively. As we can see, they are both of the same type! This means we can pass these variables to C++, and C++ can handle this type accordingly. 
The same code as above in C++ would look like this: 

```
 #include <pmt/pmt.h>
 // [...]
 pmt::pmt_t P = pmt::from_long(23);
 std::cout << P << std::endl;
 pmt::pmt_t P2 = pmt::from_complex(0,1); 
 std::cout << P2 << std::endl;
 std::cout << pmt::is_complex(P2) << std::endl;

```

Two things stand out in both Python and C++: First, we can simply print the contents of a PMT. How is this possible? Well, the PMTs have built-in capabilities to cast their value to a string (this is not possible with all types, though). Second, PMTs know their type, so we can query that, e.g. by calling the `is_complex()` method. 
  

When assigning a non-PMT value to a PMT, we can use the from__datatype_() methods, and use the to__datatype_() methods to convert back: 

```
 pmt::pmt_t P_int = pmt::from_long(42);
 int i = pmt::to_long(P_int);
 pmt::pmt_t P_double = pmt::from_double(0.2);
 double d = pmt::to_double(P_double);
 pmt::pmt_t P_double = pmt::mp(0.2);

```

The last row shows the [pmt::mp()](http://gnuradio.org/doc/doxygen/namespacepmt.html#a90faad6086ac00280e0cfd8bb541bd64) shorthand function. It basically saves some typing, as it infers the correct `from_` function from the given type. 
String types play a bit of a special role in PMTs, as we will see later, and have their own converter: 

```
 pmt::pmt_t P_str = pmt::string_to_symbol("spam");
 pmt::pmt_t P_str2 = pmt::intern("spam");
 std::string str = pmt::symbol_to_string(P_str);

```

The pmt::intern is another way of saying pmt::string_to_symbol. 
See the [PMT docs](https://www.gnuradio.org/doc/doxygen/namespacepmt.html) and the header file [pmt.h](http://gnuradio.org/doc/doxygen/pmt_8h.html) for a full list of conversion functions. 
In Python, we can make use of the dynamic typing, and there's actually a helper function to do these conversions (C++ also has a helper function for converting to PMTs called pmt::mp(), but it's less powerful, and not quite as useful, because types are always strictly known in C++): 

```
 P_int = pmt.to_pmt(42)
 i = pmt.to_python(P_int)
 P_double = pmt.to_pmt(0.2)
 d = pmt.to_double(P_double)

```

On a side note, there are three useful PMT constants, which can be used in both Python and C++ domains. In C++, these can be used as such: 

```
 pmt::pmt_t P_true = pmt::PMT_T;
 pmt::pmt_t P_false = pmt::PMT_F;
 pmt::pmt_t P_nil = pmt::PMT_NIL;

```

In Python: 

```
 P_true = pmt.PMT_T
 P_false = pmt.PMT_F
 P_nil = pmt.PMT_NIL

```

`pmt.PMT_T` and `pmt.PMT_F` are boolean PMT types representing True and False, respectively. The PMT_NIL is like a NULL or None and can be used for default arguments or return values, often indicating an error has occurred. 
To go back to C++ data types, we need to be able to find out the type from a PMT. The family of is__datatype_() methods helps us do that: 

```
 double d;
 if (pmt::is_integer(P)) {
     d = (double) pmt::to_long(P);
 } else if (pmt::is_real(P)) {
     d = pmt::to_double(P);
 } else {
     // We really expected an integer or a double here, so we don't know what to do
     throw std::runtime_error("expected an integer!");
 }

```

It is important to do type checking since we cannot unpack a PMT of the wrong data type. 
We can compare PMTs without knowing their type by using the `pmt::equal()` function: 

```
 if (pmt::eq(P_int, P_double)) {
     std::cout << "Equal!" << std::endl; // This line will never be reached

```

There are more equality functions, which compare different things: [`pmt::eq()`](http://gnuradio.org/doc/doxygen/namespacepmt.html#a5c28635e14287cc0e2f762841c11032f) and [`pmt::eqv()`](http://gnuradio.org/doc/doxygen/namespacepmt.html#a25467c81e1c5f4619a9cabad7a88eed5). We won't need these for this tutorial. 
The rest of this page provides more depth into how to handle different data types with the PMT library. 
### More Complicated Types
PMTs can hold a variety of types. Using the Python method `pmt.to_pmt()`, we can convert most of Pythons standard types out-of-the-box: 

```
P_tuple = pmt.to_pmt((1, 2, 3, 'spam', 'eggs'))
P_dict = pmt.to_pmt({'spam': 42, 'eggs': 23})
```

But what does this mean in the C++ domain? Well, there are PMT types that define [tuples](http://gnuradio.org/doc/doxygen/namespacepmt.html#a32895cc5a614a46b66b869c4a7bd283c) and [dictionaries](http://gnuradio.org/doc/doxygen/namespacepmt.html#aba10563e3ab43b8d52f9cb13132047cf), keys and values being PMTs, again.  
So, to create the tuple from the Python example, the C++ code would look like this: 

```
pmt::pmt_t P_tuple = pmt::make_tuple(pmt::from_long(1), pmt::from_long(2), pmt::from_long(3), pmt::string_to_symbol("spam"), pmt::string_to_symbol("eggs"))
```

For the dictionary, it's a bit more complex: 

```
pmt::pmt_t P_dict = pmt::make_dict();
P_dict = pmt::dict_add(P_dict, pmt::string_to_symbol("spam"), pmt::from_long(42));
P_dict = pmt::dict_add(P_dict, pmt::string_to_symbol("eggs"), pmt::from_long(23));
```

As you can see, we first need to create a dictionary, then assign every key/value pair individually. 
A variant of tuples are _vectors_. Like Python's tuples and lists, PMT vectors are mutable, whereas PMT tuples are not. In fact, PMT vectors are the only PMT data types that are mutable. When changing the value or adding an item to a dictionary, we are actually creating a new PMT. 
To create a vector, we can initialize it to a certain lengths, and fill all elements with an initial value. We can then change items or reference them: 

```
pmt::pmt_t P_vector = pmt::make_vector(5, pmt::from_long(23)); // Creates a vector with 5 23's as PMTs
pmt::vector_set(P_vector, 0, pmt::from_long(42)); // Change the first element to a 42
std::cout << pmt::vector_ref(P_vector, 0); // Will print 42
```

In Python, we can do all these steps (using `pmt.make_vector()` etc.), or convert a list: 

```
P_vector = pmt.to_pmt([42, 23, 23, 23, 23])
```

Vectors are also different from tuples in a sense that we can **directly** load data types into the elements, which don't have to be PMTs.  
Say we want to pass a series of 8 float values to another block (these might be characteristics of a filter, for example). It would be cumbersome to convert every single element to and from PMTs, since all elements of the vector are the same type. 
We can use special vector types for this case: 

```
pmt::pmt_t P_f32vector = pmt::make_f32vector(8, 5.0); // Creates a vector with 8 5.0s as floats
pmt::f32vector_set(P_f32vector, 0, 2.0); // Change the first element to a 2.0
float f = f32vector_ref(P_f32vector, 0);
std::cout << f << std::endl; // Prints 2.0
size_t len;
float *fp = pmt::f32vector_elements(P_f32vector, len);
for (size_t i = 0; i < len; i++)
    std::cout << fp[i] << std::endl; // Prints all elements from P_f32vector, one after another
```

Python has a similar concept: [Numpy arrays](http://docs.scipy.org/doc/numpy/reference/generated/numpy.array.html). As usual, the PMT library understands this and converts as expected: 

```
P_f32vector = pmt.to_pmt(numpy.array([2.0, 5.0, 5.0, 5.0, 5.0], dtype=numpy.float32))
print pmt.is_f32vector(P_f32vector) # Prints 'True'
```

Here, 'f32' stands for 'float, 32 bits'. PMTs know about most typical fixed-width data types, such as 'u8' (unsigned 8-bit character) or 'c32' (complex with 32-bit floats for each I and Q). Consult the [manual](http://gnuradio.org/doc/doxygen/namespacepmt.html) for a full list of types. 
The most generic PMT type is probably the blob (binary large object). Use this with care - it allows us to pass around anything that can be represented in memory. 
## PMT Data Type
All PMTs are of the type pmt::pmt_t. This is an opaque container and PMT functions must be used to manipulate and even do things like compare PMTs. PMTs are also _immutable_ (except PMT vectors). We never change the data in a PMT; instead, we create a new PMT with the new data. The main reason for this is thread safety. We can pass PMTs as tags and messages between blocks and each receives its own copy that we can read from. However, we can never write to this object, and so if multiple blocks have a reference to the same PMT, there is no possibility of thread-safety issues of one reading the PMT data while another is writing the data. If a block is trying to write new data to a PMT, it actually creates a new PMT to put the data into. Thus we allow easy access to data in the PMT format without worrying about mutex locking and unlocking while manipulating them. 
PMTs can represent the following: 
  * Boolean values of true/false
  * Strings (as symbols)
  * Integers (long and uint64)
  * Floats (as doubles)
  * Complex (as two doubles)
  * Pairs
  * Tuples
  * Vectors (of PMTs)
  * Uniform vectors (of any standard data type)
  * Dictionaries (list of key:value pairs)
  * Any (contains a boost::any pointer to hold anything)


The PMT library also defines a set of functions that operate directly on PMTs such as: 
  * Equal/equivalence between PMTs
  * Length (of a tuple or vector)
  * Map (apply a function to all elements in the PMT)
  * Reverse
  * Get a PMT at a position in a list
  * Serialize and deserialize
  * Printing


The constants in the PMT library are: 
  * pmt::PMT_T - a PMT True
  * pmt::PMT_F - a PMT False
  * pmt::PMT_NIL - an empty PMT (think Python's 'None')


## Inserting and Extracting Data
Use [pmt.h](https://gnuradio.org/doc/doxygen/pmt_8h.html) for a complete guide to the list of functions used to create PMTs and get the data from a PMT. When using these functions, remember that while PMTs are opaque and designed to hold any data, the data underneath is still a C++ typed object, and so the right type of set/get function must be used for the data type. 
Typically, a PMT object can be made from a scalar item using a call like "pmt::from_<type>". Similarly, when getting data out of a PMT, we use a call like "pmt::to_<type>". For example: 

```
 double a = 1.2345;
 pmt::pmt_t pmt_a = pmt::from_double(a);
 double b = pmt::to_double(pmt_a);
 
 int c = 12345;
 pmt::pmt_t pmt_c = pmt::from_long(c);
 int d = pmt::to_long(pmt_c);

```

As a side-note, making a PMT from a complex number is not obvious: 

```
 std::complex<double> a(1.2, 3.4);
 pmt::pmt_t pmt_a = pmt::make_rectangular(a.real(), a.imag());
 std::complex<double> b = pmt::to_complex(pmt_a);

```

Pairs, dictionaries, and vectors have different constructors and ways to manipulate them, and these are explained in their own sections. 
## Strings
PMTs have a way of representing short strings. These strings are actually stored as interned symbols in a hash table, so in other words, only one PMT object for a given string exists. If creating a new symbol from a string, if that string already exists in the hash table, the constructor will return a reference to the existing PMT. 
We create strings with the following functions, where the second function, pmt::intern, is simply an alias of the first. 

```
 pmt::pmt_t str0 = pmt::string_to_symbol(std::string("some string"));
 pmt::pmt_t str1 = pmt::intern(std::string("some string"));

```

The string can be retrieved using the inverse function: 

```
 std::string s = pmt::symbol_to_string(str0);

```

## Tests and Comparisons
The PMT library comes with a number of functions to test and compare PMT objects. In general, for any PMT data type, there is an equivalent "pmt::is_<type>". We can use these to test the PMT before trying to access the data inside. Expanding our examples above, we have: 

```
 pmt::pmt_t str0 = pmt::string_to_symbol(std::string("some string"));
 if(pmt::is_symbol(str0))
     std::string s = pmt::symbol_to_string(str0);
 
 double a = 1.2345;
 pmt::pmt_t pmt_a = pmt::from_double(a);
 if(pmt::is_double(pmt_a))
     double b = pmt::to_double(pmt_a);
 
 int c = 12345;
 pmt::pmt_t pmt_c = pmt::from_long(c);
 if(pmt::is_long(pmt_c))
     int d = pmt::to_long(pmt_c);
 
 \\ This will fail the test. Otherwise, trying to coerce pmt_c as a
 \\ double when internally it is a long will result in an exception.
 if(pmt::is_double(pmt_c))
     double d = pmt::to_double(pmt_c);

```

## Dictionaries
PMT dictionaries are lists of key:value pairs. They have a well-defined interface for creating, adding, removing, and accessing items in the dictionary. Note that every operation that changes the dictionary both takes a PMT dictionary as an argument and returns a PMT dictionary. The dictionary used as an input is not changed and the returned dictionary is a new PMT with the changes made there. 
The following is a list of PMT dictionary functions. View each function in the [GNU Radio C++ Manual](https://www.gnuradio.org/doc/doxygen/index.html) to get more information on what each does. 
  * bool pmt::is_dict(const pmt_t &obj)
  * pmt_t pmt::make_dict()
  * pmt_t pmt::dict_add(const pmt_t &dict, const pmt_t &key, const pmt_t &value)
  * pmt_t pmt::dict_delete(const pmt_t &dict, const pmt_t &key)
  * bool pmt::dict_has_key(const pmt_t &dict, const pmt_t &key)
  * pmt_t pmt::dict_ref(const pmt_t &dict, const pmt_t &key, const pmt_t &not_found)
  * pmt_t pmt::dict_items(pmt_t dict)
  * pmt_t pmt::dict_keys(pmt_t dict)
  * pmt_t pmt::dict_values(pmt_t dict)


This example does some basic manipulations of PMT dictionaries in Python. Notice that we pass the dictionary _a_ and return the results to _a_. This still creates a new dictionary and removes the local reference to the old dictionary. This just keeps our number of variables small. 

```
 import pmt
 
 key0 = pmt.intern("int")
 val0 = pmt.from_long(123)
 val1 = pmt.from_long(234)
 
 key1 = pmt.intern("double")
 val2 = pmt.from_double(5.4321)
 
 # Make an empty dictionary
 a = pmt.make_dict()
 
 # Add a key:value pair to the dictionary
 a = pmt.dict_add(a, key0, val0)
 print a
 
 # Add a new value to the same key;
 # new dict will still have one item with new value
 a = pmt.dict_add(a, key0, val1)
 print a
 
 # Add a new key:value pair
 a = pmt.dict_add(a, key1, val2)
 print a
 
 # Test if we have a key, then delete it
 print pmt.dict_has_key(a, key1)
 a = pmt.dict_delete(a, key1)
 print pmt.dict_has_key(a, key1)
 
 ref = pmt.dict_ref(a, key0, pmt.PMT_NIL)
 print ref
 
 # The following should never print
 if(pmt.dict_has_key(a, key0) and pmt.eq(ref, pmt.PMT_NIL)):
     print "Trouble! We have key0, but it returned PMT_NIL"

```

## Vectors
PMT vectors come in two forms: vectors of PMTs and vectors of uniform data. The standard PMT vector is a vector of PMTs, and each PMT can be of any internal type. On the other hand, uniform PMTs are of a specific data type which come in the form: 
  * (u)int8
  * (u)int16
  * (u)int32
  * (u)int64
  * float32
  * float64
  * complex 32 (std::complex<float>)
  * complex 64 (std::complex<double>)


That is, the standard sizes of integers (both signed and unsigned), floats, and complex types. 
Vectors have a well-defined interface that allows us to make, set, get, and fill them. We can also get the length of a vector with "pmt::length": 

```
pmt_t p1 = pmt_integer(1);
pmt_t p2 = pmt_integer(2);
pmt_t p3 = pmt_integer(3);

pmt_t p_list = pmt_list3(p1, p2, p3);

pmt_length(p_list);  // Returns 3

```

For standard vectors, these functions look like: 
  * bool pmt::is_vector(pmt_t x)
  * pmt_t pmt::make_vector(size_t k, pmt_t fill)
  * pmt_t pmt::vector_ref(pmt_t vector, size_t k)
  * void pmt::vector_set(pmt_t vector, size_t k, pmt_t obj)
  * void pmt::vector_fill(pmt_t vector, pmt_t fill)


Uniform vectors have the same types of functions, but they are data type-dependent. The following list illustrates where to substitute the specific data type prefix for (dtype) (prefixes being: u8, u16, u32, u64, s8, s16, s32, s64, f32, f64, c32, c64). 
  * bool pmt::is_(dtype)vector(pmt_t x)
  * pmt_t pmt::make_(dtype)vector(size_t k, (dtype) fill)
  * pmt_t pmt::init_(dtype)vector(size_t k, const (dtype*) data)
  * pmt_t pmt::init_(dtype)vector(size_t k, const std::vector<dtype> data)
  * pmt_t pmt::(dtype)vector_ref(pmt_t vector, size_t k)
  * void pmt::(dtype)vector_set(pmt_t vector, size_t k, (dtype) x)
  * const dtype* pmt::(dtype)vector_elements(pmt_t vector, size_t &len)
  * dtype* pmt::(dtype)vector_writable_elements(pmt_t vector, size_t &len)


List of available pmt::is_(dtype)vector(pmt_t x) methods from [pmt.h](https://gnuradio.org/doc/doxygen/pmt_8h.html):  


```
bool pmt::is_uniform_vector()
bool pmt::is_u8vector()
bool pmt::is_s8vector()
bool pmt::is_u16vector()
bool pmt::is_s16vector()
bool pmt::is_u32vector()
bool pmt::is_s32vector()
bool pmt::is_u64vector()
bool pmt::is_s64vector()
bool pmt::is_f32vector()
bool pmt::is_f64vector()
bool pmt::is_c32vector()
bool pmt::is_c64vector()


```

**Note:** We break the contract with vectors. The 'set' functions actually change the data underneath. It is important to keep track of the implications of setting a new value as well as accessing the 'vector_writable_elements' data. Since these are mostly standard data types, sets and gets are atomic, so it is unlikely to cause a great deal of harm. But it's only unlikely, not impossible. Best to use mutexes whenever manipulating data in a vector. 
### BLOB
A BLOB is a 'binary large object' type. In PMT's, this is actually just a thin wrapper around a u8vector. 
## Pairs
A concept that originates in Lisp dialects are [_pairs_ and _cons_](https://en.wikipedia.org/wiki/Cons). The simplest explanation is just that: If you combine two PMTs, they form a new PMT, which is a pair (or cons) of those two PMTs (don't worry about the weird name, a lot of things originating in Lisp have weird names. Think of a 'construct'). 
Similarly to vectors or tuples, pairs are a useful way of packing several components of a message into a single PMT. Using the PMTs generated in the previous section, we can combine two of these to form a pair, here in Python: 

```
P_pair = pmt.cons(pmt.string_to_symbol("taps"), P_f32vector)
print pmt.is_pair(P_pair) # Prints 'true'
```

You can combine PMTs as tuples, dictionaries, vectors, or pairs, it's just a matter of taste. This construct is well-established though, and as such used in GNU Radio quite often. 
So how do we deconstruct a pair? That's what the `car` and `cdr` functions do. Let's deconstruct that previous pair in C++: 

```
pmt::pmt_t P_key = pmt::car(P_pair);
pmt::pmt_t P_f32vector2 = pmt::cdr(P_pair);
std::cout << P_key << std::endl; // Will print 'taps' using the PMT automatic conversion to strings
```

Here is a summary of the pairs-related functions in C++ and Python: 
  * bool pmt::is_pair(const pmt_t &obj): Return true if obj is a pair, else false
  * pmt_t pmt::cons(const pmt_t &x, const pmt_t &y): construct new pair
  * pmt_t pmt::car(const pmt_t &pair): get the car of the pair (first object)
  * pmt_t pmt::cdr(const pmt_t &pair): get the cdr of the pair (second object)
  * void pmt::set_car(pmt_t pair, pmt_t value): Stores value in the car field
  * void pmt::set_cdr(pmt_t pair, pmt_t value): Stores value in the cdr field


And in Python we have: 

```
 pmt.is_pair(pair_obj) # Return True if is a pair, else False (warning: also returns True for a dict)
 pmt.cons(x, y) # Return a newly allocated pair whose car is x and whose cdr is y
 pmt.car(pair_obj) # If is a pair, return the car, otherwise raise wrong_type
 pmt.cdr(pair_obj) # If is a pair, return the cdr, otherwise raise wrong_type
 pmt.set_car(pair_obj, value) # Store value in car field
 pmt.set_cdr(pair_obj, value) # Store value in cdr field

```

For more advanced pair manipulation, refer to the [documentation](http://gnuradio.org/doc/doxygen/namespacepmt.html#a7ab95721db5cbda1852f13a92eee5362) and the [Wikipedia page for car and cdr](https://en.wikipedia.org/wiki/Car_and_cdr). 
## PDUs
A PDU is a special type of PMT object that is a PMT pair consisting of a dictionary and a uniform vector. PMTs are used to pass complete segments of data accompanied by metadata describing that data as a single object. The dictionary can contain any key/value pairs useful to describe the data, though there are a number of common keys built into GNU Radio. Any uniform vector type can be used, but complex, float, int, short, and byte are the most common vector types with the best support. 
Here are some examples: 
  * Create a PDU with no metadata and 10 zeros as stream data:


```
pdu = pmt.cons(pmt.make_dict(), pmt.make_u8vector(10, 0))

```

  * Create a PDU from a Python array:


```
idle = [127,127,127]
idle_len = len(idle)
pdu = pmt.cons(pmt.PMT_NIL,pmt.init_u8vector(idle_len,(idle)))

```

  * This snippet converts a PMT string to a PDU:


```
if not pmt.is_symbol(msg):
    print ("PMT is not a symbol")
    return
u8_data = [ord(i) for i in pmt.symbol_to_string (msg)]
pdu = pmt.cons(pmt.PMT_NIL,pmt.init_u8vector(len(u8_data), u8_data))

```

## Serializing and Deserializing
It is often important to hide the fact that we are working with PMTs to make them easier to transmit, store, write to file, etc. The PMT library has methods to serialize data into a string buffer or a string and then methods to deserialize the string buffer or string back into a PMT. We use this extensively in the metadata files (see [Metadata Information](https://wiki.gnuradio.org/index.php?title=Metadata_Information "Metadata Information")). 
  * bool pmt::serialize(pmt_t obj, std::streambuf &sink)
  * std::string pmt::serialize_str(pmt_t obj)
  * pmt_t pmt::deserialize(std::streambuf &source)
  * pmt_t pmt::deserialize_str(std::string str)


For example, we will serialize the data above to make it into a string ready to be written to a file and then deserialize it back to its original PMT. 

```
 import pmt
 
 key0 = pmt.intern("int")
 val0 = pmt.from_long(123)
 
 key1 = pmt.intern("double")
 val1 = pmt.from_double(5.4321)
 
 # Make an empty dictionary
 a = pmt.make_dict()
 
 # Add a key:value pair to the dictionary
 a = pmt.dict_add(a, key0, val0)
 a = pmt.dict_add(a, key1, val1)
 
 print a
 
 ser_str = pmt.serialize_str(a)
 print ser_str
 
 b = pmt.deserialize_str(ser_str)
 print b

```

The line where we 'print ser_str' will print and parts will be readable, but the point of serializing is not to make a human-readable string. This is only done here as a test. 
## Printing
In Python, the __repr__ function of a PMT object is overloaded to call 'pmt::write_string'. This means that any time we call a formatted printing operation on a PMT object, the PMT library will properly format the object for display. 
In C++, we can use the 'pmt::print(object)' function or print the contents using the overloaded "<<" operator with a stream buffer object. In C++, we can inline print the contents of a PMT like: 

```
 pmt::pmt_t a = pmt::from_double(1.0);
 std::cout << "The PMT a contains " << a << std::endl;

```

### Collection Notation
PMTs use a different bracket notation from what one might be used to in Python. 

```
>>> my_dict
((meaning . 42))
>>> my_vector
#[1 2 3 4]
>>> pmt.make_tuple(pmt.from_long(321), pmt.from_float(3.14))
{321 3.14}
>>> pmt.cons(pmt.from_long(1), pmt.from_long(2))
(1 . 2)
>>> my_pdu
(() . #[1 2 3 4])

```

## Conversion between Python Objects and PMTs
Although PMTs can be manipulated in Python using the Python versions of the C++ interfaces, there are some additional goodies that make it easier to work with PMTs in python. There are functions to automate the conversion between PMTs and Python types for booleans, strings, integers, longs, floats, complex numbers, dictionaries, lists, tuples and combinations thereof. 
Two functions capture most of this functionality: 

```
 pmt.to_pmt    # Converts a python object to a PMT.
 pmt.to_python # Converts a PMT into a python object.

```

Retrieved from "[https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_(PMTs)&oldid=14604](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\)&oldid=14604)"
[Category](https://wiki.gnuradio.org/index.php?title=Special:Categories "Special:Categories"): 
  * [Usage Manual](https://wiki.gnuradio.org/index.php?title=Category:Usage_Manual "Category:Usage Manual")


## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Polymorphic+Types+%28PMTs%29 "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\) "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Polymorphic_Types_\(PMTs\)&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\))
  * [View source](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\)&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\)&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Polymorphic_Types_\(PMTs\) "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Polymorphic_Types_\(PMTs\) "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\)&oldid=14604 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Polymorphic_Types_\(PMTs\)&action=info "More information about this page")


  * This page was last edited on 23 December 2024, at 18:16.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


