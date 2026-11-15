$ErrorActionPreference = 'Stop'
$projectFolder = $PSScriptRoot
$desktopFolder = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopFolder 'eBay Draft Lister.lnk'
$launcherPath = Join-Path $projectFolder 'eBay Draft Lister.vbs'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.Arguments -ne ('"' + $launcherPath + '"')) {
    throw 'An unrelated eBay Draft Lister desktop shortcut already exists. Rename it before creating this shortcut.'
}
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$shortcut.Arguments = '"' + $launcherPath + '"'
$shortcut.WorkingDirectory = $projectFolder
$shortcut.IconLocation = (Join-Path $projectFolder 'assets\app.ico') + ',0'
$shortcut.Description = 'eBay Draft Lister — local card inventory and draft listings'
$shortcut.Save()
Write-Output "Created desktop shortcut: $shortcutPath"
