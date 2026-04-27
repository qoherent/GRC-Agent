# Development
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=Development#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=Development#searchInput)
## Contents
  * [1 Contributing to GNU Radio -- FAQ](https://wiki.gnuradio.org/index.php?title=Development#Contributing_to_GNU_Radio_--_FAQ)
    * [1.1 Cheat Sheet](https://wiki.gnuradio.org/index.php?title=Development#Cheat_Sheet)
    * [1.2 How can I help?](https://wiki.gnuradio.org/index.php?title=Development#How_can_I_help?)
    * [1.3 Development Style](https://wiki.gnuradio.org/index.php?title=Development#Development_Style)
      * [1.3.1 Quality Assurance (QA) Code](https://wiki.gnuradio.org/index.php?title=Development#Quality_Assurance_\(QA\)_Code)
    * [1.4 I've found a bug, but I don't have the skill/time/whatever to fix it... now what?](https://wiki.gnuradio.org/index.php?title=Development#I've_found_a_bug,_but_I_don't_have_the_skill/time/whatever_to_fix_it..._now_what?)
      * [1.4.1 I've fixed an Issue! What do I do now?](https://wiki.gnuradio.org/index.php?title=Development#I've_fixed_an_Issue!_What_do_I_do_now?)
      * [1.4.2 Assignment](https://wiki.gnuradio.org/index.php?title=Development#Assignment)
    * [1.5 How can I add new features to GNU Radio?](https://wiki.gnuradio.org/index.php?title=Development#How_can_I_add_new_features_to_GNU_Radio?)
    * [1.6 Which kind of patches are accepted into GNU Radio?](https://wiki.gnuradio.org/index.php?title=Development#Which_kind_of_patches_are_accepted_into_GNU_Radio?)
    * [1.7 How long does it take for my patch to become part of GNU Radio?](https://wiki.gnuradio.org/index.php?title=Development#How_long_does_it_take_for_my_patch_to_become_part_of_GNU_Radio?)
    * [1.8 What's this CGRAN?](https://wiki.gnuradio.org/index.php?title=Development#What's_this_CGRAN?)
    * [1.9 Which coding conventions apply?](https://wiki.gnuradio.org/index.php?title=Development#Which_coding_conventions_apply?)
    * [1.10 How is the code documented?](https://wiki.gnuradio.org/index.php?title=Development#How_is_the_code_documented?)
      * [1.10.1 Doxygen Markup for Formulas](https://wiki.gnuradio.org/index.php?title=Development#Doxygen_Markup_for_Formulas)
    * [1.11 What's this Developer Certificate of Origin (DCO)?](https://wiki.gnuradio.org/index.php?title=Development#What's_this_Developer_Certificate_of_Origin_\(DCO\)?)
    * [1.12 Who maintains GNU Radio?](https://wiki.gnuradio.org/index.php?title=Development#Who_maintains_GNU_Radio?)


# Contributing to GNU Radio -- FAQ
GNU Radio is an Open Source project, and as such contributions from the community are welcome. If you have some code or other things such as documentation you would like to share with the rest, please have a look at this FAQ before submitting. 
## Cheat Sheet
See the information throughout this page for details about these steps. But when looking to contribute, we encourage you to either make a new issue in the Issue Tracker or identify a current Issue already up and waiting for attention. We also assume you have a public GitHub repo you can use for pull requests and that you have forked the GNU Radio git repo. 
  * Identify which branch of the GNU Radio base code to work from. Most of the time, that's main.
  * Create a new branch from here to work from.
  * Work on the bug fix or feature under the new branch.
  * Determine the QA code necessary to test and check the fix or feature.
  * Provide (an) appropriate Git commit(s) with well-formatted log messages.
  * Push the branch to a public repo on Github.
  * Issue a pull request against the base branch (i.e., main).
  * If you're in the gr-dev group, please update the labels. If you are planning on doing more GNU Radio development, please request access to that group.
  * Update the Issue on [gnuradio/issues](https://github.com/gnuradio/gnuradio/issues) with information about the fix, including a link to the pull request on Github.
  * Adding **fixes #issue_number** or **closes #issue_number** to the bottom of the appropriate commit message leverages [GitHub's automatic issue closing](https://help.github.com/articles/closing-issues-using-keywords/).
  * Wait for the pull request to be merged -- often after some back and forth and testing by other developers.
  * Mark the Issue resolved and note the commit where it was resolved.


The GNU Radio project maintainers will mark issues as Closed at the appropriate time. 
## How can I help?
The easiest way to help is to simply use GNU Radio. The more you use it, the more likely you will perhaps find a bug, or miss a particular feature. Tell us about it on the mailing list, or, even better, fix it yourself and submit the code. The most rewarding way to go is probably to actually write a radio system, e.g. a receiver for a digital standard. 
If you want to get involved in the development process, here's some suggestions where to start: 
  * Documentation
  * Missing test cases
  * Report and reproduce bugs
  * Bug fixes
  * Review pull requests
  * Implement new features


The main place to look is the issue tracker: <https://github.com/gnuradio/gnuradio/issues>. It tracks warnings, TODO's, FIXME's, and unit tests. Any warnings or FIXME fixes are useful as well as any more unit tests to exercise the validity of the signal processing or runtime code. Issues suitable for beginners are tagged ["good first issue"](https://github.com/gnuradio/gnuradio/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22). 
We also use Coverity for static code analysis. You can find our project at [[1]](https://scan.coverity.com/projects). Here, you can find small things to change and fix (a lot of these are so small you don't even need an FSF copyright assignment). 
There's also the possibility to [donate](https://crm.fsf.org/civicrm/contribute/transact?reset=1&id=16) to our project. The Free Software Foundation provides the infrastructure to do so, and can provide a receipt for US tax payers, who can deduct a donation. 
## Development Style
Our coding and development style is codified on [GREP1](https://github.com/gnuradio/greps/blob/main/grep-0001-coding-guidelines.md). 
### Quality Assurance (QA) Code
Every block should have a QA file to test the validity of the code. Most current blocks have QA code that tests that the code runs and produces repeatable, testable results on all platforms. If this code fails, it is an indication of a problem, error, or unexpected change in the code. It is an easy way to verify the validity of the code. This can be a difficult concept in some signal processing codes (such as a phase lock loop that has to settle), but the current QA tests that are there can offer guidance and hints as to how to test a new block. 
Any block that is currently without a QA testing program should be fixed to have one. 
## I've found a bug, but I don't have the skill/time/whatever to fix it... now what?
No problem. If it's really an unfixed bug, you're perhaps the first to stumble upon it. Write an email to the discuss-gnuradio@gnu.org mailing list explaining what's wrong and, ideally, providing a test case so the developers can reproduce the error. 
If it's a confirmed bug, you should add a ticket to the [GNU Radio issue tracker](https://github.com/gnuradio/gnuradio/issues) by following these steps: 
1. If you don't already have one, create an account on GitHub.  
2. You'll need to login to GitHub, first, and then click New Issue.  
3. Provide an appropriate subject to explain the Issue (shorter than 72 characters).  
4. Provide a comprehensive description of the Issue. Provide links, code, and any examples here. It's much easier for us to understand a bug if we have a simple test case that demonstrates the issue such as a GRC file or Python program. Feel free to upload attachments using the Files button.  

### I've fixed an Issue! What do I do now?
That's fantastic! Thanks! To help us provide a consistent experience resolving and fixing issues, please follow these steps. 
1. Fork the [GNU Radio project repository](https://github.com/gnuradio/gnuradio) to your personal GitHub development space.  
2. Create a new git branch based on main or dev-4.0 and add commits which fix the issue you found.  
3. Push the new feature branch to your personal fork of the GNU Radio project repository.  
4. Create a new pull request on GitHub by visiting the [GNU Radio pull request page](https://github.com/gnuradio/gnuradio/pulls) and clicking New pull request, then select the appropriate base branch in the GNU Radio project (main or dev-4.0) and select your feature branch to compare with from your personal fork .  

The GNU Radio development team will take the issue from here. We will handle merging the fix and updating the issue from there. We will need your help to resolve conflicts and issues regarding your code. Communication will happen solely in the GitHub pull request comments to keep conversation and code close together. 
### Assignment
Assignment to an Issue means that an individual has taken on the task of handling that particular issue of either fixing a bug or adding a feature. When assigned, that issue is claimed by the assignee, and so we expect that person to handle the task. Assigned issues that are not kept up on will be either removed or the assignee will be taken off. Removing the issue will generally occur with a feature request where that feature is not being managed properly. If it is a bug, inaction on it means that others may be discouraged from working on this because it is already assigned. 
## How can I add new features to GNU Radio?
First of all, you should ask yourself if your code really belongs into the GNU Radio core. If you are developing a GNU Radio module which can exist completely separate of the GNU Radio code, it might be worth uploading it to CGRAN, where you can maintain the code yourself and don't have to go through the process of re-submitting patches. 
If you're having any trouble with this, don't hesitate to ask on the mailing list. 
## Which kind of patches are accepted into GNU Radio?
There is no definitive answer to this. Bug fixes and missing QA codes are of course always welcome. 
Ultimately, the decision lies with the maintainers. If in doubt, consult the mailing list. 
## How long does it take for my patch to become part of GNU Radio?
Again, there is no definitive answer to this. It depends on many things: the complexity and size of the patch, the current situation of development and the relevance of the patch. 
However, the following things are guaranteed to delay acceptance: 
  * Lack of documentation and/or test cases
  * Not complying with the [coding conventions](https://wiki.gnuradio.org/index.php?title=Coding_guide_impl "Coding guide impl")
  * Non-portable code
  * QA failures
  * Lack of [copyright assignment](https://wiki.gnuradio.org/index.php?title=Development#Whats-this-Copyright-Assignment)


## What's this CGRAN?
The Comprehensive GNU Radio Archive Network ([CGRAN](http://cgran.org/)) is a free open source repository for 3rd party GNU Radio applications that are not officially supported by the GNU Radio project _. In other words, it is a place for anybody to upload and publish extensions and modifications of and for GNU Radio._
If you are developing a GNU Radio project which works separately from the GNU Radio core, you might want to submit it to CGRAN rather than to the GNU Radio core. This way, you keep the write access to your published code and can maintain it independently from the core. 
## Which coding conventions apply?
See [Coding guidelines](https://github.com/gnuradio/greps/blob/master/grep-0001-coding-guidelines.md). 
## How is the code documented?
GNU Radio uses [Doxygen](http://www.stack.nl/~dimitri/doxygen) to document the source code. Any new block should use Doxygen markup structure to add to the Doxygen manual. Also, we use a list of groups to categorize all of the blocks, so when a new block is created, add this block to one or more of the available groups, a list of which can be found in `docs/doxygen/other/group_defs.dox`. Below is an example of a marked-up header file. 

```
/*!
 * \brief A new block that does something.
 * \ingroup some_group
 * \ingroup another_group
 *
 * Detailed description of what this block does.
 * Quoting papers or textbooks is a good idea, too.
 */
class MODNAME_API new_block : public gr::block
{
 private:
  new_block(int param1, double param2);

 public:
  typedef boost::shared_ptr sptr;

  /*!
   * \brief Description of public_function()
   * \param foo Describe foo
   * \param bar Describe bar
   */
  virtual int public_function(int foo, float bar) = 0;

  /*!
   * \param param1 Describe param1
   * \param param2 Describe param2
   */
  static sptr make(int param1, double param2);

};
```

The top level blocks are all described inside that blocks `doc` directory in a `.dox` files named or the component. For example, in the component `gr-digital`, the manual page describing this component is in `gr-digital/doc/digital.dox`. This page should provide any detail that can help users add and use these packages and their blocks. As these components are developed, used, and added to, any more details that can go into this Doxygen page should be added for the benefit of anyone else. 
The component's `doc` directory will also contain a `README.package` that gives a brief description of the package. This file should contain the most basic necessary information and point to the Doxygen files for more detail. 
### Doxygen Markup for Formulas
When inserting formulas into the header to be part of the documentation, we want to have the best representation possible, which means making Latex style formulas. This is done in Doxygen using the "\f{" to begin and "\f}" to end the formula section. However, these do not properly show up in the XML documents used in the Python help files. So we have to make the formula twice, once formatted for the HTML manual and another for the XML. It will look like this: 

```
This is some text in the header...
\f{html}{
enter Latex formula here -> will only show up in the HTML document.
}

\xmlonly
Same Latex formula, but this will not be processed; will only be the raw Latex formula.
\endxmlonly

And here's some following text to the formulas.
```

Note that the spacing between sections is important to get the best output format. Furthermore, there are certain Doxygen markups that will cause a problem in the XML representation, like use the of \frac in Latex to create a fraction will not parse properly. There is likely a better way to handle this (PLEASE UPDATE IF YOU KNOW IT), but for now, we just add a space between them as "\ f". 
## What's this Developer Certificate of Origin (DCO)?
Any code contributions going into GNU Radio will become part of a GPL-licensed, open source repository. It is therefore imperative that code submissions belong to the authors, and that submitters have the authority to merge that code into the public GNU Radio codebase. 
At the end of 2020, GNU Radio [announced](https://lists.gnu.org/archive/html/discuss-gnuradio/2020-12/msg00107.html) a switch from a Copyright Assignment, which included a CLA, to a [Developer Certificate of Origin (DCO)](https://github.com/gnuradio/gnuradio/blob/main/DCO.txt) requirement to contribute. Since there is no copyright assignment in the DCO, this means that new components of GNU Radio can be upstreamed without copyright assignment. The copyright remains with the author or their company. 
Additional DCO information is included in the GNU Radio [Contributing Guide](https://github.com/gnuradio/gnuradio/blob/main/CONTRIBUTING.md#dco-signed). 
You can sign a DCO using the `git commit -s` command line argument. 
If you forget to sign a single commit, it can be quickly fixed with `git commit --amend --signoff`. If you forget to sign a series of commits, the following can be used to go back and sign all commits up until the provided COMMIT_HASH: 
`git rebase --exec 'git commit --amend --no-edit -n -s' -i COMMIT_HASH`
## Who maintains GNU Radio?
See [gnuradio organization](https://www.gnuradio.org/org/organization/). 
Retrieved from "[https://wiki.gnuradio.org/index.php?title=Development&oldid=13629](https://wiki.gnuradio.org/index.php?title=Development&oldid=13629)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=Development "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=Development "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:Development&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=Development)
  * [View source](https://wiki.gnuradio.org/index.php?title=Development&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=Development&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/Development "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/Development "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=Development&oldid=13629 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=Development&action=info "More information about this page")


  * This page was last edited on 25 November 2023, at 00:42.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


