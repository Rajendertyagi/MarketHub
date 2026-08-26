# Vendor Web Awesome dist subset for self-hosted use.
#
# Source: @awesome.me/webawesome@3.12.0 (npm) — current stable release
# https://github.com/shoelace-style/webawesome/releases/tag/v3.12.0
#
# Usage:
#   .\vendor_webawesome.ps1              # download + extract + vendor
#
# The script downloads the npm tarball, extracts only runtime-needed
# directories (skips react/ssr/skills/types/translations/events), and
# copies them into web/ui/vendor/webawesome/dist/.
#
# No protoc / Node / Bun required — just PowerShell and network access
# to registry.npmjs.org.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$pkgName    = '@awesome.me/webawesome'
$pkgVersion = '3.12.0'
$tarballUrl = "https://registry.npmjs.org/$pkgName/-/webawesome-$pkgVersion.tgz"

$projectRoot = Split-Path $PSScriptRoot
$dstRoot     = Join-Path $projectRoot 'web\ui\vendor\webawesome\dist'
$tmpRoot     = Join-Path $env:TEMP "wa-vendor-$pkgVersion"

# ── Download ──────────────────────────────────────────────────────────────
Write-Host "Downloading ${pkgName}@${pkgVersion}..." -ForegroundColor Cyan
Remove-Item -Recurse -Force $tmpRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $tmpRoot -Force | Out-Null
$tgz = Join-Path $tmpRoot 'webawesome.tgz'
Invoke-WebRequest -Uri $tarballUrl -OutFile $tgz -TimeoutSec 60
Write-Host "  downloaded $((Get-Item $tgz).Length) bytes"

# ── Extract ───────────────────────────────────────────────────────────────
tar -xzf $tgz -C $tmpRoot
if ($LASTEXITCODE -ne 0) { throw "tar extraction failed" }

# ── Vendor ────────────────────────────────────────────────────────────────
$src = Join-Path $tmpRoot 'package\dist'
if (-not (Test-Path $src)) { throw "dist not found in npm package" }

Write-Host "Vendoring dist into $dstRoot..." -ForegroundColor Cyan
Remove-Item -Recurse -Force $dstRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $dstRoot -Force | Out-Null

# Copy only runtime-needed directories (skip react/ssr/skills/types/etc.)
foreach ($dir in @('chunks', 'components', 'internal', 'styles', 'utilities')) {
    Copy-Item -Recurse -Force (Join-Path $src $dir) (Join-Path $dstRoot $dir)
}

# Copy entry point
Copy-Item (Join-Path $src 'webawesome.loader.js') (Join-Path $dstRoot 'webawesome.loader.js')

# Remove TypeScript declarations (not needed at runtime)
Get-ChildItem -Recurse $dstRoot -Filter '*.d.ts' | Remove-Item -Force

# Clean up temp
Remove-Item -Recurse -Force $tmpRoot -ErrorAction SilentlyContinue

$fileCount = (Get-ChildItem -Recurse $dstRoot -File).Count
$totalSize = (Get-ChildItem -Recurse $dstRoot -File | Measure-Object -Property Length -Sum).Sum
Write-Host "Vendored $fileCount files ($([math]::Round($totalSize/1024)) KB) -> $dstRoot" -ForegroundColor Green
