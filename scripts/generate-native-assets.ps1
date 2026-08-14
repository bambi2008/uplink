[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root 'static\app-icon-512.png'
$icon = [Drawing.Image]::FromFile($source)

function Save-ResizedPng([string]$Path, [int]$Width, [int]$Height, [double]$Scale = 1.0, [switch]$DarkBackground) {
    $bitmap = New-Object Drawing.Bitmap($Width, $Height, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CompositingQuality = [Drawing.Drawing2D.CompositingQuality]::HighQuality
        $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::HighQuality
        if ($DarkBackground) { $graphics.Clear([Drawing.ColorTranslator]::FromHtml('#05070C')) } else { $graphics.Clear([Drawing.Color]::Transparent) }
        $size = [int]([Math]::Min($Width, $Height) * $Scale)
        $x = [int](($Width - $size) / 2)
        $y = [int](($Height - $size) / 2)
        $graphics.DrawImage($icon, $x, $y, $size, $size)
        $bitmap.Save($Path, [Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

try {
    $densities = @{
        'mdpi' = @(48, 108)
        'hdpi' = @(72, 162)
        'xhdpi' = @(96, 216)
        'xxhdpi' = @(144, 324)
        'xxxhdpi' = @(192, 432)
    }
    foreach ($density in $densities.Keys) {
        $legacy, $foreground = $densities[$density]
        $dir = Join-Path $root "android\app\src\main\res\mipmap-$density"
        Save-ResizedPng (Join-Path $dir 'ic_launcher.png') $legacy $legacy
        Save-ResizedPng (Join-Path $dir 'ic_launcher_round.png') $legacy $legacy
        Save-ResizedPng (Join-Path $dir 'ic_launcher_foreground.png') $foreground $foreground 0.72
    }

    Save-ResizedPng (Join-Path $root 'ios\App\App\Assets.xcassets\AppIcon.appiconset\AppIcon-512@2x.png') 1024 1024 1.0 -DarkBackground

    $splashPaths = @(
        (Join-Path $root 'android\app\src\main\res\drawable\splash.png')
        (Get-ChildItem (Join-Path $root 'android\app\src\main\res') -Directory -Filter 'drawable-*' | ForEach-Object { Join-Path $_.FullName 'splash.png' } | Where-Object { Test-Path $_ })
        (Get-ChildItem (Join-Path $root 'ios\App\App\Assets.xcassets\Splash.imageset') -File -Filter '*.png' | Select-Object -ExpandProperty FullName)
    )
    foreach ($path in $splashPaths) {
        $existing = [Drawing.Image]::FromFile($path)
        try { $width = $existing.Width; $height = $existing.Height } finally { $existing.Dispose() }
        Save-ResizedPng $path $width $height 0.28 -DarkBackground
    }
} finally {
    $icon.Dispose()
}

Write-Host 'Generated Uplink Android and iOS assets.'
