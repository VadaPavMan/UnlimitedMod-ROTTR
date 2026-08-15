**NOTE: This mod may be detected as a threat by Windows Defender, as it scans Game Process Memory for read & write operations to apply modifications. This mod does not contain any sort of malware, and to keep everything transparent, I have also shared the source code along with the mod in the src folder.**

# ROTTR Mod Menu

A lightweight GUI-based mod menu for Rise of the Tomb Raider that allows you to enable Unlimited Resources, Unlimited Ammo, and Unlimited Survival Instinct while the game is running. Designed for easy use and quick toggling without restarting the game.

# Preview:
<img width="956" height="768" alt="275-1786526183-1144037140" src="https://github.com/user-attachments/assets/155b2f81-bcfb-4b9f-91df-713eaf58fcbf" />

# Note:

This mod trainer is currently in beta phase. The goal is to test it with more versions of ROTTR.exe so it can work properly for as many versions as possible.

## Current Features

The following cheats are currently included:

- Unlimited Ammo (with and without reload)
- Unlimited Resources
- Unlimited Survival Instinct

More features may be added in the future as new values are found and tested using Cheat Engine.

## Project Structure

- src\ - contains the main GUI script file, rottr_trainer_gui.py
- assets\ - contains icon and related asset files
- ROTTR-Mod_Menu.exe - the main executable program

## How to Use [You need to compile the source for exe]

Please read [HOW_TO_USE.md](HOW_TO_USE.md) for full step-by-step instructions.

## How to Compile

To Build And Compile The Source, Make Sure To `Install Python First` Then Follow Along:

- `pip install pymem`
- `pip install pyinstaller`
- `pyinstaller --onefile --noconsole --name "ROTTR-Mod_Menu" --icon=assets/mod.ico --add-data "assets/mod.ico;." --version-file version.txt --uac-admin --noupx --clean src/rottr_trainer_gui.py`

## Notes

- The mod can be enabled or disabled while the game is running.
- You can toggle each feature simply by checking or unchecking the corresponding box in the menu.
- Running the program as administrator is recommended for better compatibility.

## Credits

App icon credit: gravisio (https://www.flaticon.com/authors/gravisio)
