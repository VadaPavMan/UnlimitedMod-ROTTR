# ROTTR Mod Menu

A lightweight GUI-based mod menu for Rise of the Tomb Raider that allows you to enable Unlimited Resources, Unlimited Ammo, and Unlimited Survival Instinct while the game is running. Designed for easy use and quick toggling without restarting the game.

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

## How to Use

Please read [HOW_TO_USE.md](HOW_TO_USE.md) for full step-by-step instructions.

## Compile (For Devs)

To Build And Compile The Source:

- `pip install pymem`
- `pip install pyinstaller`
- `pyinstaller --onefile --noconsole --name "ROTTR-Mod_Menu" --icon=assets/mod.ico --add-data "assets/mod.ico;." --version-file version.txt --uac-admin --noupx --clean src/rottr_trainer_gui.py`

## Notes

- The mod can be enabled or disabled while the game is running.
- You can toggle each feature simply by checking or unchecking the corresponding box in the menu.
- Running the program as administrator is recommended for better compatibility.

## Credits

App icon credit: gravisio (https://www.flaticon.com/authors/gravisio)
