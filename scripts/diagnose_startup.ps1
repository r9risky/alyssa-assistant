$ErrorActionPreference = "SilentlyContinue"
$startupEntries = @()
$shell = New-Object -ComObject WScript.Shell

foreach ($folder in @(
    [Environment]::GetFolderPath("Startup"),
    [Environment]::GetFolderPath("CommonStartup")
)) {
    foreach ($item in Get-ChildItem -LiteralPath $folder -Force) {
        $target = $item.FullName
        $arguments = ""
        $workingDirectory = ""
        $windowStyle = ""
        if ($item.Extension -eq ".lnk") {
            $shortcut = $shell.CreateShortcut($item.FullName)
            $target = $shortcut.TargetPath
            $arguments = $shortcut.Arguments
            $workingDirectory = $shortcut.WorkingDirectory
            $windowStyle = $shortcut.WindowStyle
        }
        if (($item.Name + $target + $arguments) -match "alyssa") {
            $startupEntries += [pscustomobject]@{
                Mechanism = "Startup folder"
                Name = $item.Name
                Command = "$target $arguments".Trim()
                WorkingDirectory = $workingDirectory
                Details = "WindowStyle=$windowStyle"
            }
        }
    }
}

foreach ($key in @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
)) {
    if (Test-Path $key) {
        foreach ($property in (Get-ItemProperty -LiteralPath $key).PSObject.Properties) {
            if (($property.Name + [string]$property.Value) -match "alyssa") {
                $startupEntries += [pscustomobject]@{
                    Mechanism = "Registry Run key"
                    Name = $property.Name
                    Command = [string]$property.Value
                    WorkingDirectory = ""
                    Details = $key
                }
            }
        }
    }
}

foreach ($task in Get-ScheduledTask) {
    if (($task.TaskName + $task.Actions.Execute + $task.Actions.Arguments) -match "alyssa") {
        $startupEntries += [pscustomobject]@{
            Mechanism = "Task Scheduler"
            Name = "$($task.TaskPath)$($task.TaskName)"
            Command = "$($task.Actions.Execute) $($task.Actions.Arguments)".Trim()
            WorkingDirectory = $task.Actions.WorkingDirectory
            Details = "State=$($task.State); Hidden=$($task.Settings.Hidden); Delay=$($task.Triggers.Delay)"
        }
    }
}

if ($startupEntries.Count -eq 0) {
    Write-Output "No Alyssa autostart registration was found."
    exit 1
}

$startupEntries | Format-List
