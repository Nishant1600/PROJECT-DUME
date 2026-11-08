' Double-click to launch JARVIS with no terminal window.
' Runs jarvis/main.py with pythonw (windowless) from the project's own venv.
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root
' 0 = hidden window; the HUD opens as its own app window
sh.Run """" & pyw & """ -m jarvis.main", 0, False
