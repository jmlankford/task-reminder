@echo off
:: Task Reminder — silent tray launcher
:: Uses pythonw.exe so no console window appears.
::
:: TO AUTO-START AT LOGIN — pick one method:
::
::   Method A (Startup folder):
::     1. Press Win+R and run:  shell:startup
::     2. Copy a shortcut to THIS .bat file into that folder.
::
::   Method B (Registry):
::     1. Press Win+R and run:  regedit
::     2. Navigate to:
::          HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
::     3. Right-click → New → String Value
::        Name:   TaskReminder
::        Value:  "C:\full\path\to\start_tray.bat"

cd /d "%~dp0"
start "" /b pythonw "%~dp0taskreminder_tray.py"
