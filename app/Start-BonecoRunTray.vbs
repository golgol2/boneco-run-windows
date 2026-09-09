Option Explicit

Dim shell, fso, root, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " _
  & Chr(34) & root & "\tray\boneco_tray.ps1" & Chr(34)

shell.Run command, 0, False
