Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
desktop = root & "\desktop"
py = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\AI\software\anaconda\envs\agent\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw"
shell.CurrentDirectory = desktop
shell.Run """" & py & """ """ & desktop & "\launch.py""", 0, False
