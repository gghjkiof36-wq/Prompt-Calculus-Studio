Option Explicit
Dim shell, files, folder, python, launcher, command, argument
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
folder = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(folder, ".venv\Scripts\pythonw.exe")
launcher = files.BuildPath(folder, "launch_source.pyw")
If Not files.FileExists(python) Or Not files.FileExists(launcher) Then
    MsgBox "PCS needs its Python 3.12 environment. Follow docs\guide\getting-started.md to create .venv and install requirements.", 16, "Prompt Calculus Studio"
    WScript.Quit 1
End If
command = Quote(python) & " -B -X utf8 " & Quote(launcher)
For Each argument In WScript.Arguments
    command = command & " " & Quote(argument)
Next
shell.CurrentDirectory = folder
shell.Run command, 0, False

Function Quote(value)
    Dim result, index, character, slashes
    result = Chr(34)
    slashes = 0
    For index = 1 To Len(value)
        character = Mid(value, index, 1)
        If character = "\" Then
            slashes = slashes + 1
        ElseIf character = Chr(34) Then
            result = result & String(slashes * 2 + 1, "\") & Chr(34)
            slashes = 0
        Else
            result = result & String(slashes, "\") & character
            slashes = 0
        End If
    Next
    Quote = result & String(slashes * 2, "\") & Chr(34)
End Function
