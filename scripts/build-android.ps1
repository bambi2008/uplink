[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https://')]
    [string]$ApiOrigin,

    [ValidateSet('Debug', 'Release')]
    [string]$Configuration = 'Debug',

    [ValidateRange(1, 2100000000)]
    [int]$VersionCode = 1,

    [ValidatePattern('^[0-9]+(\.[0-9]+){1,3}([-.][0-9A-Za-z]+)?$')]
    [string]$VersionName = '1.0.0',

    [switch]$RequireSigning
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$sdk = if ($env:ANDROID_HOME) { $env:ANDROID_HOME } elseif ($env:ANDROID_SDK_ROOT) { $env:ANDROID_SDK_ROOT } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }
$api = $ApiOrigin.TrimEnd('/')

$uri = [Uri]$api
$placeholderHost = $uri.Host -eq 'localhost' -or $uri.Host -match '\.(localhost|invalid|test)$' -or $uri.Host -match '(^|\.)example\.(com|org|net)$'
if ($uri.AbsoluteUri.TrimEnd('/') -ne $api -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) {
    throw 'ApiOrigin must be an exact HTTPS origin without a path, credentials, query, or fragment.'
}
if ($Configuration -eq 'Release' -and $placeholderHost) {
    throw 'Release builds require the real production API origin; local and placeholder domains are not allowed.'
}
if (-not (Test-Path (Join-Path $sdk 'platforms\android-36'))) {
    throw "Android API 36 is not installed under $sdk"
}

$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
$env:UPLINK_API_ORIGIN = $api
$env:UPLINK_ANDROID_VERSION_CODE = [string]$VersionCode
$env:UPLINK_ANDROID_VERSION_NAME = $VersionName
$env:UPLINK_RELEASE_BUILD = if ($Configuration -eq 'Release') { '1' } else { '0' }
$env:UPLINK_REQUIRE_ANDROID_SIGNING = if ($RequireSigning) { '1' } else { '0' }

Push-Location $root
try {
    & npm.cmd run mobile:sync
    if ($LASTEXITCODE -ne 0) { throw "Capacitor sync failed with exit code $LASTEXITCODE" }

    Push-Location (Join-Path $root 'android')
    try {
        $task = if ($Configuration -eq 'Release') { 'bundleRelease' } else { 'assembleDebug' }
        & .\gradlew.bat --no-daemon $task
        if ($LASTEXITCODE -ne 0) { throw "Gradle $task failed with exit code $LASTEXITCODE" }
    } finally {
        Pop-Location
    }

    $source = if ($Configuration -eq 'Release') {
        Join-Path $root 'android\app\build\outputs\bundle\release\app-release.aab'
    } else {
        Join-Path $root 'android\app\build\outputs\apk\debug\app-debug.apk'
    }
    $extension = [IO.Path]::GetExtension($source)
    $destinationDir = Join-Path $root 'dist\android'
    New-Item -ItemType Directory -Force -Path $destinationDir | Out-Null
    $destination = Join-Path $destinationDir "uplink-$VersionName-$VersionCode$extension"
    Copy-Item -LiteralPath $source -Destination $destination -Force

    & (Join-Path $PSScriptRoot 'verify-android-package.ps1') -Package $destination -ApiOrigin $api -VersionCode $VersionCode -VersionName $VersionName
    if ($LASTEXITCODE -ne 0) { throw 'Android package verification failed' }
    Write-Host "Android package ready: $destination"
} finally {
    Pop-Location
}
