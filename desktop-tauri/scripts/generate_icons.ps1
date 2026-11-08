param(
    [string]$Source = (Join-Path $PSScriptRoot '../../docs/assets/lycosa-logo-source.png'),
    [string]$Output = (Join-Path $PSScriptRoot '../src-tauri/icons')
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

# Keep the supplied artwork unchanged. Normalize only its transparent canvas
# before Tauri makes the native icon sizes; source-image padding otherwise
# makes the taskbar and Task Manager icons much smaller than other apps.
$drawingReferences = @('System.Drawing.Common', 'System.Drawing.Primitives', 'System.Runtime')
# Newer PowerShell/.NET builds split the Windows drawing interop into these
# assemblies; older builds do not reference them.
$drawingReferences += [System.Drawing.Bitmap].Assembly.GetReferencedAssemblies() |
    Where-Object { $_.Name -like 'System.Private.Windows.*' } |
    ForEach-Object { $_.Name }
Add-Type -ReferencedAssemblies $drawingReferences -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

public static class LycosaIconCanvas {
    public static void Write(string sourcePath, string outputPath) {
        using (var source = new Bitmap(sourcePath)) {
            int left = source.Width, top = source.Height, right = -1, bottom = -1;
            for (int y = 0; y < source.Height; y++) {
                for (int x = 0; x < source.Width; x++) {
                    if (source.GetPixel(x, y).A <= 8) continue;
                    left = Math.Min(left, x); top = Math.Min(top, y);
                    right = Math.Max(right, x); bottom = Math.Max(bottom, y);
                }
            }
            if (right < left) throw new InvalidOperationException("The artwork is empty.");
            // Include faint antialiased edge pixels, then leave 3% per side.
            left = Math.Max(0, left - 2); top = Math.Max(0, top - 2);
            right = Math.Min(source.Width - 1, right + 2);
            bottom = Math.Min(source.Height - 1, bottom + 2);
            int width = right - left + 1, height = bottom - top + 1;
            const int size = 1024;
            float scale = size * 0.94f / Math.Max(width, height);
            float w = width * scale, h = height * scale;
            using (var canvas = new Bitmap(size, size, PixelFormat.Format32bppArgb)) {
                using (var graphics = Graphics.FromImage(canvas)) {
                    graphics.Clear(Color.Transparent);
                    graphics.CompositingMode = CompositingMode.SourceCopy;
                    graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                    graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
                    graphics.DrawImage(source,
                        new RectangleF((size - w) / 2, (size - h) / 2, w, h),
                        new RectangleF(left, top, width, height), GraphicsUnit.Pixel);
                }
                canvas.Save(outputPath, ImageFormat.Png);
            }
        }
    }
}
'@

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$staging = Join-Path $repoRoot 'reports/branding/app-icons'
New-Item -ItemType Directory -Force -Path $staging | Out-Null
$canvas = Join-Path $staging 'icon-canvas.png'
[LycosaIconCanvas]::Write([IO.Path]::GetFullPath($Source), $canvas)

Push-Location (Join-Path $PSScriptRoot '..')
try {
    & npm.cmd run tauri -- icon $canvas --output $staging
    if ($LASTEXITCODE -ne 0) { throw 'Tauri icon generation failed.' }
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $Output | Out-Null
foreach ($name in @('32x32.png', '64x64.png', '128x128.png', '128x128@2x.png', 'icon.png', 'icon.ico', 'icon.icns')) {
    Copy-Item -LiteralPath (Join-Path $staging $name) -Destination (Join-Path $Output $name) -Force
}
Write-Host 'Native icons regenerated with 94% artwork coverage.'
