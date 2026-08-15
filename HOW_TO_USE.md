# How to Use ROTTR Mod Menu

## Step 1: Download and Extract

- Download the ZIP file containing the mod.
- Extract the ZIP file to a folder on your computer.

## Step 2: Install Python
- Download and Install Python To your System also configure it properly.
- Open the extracted folder in VS Code or Cmd.
- Type this in terminal of this folder directory:
- - `pip install pymem`
  - `pip install pyinstaller`
  - `pyinstaller --onefile --noconsole --name "ROTTR-Mod_Menu" --icon=assets/mod.ico --add-data "assets/mod.ico;." --version-file version.txt --uac-admin --noupx --clean src/rottr_trainer_gui.py`
- After the above process you will get the fully compiled .exe -> `ROTTR-Mod_Menu.exe`. 

## Step 3: Run as Administrator

- Right-click on ROTTR-Mod_Menu.exe.
- Select Properties.
- Open the Compatibility tab.
- Check the option that says Run this program as an administrator.
- Click Apply, then OK.

## Step 4: Launch the Game and Mod

- Start the game.
- Run ROTTR-Mod_Menu.exe alongside the game.
- Use the checkboxes in the menu to enable or disable the mods you want.

## Step 5: Toggle Mods During Gameplay

- You can enable or disable mods at any time while the game is running.
- Simply click the checkbox next to each feature to turn it on or off.

## Notes

- Game require administrator access for locating the game process and memory editing features to work correctly.
- If the mod does not work on your version of the game, please test with a different version or report the issue for further updates.
