Option Explicit

Dim shell, fso, root, scriptPath, powershellPath, psCommand, command, exitCode, processEnvironment
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set processEnvironment = shell.Environment("Process")
root = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fso.BuildPath(fso.BuildPath(root, "portable"), "stockwatcher_portable.py")
powershellPath = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
processEnvironment("STOCKWATCHER_PORTABLE_SCRIPT") = scriptPath

psCommand = "$script=$env:STOCKWATCHER_PORTABLE_SCRIPT;" & _
    "$direct=@(" & _
    "[Environment]::ExpandEnvironmentVariables('%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe')," & _
    "[Environment]::ExpandEnvironmentVariables('%ProgramFiles%\Python312\pythonw.exe')" & _
    ");" & _
    "foreach($p in $direct){" & _
    "if(Test-Path -LiteralPath $p){" & _
    "$s=Get-AuthenticodeSignature -LiteralPath $p;" & _
    "if($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'Python Software Foundation'){" & _
    "Start-Process -FilePath $p -ArgumentList @(([char]34+$script+[char]34));exit 0" & _
    "}}};" & _
    "$launcher=(Get-Command pyw.exe -ErrorAction SilentlyContinue).Source;" & _
    "if($launcher){" & _
    "$s=Get-AuthenticodeSignature -LiteralPath $launcher;" & _
    "if($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'Python Software Foundation'){" & _
    "Start-Process -FilePath $launcher -ArgumentList @('-3.12',([char]34+$script+[char]34));exit 0" & _
    "}};" & _
    "exit 2"

command = """" & powershellPath & """ -NoLogo -NoProfile -NonInteractive " & _
    "-WindowStyle Hidden -Command " & """" & psCommand & """"
exitCode = shell.Run(command, 0, True)
processEnvironment.Remove("STOCKWATCHER_PORTABLE_SCRIPT")

If exitCode <> 0 Then
    MsgBox "未找到数字签名有效、发布者匹配 Python Software Foundation 的 Python 3.12 Pythonw。StockWatcher 未启动。", 16, "StockWatcher"
End If
