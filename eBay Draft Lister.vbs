Option Explicit
Dim files, shell, folder, python, launcher
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(folder, ".venv\Scripts\pythonw.exe")
launcher = files.BuildPath(folder, "launcher.py")
If Not files.FileExists(python) Then
    MsgBox "Run setup-windows.cmd first, then open eBay Draft Lister again.", vbExclamation, "eBay Draft Lister"
    WScript.Quit 1
End If
shell.CurrentDirectory = folder
shell.Run """" & python & """ """ & launcher & """", 0, False
