param(
    [string]$Destination,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$skillPackRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if ([string]::IsNullOrWhiteSpace($Destination)) {
    $codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
    $Destination = Join-Path $codexHome "skills"
}

$skillFolders = Get-ChildItem -LiteralPath $skillPackRoot -Directory | Where-Object {
    Test-Path -LiteralPath (Join-Path $_.FullName "SKILL.md")
}

if (-not $skillFolders) {
    throw "No skill folders containing SKILL.md were found."
}

New-Item -ItemType Directory -Path $Destination -Force | Out-Null

foreach ($skillFolder in $skillFolders) {
    $target = Join-Path $Destination $skillFolder.Name
    if ((Test-Path -LiteralPath $target) -and -not $Force) {
        Write-Host "Skipped: $($skillFolder.Name) (target exists; use -Force to replace it)"
        continue
    }
    Copy-Item -LiteralPath $skillFolder.FullName -Destination $target -Recurse -Force:$Force
    Write-Host "Installed: $($skillFolder.Name) -> $target"
}
