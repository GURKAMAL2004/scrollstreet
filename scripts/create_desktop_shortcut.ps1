# Creates a ScrollStreet shortcut on your desktop with the app icon.
# Run:  powershell -ExecutionPolicy Bypass -File scripts\create_desktop_shortcut.ps1

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Test-Path "$root\assets\scrollstreet.ico")) {
    & "$root\.venv\Scripts\python.exe" "$root\scripts\make_icon.py"
}

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\ScrollStreet.lnk")
$lnk.TargetPath = "wscript.exe"
$lnk.Arguments = "`"$root\ScrollStreet.vbs`""
$lnk.WorkingDirectory = $root
$lnk.IconLocation = "$root\assets\scrollstreet.ico,0"
$lnk.Description = "ScrollStreet - the financial universe as an infinite scroll"
$lnk.Save()
Write-Host "Desktop shortcut created -> ScrollStreet"
