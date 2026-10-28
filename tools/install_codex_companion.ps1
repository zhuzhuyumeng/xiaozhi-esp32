[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)]
    [string]$DeviceHost,

    [string]$BridgeScript = (Join-Path $PSScriptRoot "codex_status_bridge.py"),
    [string]$HooksPath = (Join-Path $env:USERPROFILE ".codex\hooks.json")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Show-Usage {
    Write-Output "Usage: powershell -NoProfile -File install_codex_companion.ps1 -DeviceHost <IPv4>"
    Write-Output "Optional: -BridgeScript <path> -HooksPath <path>"
}

function Find-PythonCommand {
    $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        return @($pyLauncher.Source, "-3")
    }

    $python = Get-Command "python" -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        return @($python.Source)
    }

    throw "Python 3 was not found. Install Python 3 and run this installer again."
}

if ($DeviceHost -notmatch '^(?:\d{1,3}\.){3}\d{1,3}$') {
    throw "DeviceHost must be an IPv4 address: $DeviceHost"
}

$octets = $DeviceHost.Split('.') | ForEach-Object { [int]$_ }
if ($octets | Where-Object { $_ -lt 0 -or $_ -gt 255 }) {
    throw "DeviceHost is not a valid IPv4 address: $DeviceHost"
}

$resolvedBridge = (Resolve-Path -LiteralPath $BridgeScript -ErrorAction Stop).Path
$pythonCommand = Find-PythonCommand
$pythonExecutable = $pythonCommand[0]
$pythonArguments = @($pythonCommand | Select-Object -Skip 1)
$hooksDirectory = Split-Path -Parent $hooksPath

if (-not (Test-Path -LiteralPath $hooksDirectory)) {
    if ($PSCmdlet.ShouldProcess($hooksDirectory, "Create Codex configuration directory")) {
        New-Item -ItemType Directory -Path $hooksDirectory -Force | Out-Null
    }
}

if (Test-Path -LiteralPath $hooksPath) {
    $rawHooks = Get-Content -LiteralPath $hooksPath -Raw -Encoding UTF8
    try {
        $config = $rawHooks | ConvertFrom-Json -AsHashtable
    } catch {
        throw "Cannot parse $hooksPath as JSON: $($_.Exception.Message)"
    }
} else {
    $config = @{}
}

if (-not ($config -is [hashtable])) {
    throw "$hooksPath must contain a JSON object"
}
if (-not $config.ContainsKey("hooks")) {
    $config["hooks"] = @{}
}
if (-not ($config["hooks"] -is [hashtable])) {
    throw "$hooksPath has an invalid hooks object"
}

$pythonPrefix = @($pythonExecutable) + $pythonArguments
$quotedPrefix = ($pythonPrefix | ForEach-Object {
    if ($_ -match '\s') { '"' + $_.Replace('"', '\"') + '"' } else { $_ }
}) -join ' '
$commandWindows = "$quotedPrefix `"$resolvedBridge`" --host $DeviceHost hook"
$events = @("SessionStart", "UserPromptSubmit", "Stop", "Interrupt", "SessionEnd")
$registered = 0

foreach ($event in $events) {
    if (-not $config["hooks"].ContainsKey($event)) {
        $config["hooks"][$event] = @()
    }
    if (-not ($config["hooks"][$event] -is [array])) {
        $config["hooks"][$event] = @($config["hooks"][$event])
    }

    $config["hooks"][$event] = @($config["hooks"][$event] | Where-Object {
        $commands = @($_.hooks | ForEach-Object { $_.commandWindows })
        -not ($commands | Where-Object {
            $_ -match 'codex_status_bridge\.py.*--host' -and $_ -match [regex]::Escape($DeviceHost)
        })
    })

    $alreadyRegistered = $false
    foreach ($entry in $config["hooks"][$event]) {
        foreach ($hook in @($entry.hooks)) {
            if ($hook.commandWindows -eq $commandWindows) {
                $alreadyRegistered = $true
            }
        }
    }

    if (-not $alreadyRegistered) {
        $entry = @{
            hooks = @(@{
                type = "command"
                command = $commandWindows
                commandWindows = $commandWindows
                timeout = 3
            })
        }
        if ($PSCmdlet.ShouldProcess("${hooksPath}:$event", "Register Codex status hook")) {
            $config["hooks"][$event] += $entry
            $registered++
        }
    }
}

if ($PSCmdlet.ShouldProcess($hooksPath, "Write merged Codex hooks configuration")) {
    $json = $config | ConvertTo-Json -Depth 20
    $tempPath = "$hooksPath.$PID.tmp"
    Set-Content -LiteralPath $tempPath -Value $json -Encoding UTF8
    Move-Item -LiteralPath $tempPath -Destination $hooksPath -Force
}

if ($PSCmdlet.ShouldProcess($DeviceHost, "Send initial IDLE state")) {
    & $pythonExecutable @pythonArguments $resolvedBridge --host $DeviceHost send idle
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to send initial IDLE state (exit code $LASTEXITCODE)"
    }
}

Write-Output "Codex Companion installed. Registered $registered new hook(s)."
Write-Output "Device: $DeviceHost"
Write-Output "Hooks: $hooksPath"
Write-Output "Bridge: $resolvedBridge"
