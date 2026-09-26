# Installing Micro-Manager and SimulatedMicroscope for the workshop

## Windows

1. Download and install the official Micro-Manager nightly build: https://download.micro-manager.org/nightly/2.0/Windows/MMSetup_64bit_2.0.3_20260925.exe

   (Note that you can install multiple versions of Micro-Manager on the same computer as long as you put them in different locations and don't run them at the same time.)

2. Then, download the [SimulatedMicroscope device adapter](https://github.com/marktsuchida/mmdev-SimulatedMicroscope/releases/download/v20260926.75/mmdev-SimulatedMicroscope-windows-x86_64-v20260926.75.zip). Place the file `mmgr_dal_SimulatedMicroscope.dll` in the Micro-Manager install folder (under Program Files if you used the default).

## macOS

1. Micro-Manager will only work if the *newest* Java installed on the system is Java 11 for the correct architecture. All Java installations are in `/Library/Java/JavaVirtualMachines`. Check this folder and temporarily remove any conflicting or newer versions (you can back them up and put them back later).

   You can open `/Library/Java/JavaVirtualMachines` in Finder using `Go` > `Go
   to Folder...` (Shift-Cmd-G).

2. Download and install [Temurin JDK 11](https://adoptium.net/temurin/releases/?version=11&os=any&arch=any) for macOS. Make sure to get the aarch64 version for Apple Silicon or the x64 version for Intel.

3. Download and install our special build of Micro-Manager for the workshop:
    - [Apple Silicon arm64](https://github.com/uw-loci/mm-workshop-i2k-bina-2026/releases/download/macos-installers/Micro-Manager-20260925-arm64.dmg)
    - [Intel x86_64](https://github.com/uw-loci/mm-workshop-i2k-bina-2026/releases/download/macos-installers/Micro-Manager-20260925-x86_64.dmg)

The special Micro-Manager builds linked here differ from the official version in the following ways:
- They include the SimulatedMicroscope device adapter we'll use in the workshop
- An arm64 version is provided

(We're working to make the arm64 version available officially and to remove the need for a separate Java install!)

## Ubuntu 24.04

You'll need some familiarity with the command line here.

Build and install as follows:

```sh
sudo apt update
sudo apt install \
    curl git subversion build-essential autoconf automake libtool \
    autoconf-archive pkg-config swig3.0 openjdk-11-jdk ant libboost-all-dev

curl -LO https://wsr.imagej.net/distros/cross-platform/ij154.zip
unzip ij154.zip
sudo mkdir -p /opt/micro-manager
sudo mv ImageJ/* /opt/micro-manager

mkdir 3rdpartypublic
pushd 3rdpartypublic
svn checkout https://svn.micro-manager.org/3rdpartypublic/classext
popd

git clone https://github.com/micro-manager/micro-manager.git
pushd micro-manager
git submodule update --init --recursive
export SWIG=/usr/bin/swig3.0
./autogen.sh
./configure --enable-imagej-plugin=/opt/micro-manager
make fetchdeps
make -j
sudo make install
popd

curl -LO https://github.com/marktsuchida/mmdev-SimulatedMicroscope/releases/download/v20260926.75/mmdev-SimulatedMicroscope-linux-x86_64-v20260926.75.zip
unzip mmdev-SimulatedMicroscope-linux-x86_64-v20260926.75.zip
sudo mv libmmgr_dal_SimulatedMicroscope.so.0 /opt/micro-manager
```

Launch Micro-Manager with:

```sh
/opt/micro-manager/mmimagej &
```

More details are [here](https://github.com/micro-manager/micro-manager/blob/main/doc/how-to-build.md), including some troubleshoting tips at the end of the page.

## Ubuntu 26.04

You'll need some familiarity with the command line here.

First, download one source package using your web browser, because it is hard to get using command line tools (it uses HTML redirects): [pcre-8.45.tar.bz2](https://sourceforge.net/projects/pcre/files/pcre/8.45/pcre-8.45.tar.bz2/download) I'll assume below that `pcre-8.45.tar.bz2` is in `~/Downloads/`.

Build and install as follows:

```sh
sudo apt update
sudo apt install \
    curl git subversion build-essential autoconf automake libtool \
    autoconf-archive pkg-config openjdk-11-jdk ant libboost-all-dev

curl -LO https://prdownloads.sourceforge.net/swig/swig-3.0.12.tar.gz
tar xf swig-3.0.12.tar.gz
pushd swig-3.0.12
cp ~/Downloads/pcre-8.45.tar.bz2 ./    # See note above
./Tools/pcre-build.sh
./configure --program-suffix=3.0
make -j4
sudo make install
popd

curl -LO https://wsr.imagej.net/distros/cross-platform/ij154.zip
unzip ij154.zip
sudo mkdir -p /opt/micro-manager
sudo mv ImageJ/* /opt/micro-manager

mkdir 3rdpartypublic
pushd 3rdpartypublic
svn checkout https://svn.micro-manager.org/3rdpartypublic/classext
popd

git clone https://github.com/micro-manager/micro-manager.git
pushd micro-manager
git submodule update --init --recursive
export SWIG=/usr/local/bin/swig3.0
./autogen.sh
./configure --enable-imagej-plugin=/opt/micro-manager
make fetchdeps
make -j
sudo make install
popd

curl -LO https://github.com/marktsuchida/mmdev-SimulatedMicroscope/releases/download/v20260926.75/mmdev-SimulatedMicroscope-linux-x86_64-v20260926.75.zip
unzip mmdev-SimulatedMicroscope-linux-x86_64-v20260926.75.zip
sudo mv libmmgr_dal_SimulatedMicroscope.so.0 /opt/micro-manager
```

Launch Micro-Manager with:

```sh
/opt/micro-manager/mmimagej &
```
