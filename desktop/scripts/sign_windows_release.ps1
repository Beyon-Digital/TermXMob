<#
.SYNOPSIS
Signs Termx Windows artifacts with signtool. No arguments required.

.DESCRIPTION
Run it from the repository root:

    .\desktop\scripts\sign_windows_release.ps1

It discovers the newest .msi and NSIS setup .exe under the build output, .\dist,
the current directory, and %USERPROFILE%\Downloads. Use -All to sign every match.

Configuration is read from the environment and from desktop\signing.env
(copy desktop\signing.env.example and edit):

    TERMX_WINDOWS_PFX=C:\certs\termx.pfx
    TERMX_WINDOWS_PFX_PASSWORD=secret

Or use the same variables CI uses:

    WINDOWS_CERTIFICATE=<base64 .pfx>
    WINDOWS_CERTIFICATE_PASSWORD=secret

.EXAMPLE
.\desktop\scripts\sign_windows_release.ps1
.EXAMPLE
.\desktop\scripts\sign_windows_release.ps1 -All -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Files,

    [string[]]$InputPaths,
    [switch]$All,
    [switch]$DryRun,
    [string]$CertificatePath,
    [string]$CertificatePassword,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Import-SigningEnv {
    $path = Join-Path $RepoRoot "desktop\signing.env"
    if (-not (Test-Path $path)) { return }
    Get-Content $path | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
        $parts = $_ -split '=', 2
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name))) {
            Set-Item -Path "env:$name" -Value $value
        }
    }
}
Import-SigningEnv

if (-not $CertificatePath) { $CertificatePath = $env:TERMX_WINDOWS_PFX }
if (-not $CertificatePassword) { $CertificatePassword = $env:TERMX_WINDOWS_PFX_PASSWORD }
if (-not $CertificatePassword) { $CertificatePassword = $env:WINDOWS_CERTIFICATE_PASSWORD }

function Find-SignTool {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    if ($env:TERMX_SIGNTOOL -and (Test-Path $env:TERMX_SIGNTOOL)) {
        return $env:TERMX_SIGNTOOL
    }
    $candidates = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" `
        -ErrorAction SilentlyContinue | Sort-Object FullName -Descending
    if ($candidates.Count -gt 0) { return $candidates[0].FullName }
    throw "signtool.exe not found. Install the Windows SDK or set TERMX_SIGNTOOL."
}

function Get-CandidateArtifacts {
    $roots = @()
    if ($InputPaths) { $roots = $InputPaths }
    elseif ($Files) { $roots = $Files }
    elseif ($env:TERMX_SIGN_INPUT) { $roots = @($env:TERMX_SIGN_INPUT) }
    else {
        $roots = @(
            (Join-Path $RepoRoot "desktop\src-tauri\target\release\bundle\msi"),
            (Join-Path $RepoRoot "desktop\src-tauri\target\release\bundle\nsis"),
            (Join-Path $RepoRoot "dist"),
            (Get-Location).Path,
            (Join-Path $env:USERPROFILE "Downloads")
        )
    }
    $found = New-Object System.Collections.Generic.List[System.IO.FileInfo]
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $item = Get-Item -LiteralPath $root
        if ($item.PSIsContainer) {
            Get-ChildItem -LiteralPath $root -Recurse -Depth 1 -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -in ".msi", ".exe" } |
                ForEach-Object { $found.Add($_) }
        }
        elseif ($item.Extension -in ".msi", ".exe") {
            $found.Add($item)
        }
    }
    $found | Sort-Object FullName -Unique
}

$candidates = @(Get-CandidateArtifacts)
if ($All) {
    $selected = $candidates
}
else {
    $selected = @()
    $msi = $candidates | Where-Object { $_.Extension -eq ".msi" } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $exe = $candidates | Where-Object { $_.Extension -eq ".exe" -and $_.Name -notmatch "backend" } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($msi) { $selected += $msi }
    if ($exe) { $selected += $exe }
}

if ($selected.Count -eq 0) {
    throw @"
No .msi/.exe found. Pass paths, set TERMX_SIGN_INPUT=<path>, or build first:
  cargo tauri build --bundles msi,nsis   (in desktop\src-tauri)
"@
}

Write-Host "==> artifacts to sign:"
$selected | ForEach-Object { Write-Host "  $($_.FullName)" }

if ($DryRun) {
    Write-Host "==> dry run; no certificate required"
    exit 0
}

$tempCertificate = $null
if (-not $CertificatePath -and $env:WINDOWS_CERTIFICATE) {
    $tempCertificate = Join-Path $env:TEMP "termx-signing-$([guid]::NewGuid().ToString('N')).p12"
    $base64 = ($env:WINDOWS_CERTIFICATE -replace '\s', '')
    [IO.File]::WriteAllBytes($tempCertificate, [Convert]::FromBase64String($base64))
    $CertificatePath = $tempCertificate
}

if (-not $CertificatePath) {
    throw @"
No certificate configured, so nothing can be signed yet.

Set one of these in desktop\signing.env (gitignored) or the environment:
  TERMX_WINDOWS_PFX=C:\certs\termx.pfx
  TERMX_WINDOWS_PFX_PASSWORD=secret
or
  WINDOWS_CERTIFICATE=<base64 .pfx>
  WINDOWS_CERTIFICATE_PASSWORD=secret

Until then, users can install the unsigned builds: SmartScreen -> More info -> Run anyway.
"@
}
if (-not (Test-Path $CertificatePath)) {
    throw "Certificate not found: $CertificatePath"
}
if (-not $CertificatePassword) {
    Write-Warning "No certificate password supplied; signtool may prompt (or fail in CI)."
}

$signtool = Find-SignTool
Write-Host "signtool: $signtool"
Write-Host "certificate: $CertificatePath"

try {
    foreach ($file in $selected) {
        Write-Host "==> signing $($file.FullName)"
        & $signtool sign /fd sha256 /tr $TimestampUrl /td sha256 `
            /f $CertificatePath /p $CertificatePassword $file.FullName
        if ($LASTEXITCODE -ne 0) { throw "signtool sign failed for $($file.FullName)" }
        if (-not $SkipVerify) {
            & $signtool verify /pa /all $file.FullName
            if ($LASTEXITCODE -ne 0) { throw "signtool verify failed for $($file.FullName)" }
        }
    }
}
finally {
    if ($tempCertificate -and (Test-Path $tempCertificate)) {
        Remove-Item $tempCertificate -Force
    }
}

Write-Host "done. signed $($selected.Count) file(s)."
