# SBTT - Stellar Blade Text Tools

This repository contains two tools necessary to extract and organize text from the Stellar Blade demo to edit them easily. This has NOT been tested outside of the demo.

The two tools were written by Claude. The guide was created by hand by me.

## Guide

### 1. Verify Prerequisites
```bash
python3 --version
repak --version
```

Python is a simple Google search to install.

Repak install guide is here: https://asdfkb.com/Knowledgebase/CLI/Repak

### 2. Download and Organize Tools
1. Create a new folder (I called it **SBDT**) which will be your ROOT folder.
2. Create a folder INSIDE of that **SBDT** (or whatever you called it) called **tools**.
3. Inside of tools (which is in SBDT >> tools), put the two Python files located in this repository: `locres.py` and `sbtext.py`.

### 3. Install Stellar Blade Demo
If you haven't already...
Keep in mind that this mod WAS NOT tested on the full game of Stellar Blade.

### 4. Set Stellar Blade Directory
In Steam, right-click the game then select Properties.
Select **Installed Files** then **Browse**.
Copy that entire path and use it below.

```bash
set -Ux SB_GAME_DIR <path_to_stellar_blade_dir>
```

The `-Ux` flag is permanent and will set the variable `SB_GAME_DIR` to always be that location. If you want it to be temporary and only exist in that current terminal session, remove the `U` part from the `-Ux` flag and just use: `-x`.

### 5. Build and Extract
First, enter your **tools** folder, I entered mine as shown below.
```bash
cd ~/SBDT/tools
```

```bash
./sbtext.py extract
./sbtext.py doctor
./sbtextpy export    # Only run if you get [OK] across the board
```

### 6. Making Changes
1. Create an **edits** folder in your root directory.
2. Copy any of the CSV files from *./work/export/en* into that **edits** folder.
3. To make a change, locate (with Ctrl+F) the text you want to replace and add text **AFTER THE COMMA** to replace the text.
For instance, `UI_Lobby_New_Game,8,New Game,My New Text Here!!!`.

### 7. Push Changes
```bash
./sbtext.py build --install
```

Now open the game to verify.

### 8. Sharing Online
Go to: `StellarBladeDemo/SB/Content/Paks/~mods/` folder.
Find the `.pak` file and ONLY change the `text` part, leaving the `zzz` and `_P` part alone.

## Important
Remember that this was only tested on the DEMO of Stellar Blade and NOT on the full game itself.
