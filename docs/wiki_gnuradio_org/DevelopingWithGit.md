# DevelopingWithGit
From GNU Radio
[Jump to navigation](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#mw-head) [Jump to search](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#searchInput)
## Contents
  * [1 Using Git for GNU Radio development](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Using_Git_for_GNU_Radio_development)
    * [1.1 Creating your local repository of GNU Radio](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Creating_your_local_repository_of_GNU_Radio)
      * [1.1.1 Clone the repository](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Clone_the_repository)
      * [1.1.2 Setting up git](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Setting_up_git)
      * [1.1.3 Adding remotes](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Adding_remotes)
      * [1.1.4 Using Someone Else's Remote Branch](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Using_Someone_Else's_Remote_Branch)
    * [1.2 Setting up tracking branches](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Setting_up_tracking_branches)
    * [1.3 Updates](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Updates)
    * [1.4 Branching and adding own stuff](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Branching_and_adding_own_stuff)
      * [1.4.1 Upload your changes](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Upload_your_changes)
      * [1.4.2 Deleting branches](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Deleting_branches)
    * [1.5 Git Graphical Browsing Tools](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Git_Graphical_Browsing_Tools)
    * [1.6 Publishing (upstreaming) your code](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Publishing_\(upstreaming\)_your_code)
      * [1.6.1 Pull requests](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Pull_requests)
      * [1.6.2 Tell us about your branch](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Tell_us_about_your_branch)
      * [1.6.3 Submitting and applying patches](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Submitting_and_applying_patches)
    * [1.7 Converting From Old Subversion Repository to the New Git Repository](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Converting_From_Old_Subversion_Repository_to_the_New_Git_Repository)
    * [1.8 Other Resources](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit#Other_Resources)


# Using Git for GNU Radio development
Git makes it easier for anyone to develop and contribute code to gnuradio. This article will describe how to use git with multiple repositiories so you can develop and publish your changes to gnuradio. The goal of this page is to help people manage git repositories so code may easily be shared among GNU Radio developers. 
The basic idea is you clone the gnuradio git repository and maintain a public repository with your work on github, or other public git server.  
In these examples, we will assume you are using github (with user name USER), as it is the most popular among GNU Radio developers. Also, we host a mirror of the main repository on github, so this is really, really simple. 
## Creating your local repository of GNU Radio
First, head over to the github repository of GNU Radio at <https://github.com/gnuradio/gnuradio>, and click the "fork" button at the top. This will automatically create a repository in your github account, called `gnuradio`. You basically have a copy of the official repository, but you can write to this one. 
### Clone the repository
Before you can do anything, you need the repository on your local hard drive ("cloning").  
github will tell you the correct URL, most likely, it's something like this: 

```
$ git clone --recursive https://github.com/USER/gnuradio.git
```

If you don't want to use github, you can directly clone the GNU Radio repository, but you won't be able to use some very useful features of github, such as pull requests: 

```
$ git clone --recursive https://github.com/gnuradio/gnuradio.git
```

### Setting up git
If you haven't done so yet, git needs some configuring.  
First, you need to set up an identity: 

```
$ git config user.name "Your Name"
$ git config user.email your@email.abc
```

On github, you also need to set up an SSH key; github has excellent documentation on how to do that. 
### Adding remotes
A remote is a location of a repository.  
You can get a list of remotes with the command 

```
$ git remote -v
origin  https://github.com/USER/gnuradio.git (fetch)
origin  https://github.com/USER/gnuradio.git (push)
```

You need at least one more remote, for tracking the changes to the main repository: 

```
$ git remote add upstream git://github.com/gnuradio/gnuradio.git
$ git remote -v
origin  https://github.com/USER/gnuradio.git (fetch)
origin  https://github.com/USER/gnuradio.git (push)
upstream        git://github.com/gnuradio/gnuradio.git (fetch)
upstream        git://github.com/gnuradio/gnuradio.git (push)
```

### Using Someone Else's Remote Branch
Just in case this wasn't clear from the previous section:  
As a collaboration tool, git can set up what's known as a remote to connect to other people's repositories. These repos, in the git distributed system, do not need to be on a single server, but can be anywhere. 
Now, someone might be doing something interesting you care about. Say this is Tom Rondeau and you want to track his work: 

```
$ git remote add trondeau git://github.com/trondeau/gnuradio.git
$ git fetch --all # This downloads all available content
$ git branch -r # Lists remote branches
```

## Setting up tracking branches
Say you want to work off the `next` branch. First, you need a copy of that in your local repository -- a tracking branch: 

```
$ git fetch --all # This downloads all available content
$ git branch -r # Lists remote branches
```

You can see that github already copied the next branch remotely. So, create a local tracking branch called next from the remote branch called origin/next: 

```
$ git checkout --track -b next origin/next
```

**Important: Never, ever commit (write) to a local tracking branch. Always use them as a base to branch off!**
## Updates
To make sure your tracking branches are up-to-date: 

```
$ git checkout master # Switch to branch you want to update
$ git pull upstream master # Download the newest code from our repository
```

Do this anytime you start working on something. 
## Branching and adding own stuff
Whenever you want to work on something, create a branch for it. Say you want to implement a Heisenberg compensator into the master branch: 

```
$ git checkout master # Go to our starting branch
$ git pull upstream master # Update
$ git checkout -b heisencomp # Create new branch from current branch (master)
```

Now, you can edit a new file with the fantastic code.  
A very useful command to figure out the state of your repository is 

```
$ git status
# On branch heisencomp
# Untracked files:
#   (use "git add ..." to include in what will be committed)
#
#       heisenberg.c
nothing added to commit but untracked files present (use "git add" to track)
```

So, there's a new file but git doesn't know what to do with it. So, you add it to your repository: 

```
$ git add heisenberg.c # Marks all files that go into the following commit
$ git commit -m 'core: Added a Heisenberg compensator' # Commits the changes
```

### Upload your changes
Your commit is only locally available now. You must manually copy this to your repository: 

```
$ git push origin heisencomp
```

Which will create a new remote branch with a copy of your local branch. 
### Deleting branches
When you are done with the branch, delete it locally, and on the remote repository (if needed): 
To delete your branch locally: 

```
$ git branch -d heisencomp
```

To delete your branch from the server: 

```
$ git push origin :heisencomp # If you think this is a weird syntax, you're not alone
```

## Git Graphical Browsing Tools
There are some of the git graphical browsing tools such as gitk and qgit. The **gitk** is the original TCL/TK GUI for browsing history of Git repositories. The **qgit** is a QT GUI for browsing history of Git repositories, similar to gitk but with more features. **tig** is a curses-based GUI. 
## Publishing (upstreaming) your code
The most important part of the code sharing process is contributing your changes back upstream. 
### Pull requests
If you're on github, this is the easiest way: 
  1. Fork your repo off of our gnuradio repo, then create a remote branch on github with your changes
  2. Go onto the github site, visit your repository
  3. There's a "Pull Request" button that will do all the work for you.


### Tell us about your branch
If you have significant changes, you can simply email us (best way is by mailing list) and tell us about your code. All we need is the link to your remote branch. 
### Submitting and applying patches
Small stuff, bug fixes (and attachments for the issue tracker!) comes in the form of patches.  
Going back to the previous example: 

```
$ git format-patch HEAD~
0001-core-Added-a-Heisenberg-compensator.patch
```

This makes a patch for the latest commit and displays the file name. You can send this patch via email, or upload it to the issue tracker. 
Someone else can apply this patch in his repository using 

```
git apply FILENAME.patch
```

## Converting From Old Subversion Repository to the New Git Repository
The subversion imported commits have the revision number at the end of the description in the "git-svn-id" line. For example, you can create and checkout a new branch with svn revision 10184 by doing the following. 
  1. Find the commit hash from the git-svn-id.


```
  $ git log --grep=git-svn-id.*@10184
```

  1. Create and checkout a new branch named r10184 from the commit hash.


```
  $ git checkout -b r10184 cd7b07f0140ddff6
```

## Other Resources
Some external resources that may be useful for learning/working with git: 
  * <http://www.gitready.com/>


  * <http://git.or.cz/course/svn.html>


  * <http://git-scm.com/>


  * <http://git.or.cz/gitwiki/GitCheatSheet>


Retrieved from "[https://wiki.gnuradio.org/index.php?title=DevelopingWithGit&oldid=715](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit&oldid=715)"
## Navigation menu
###  Personal tools
  * [Log in](https://wiki.gnuradio.org/index.php?title=Special:UserLogin&returnto=DevelopingWithGit "You are encouraged to log in; however, it is not mandatory \[alt-shift-o\]")


###  Namespaces
  * [Page](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit "View the content page \[alt-shift-c\]")
  * [Discussion](https://wiki.gnuradio.org/index.php?title=Talk:DevelopingWithGit&action=edit&redlink=1 "Discussion about the content page \(page does not exist\) \[alt-shift-t\]")


English
###  Views
  * [Read](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit)
  * [View source](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit&action=edit "This page is protected.
You can view its source \[alt-shift-e\]")
  * [View history](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit&action=history "Past revisions of this page \[alt-shift-h\]")


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
  * [What links here](https://wiki.gnuradio.org/index.php?title=Special:WhatLinksHere/DevelopingWithGit "A list of all wiki pages that link here \[alt-shift-j\]")
  * [Related changes](https://wiki.gnuradio.org/index.php?title=Special:RecentChangesLinked/DevelopingWithGit "Recent changes in pages linked from this page \[alt-shift-k\]")
  * [Special pages](https://wiki.gnuradio.org/index.php?title=Special:SpecialPages "A list of all special pages \[alt-shift-q\]")
  * [Printable version](javascript:print\(\); "Printable version of this page \[alt-shift-p\]")
  * [Permanent link](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit&oldid=715 "Permanent link to this revision of this page")
  * [Page information](https://wiki.gnuradio.org/index.php?title=DevelopingWithGit&action=info "More information about this page")


  * This page was last edited on 21 March 2017, at 01:07.
  * Content is available under [Creative Commons Attribution-ShareAlike](http://creativecommons.org/licenses/by-sa/3.0/) unless otherwise noted.


  * [Privacy policy](https://wiki.gnuradio.org/index.php?title=GNU_Radio:Privacy_policy)
  * [About GNU Radio](https://wiki.gnuradio.org/index.php?title=GNU_Radio:About)
  * [Disclaimers](https://wiki.gnuradio.org/index.php?title=GNU_Radio:General_disclaimer)


  * [![Creative Commons Attribution-ShareAlike](https://wiki.gnuradio.org/resources/assets/licenses/cc-by-sa.png)](http://creativecommons.org/licenses/by-sa/3.0/)
  * [![Powered by MediaWiki](https://wiki.gnuradio.org/resources/assets/poweredby_mediawiki.svg)](https://www.mediawiki.org/)


