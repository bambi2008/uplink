[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Package,
    [Parameter(Mandatory = $true)]
    [string]$ApiOrigin,
    [Parameter(Mandatory = $true)]
    [int]$VersionCode,
    [Parameter(Mandatory = $true)]
    [string]$VersionName
)

$ErrorActionPreference = 'Stop'
$packagePath = (Resolve-Path -LiteralPath $Package).Path
$sdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } elseif ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }
$aapt = Join-Path $sdk 'build-tools\36.0.0\aapt2.exe'

if (-not (Test-Path -LiteralPath $aapt)) { throw "aapt2 was not found at $aapt" }
if ((Get-Item -LiteralPath $packagePath).Length -lt 100000) { throw 'Android package is unexpectedly small' }

if ([IO.Path]::GetExtension($packagePath) -eq '.apk') {
    $badging = (& $aapt dump badging $packagePath) -join "`n"
    if ($badging -notmatch "package: name='com\.bambi2008\.uplink' versionCode='$VersionCode' versionName='$([regex]::Escape($VersionName))'") { throw 'APK identity or version is incorrect' }
    if ($badging -notmatch "uses-permission: name='android\.permission\.RECORD_AUDIO'") { throw 'APK microphone permission is missing' }
    $bridgeEntry = 'assets/public/native-bridge.js'
} else {
    $bridgeEntry = 'base/assets/public/native-bridge.js'
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($packagePath)
try {
    $entry = $archive.GetEntry($bridgeEntry)
    if (-not $entry) { throw "Native bridge is missing from $packagePath" }
    $reader = New-Object IO.StreamReader($entry.Open())
    try { $bridge = $reader.ReadToEnd() } finally { $reader.Dispose() }
} finally {
    $archive.Dispose()
}

$expected = 'const API_ORIGIN = "' + $ApiOrigin.TrimEnd('/') + '"'
if ($bridge -notmatch [regex]::Escape($expected)) { throw 'The packaged API origin does not match the requested origin' }
if ($bridge -match 'MINIMAX_API_KEY|XF_APIKEY|DOUBAO_ACCESS_TOKEN') { throw 'A provider credential identifier leaked into the native bridge' }
if ($bridge -notmatch 'api/account/ws-ticket') { throw 'The one-time WebSocket ticket flow is missing' }

Write-Host "Verified $packagePath"
