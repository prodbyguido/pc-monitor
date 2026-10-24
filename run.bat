@echo off
rem Starts PC Monitor without a console window.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py"
