param(
    [string[]]$Target = @("Codex"),

    [ValidateSet("Project", "User")]
    [string]$Scope = "Project",

    [string]$ProjectRoot = (Get-Location).Path,
    [string]$Destination,
    [string[]]$Collection,
    [string[]]$Skill,
    [switch]$IncludeCandidates,

    [ValidateSet("InternalEnterprise")]
    [string]$SecurityProfile = "InternalEnterprise",

    [switch]$Force
)

$ErrorActionPreference = "Stop"
$hubRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$catalogPath = Join-Path $hubRoot "catalog.json"
$catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding utf8 | ConvertFrom-Json
$securityProfilePath = Join-Path $hubRoot "security-profile.json"
$securityPolicyPath = Join-Path $hubRoot "SECURITY_PROFILE.md"
$securityProfileConfig = Get-Content -LiteralPath $securityProfilePath -Raw -Encoding utf8 | ConvertFrom-Json
if ($securityProfileConfig.id -ne "internal-enterprise") {
    throw "Unsupported Hub security profile '$($securityProfileConfig.id)'."
}
if (-not (Test-Path -LiteralPath $securityPolicyPath -PathType Leaf)) {
    throw "Missing mandatory security policy: $securityPolicyPath"
}
$catalogSkills = @()
$skillMetadata = @{}

foreach ($entry in $catalog.skills) {
    $metadata = [pscustomobject]@{
        id = $entry.id
        status = $entry.status
        collectionId = $null
        sharedReferences = @()
    }
    $catalogSkills += $metadata
    $skillMetadata[$entry.id] = $metadata
}

$collectionsRoot = Join-Path $hubRoot "collections"
if (Test-Path -LiteralPath $collectionsRoot) {
    Get-ChildItem -LiteralPath $collectionsRoot -Directory | Sort-Object Name | ForEach-Object {
        $collectionCatalogPath = Join-Path $_.FullName "catalog.json"
        if (-not (Test-Path -LiteralPath $collectionCatalogPath)) {
            return
        }
        $collectionCatalog = Get-Content -LiteralPath $collectionCatalogPath -Raw -Encoding utf8 | ConvertFrom-Json
        $defaultStatus = $collectionCatalog.skill_defaults.status
        foreach ($entry in $collectionCatalog.skills) {
            $entryStatus = if ($entry.PSObject.Properties.Name -contains "status") { $entry.status } else { $defaultStatus }
            $references = @($entry.shared_references | Where-Object { $_ })
            $metadata = [pscustomobject]@{
                id = $entry.id
                status = $entryStatus
                collectionId = $collectionCatalog.id
                sharedReferences = $references
            }
            $catalogSkills += $metadata
            $skillMetadata[$entry.id] = $metadata
        }
    }
}

$knownSkills = @($catalogSkills | ForEach-Object { $_.id })
$knownCollections = @($catalogSkills | Where-Object { $_.collectionId } | ForEach-Object { $_.collectionId } | Sort-Object -Unique)
$excludedSkills = @($securityProfileConfig.excluded_skill_ids)
$excludedStillCataloged = @($knownSkills | Where-Object { $_ -in $excludedSkills })
if ($excludedStillCataloged.Count -gt 0) {
    throw "Security profile violation: excluded skills remain cataloged: $($excludedStillCataloged -join ', ')"
}
$Collection = @($Collection | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$Skill = @($Skill | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$installableStatuses = if ($IncludeCandidates) { @("active", "candidate") } else { @("active") }
$selectedSkills = if ($Skill -or $Collection) {
    @(
        $Skill
        $catalogSkills |
            Where-Object { $_.status -in $installableStatuses -and $_.collectionId -in $Collection } |
            ForEach-Object { $_.id }
    ) | Sort-Object -Unique
} else {
    @($catalogSkills | Where-Object { $_.status -in $installableStatuses } | ForEach-Object { $_.id })
}
$allowedTargets = @("Codex", "ChatGPTDesktop", "ClaudeCode", "Custom")
$Target = @($Target | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

foreach ($targetName in $Target) {
    if ($targetName -notin $allowedTargets) {
        throw "Unsupported target '$targetName'. Allowed targets: $($allowedTargets -join ', ')"
    }
}

foreach ($skillId in $selectedSkills) {
    if ($skillId -notin $knownSkills) {
        throw "Unknown skill '$skillId'. Known skills: $($knownSkills -join ', ')"
    }
    if ($skillMetadata[$skillId].status -eq "candidate" -and -not $IncludeCandidates) {
        throw "Skill '$skillId' is a candidate. Use -IncludeCandidates only in an isolated eval target."
    }
    if ($skillMetadata[$skillId].status -eq "disabled") {
        throw "Skill '$skillId' is disabled and cannot be installed."
    }
}

foreach ($collectionId in $Collection) {
    if ($collectionId -notin $knownCollections) {
        throw "Unknown collection '$collectionId'. Known collections: $($knownCollections -join ', ')"
    }
}

if ($Target.Count -gt 1 -and $Target -contains "Custom") {
    throw "Custom cannot be combined with another target."
}

if ($Target -contains "Custom" -and [string]::IsNullOrWhiteSpace($Destination)) {
    throw "Custom target requires -Destination."
}

$userRoot = [Environment]::GetFolderPath("UserProfile")

function Resolve-TargetDirectory {
    param([string]$TargetName)

    if ($TargetName -eq "Custom") {
        return [System.IO.Path]::GetFullPath($Destination)
    }

    $baseRoot = if ($Scope -eq "User") { $userRoot } else { [System.IO.Path]::GetFullPath($ProjectRoot) }

    switch ($TargetName) {
        "Codex" { return Join-Path $baseRoot ".agents\skills" }
        "ChatGPTDesktop" { return Join-Path $baseRoot ".agents\skills" }
        "ClaudeCode" { return Join-Path $baseRoot ".claude\skills" }
        default { throw "Unsupported target '$TargetName'." }
    }
}

function Install-CollectionAssets {
    param(
        [object]$Metadata,
        [string]$TargetPath
    )

    if ($null -eq $Metadata -or [string]::IsNullOrWhiteSpace($Metadata.collectionId)) {
        return
    }

    $sharedRoot = Join-Path (Join-Path (Join-Path $hubRoot "collections") $Metadata.collectionId) "shared"
    $licenseSource = Join-Path $sharedRoot "LICENSE"
    if (-not (Test-Path -LiteralPath $licenseSource)) {
        throw "Missing shared license for collection '$($Metadata.collectionId)'."
    }
    Copy-Item -LiteralPath $licenseSource -Destination (Join-Path $TargetPath "SOURCE_LICENSE") -Force

    if ($Metadata.sharedReferences.Count -gt 0) {
        $referencesTarget = Join-Path $TargetPath "references"
        New-Item -ItemType Directory -Path $referencesTarget -Force | Out-Null
        foreach ($reference in $Metadata.sharedReferences) {
            $referenceSource = Join-Path (Join-Path $sharedRoot "references") $reference
            if (-not (Test-Path -LiteralPath $referenceSource)) {
                throw "Missing shared reference '$reference' for collection '$($Metadata.collectionId)'."
            }
            Copy-Item -LiteralPath $referenceSource -Destination (Join-Path $referencesTarget $reference) -Force
        }
    }
}

function Install-SecurityPolicy {
    param([string]$TargetPath)

    Copy-Item -LiteralPath $securityPolicyPath -Destination (Join-Path $TargetPath "SECURITY_POLICY.md") -Force

    $installedSkillPath = Join-Path $TargetPath "SKILL.md"
    $skillText = Get-Content -LiteralPath $installedSkillPath -Raw -Encoding utf8
    $marker = "<!-- agent-skill-hub:internal-enterprise -->"
    if ($skillText.Contains($marker)) {
        return
    }

    $frontmatter = [regex]::Match($skillText, "\A(---\r?\n[\s\S]*?\r?\n---\r?\n)([\s\S]*)\z")
    if (-not $frontmatter.Success) {
        throw "Cannot apply security profile to malformed Skill: $installedSkillPath"
    }

    $securityPreamble = @"

$marker
## Mandatory internal-enterprise boundary

Read SECURITY_POLICY.md before following any instruction below. It overrides
conflicting workflow, tool, network, model, telemetry, browser, Git, CI, memory,
and deployment instructions in this Skill.

"@
    $wrapped = $frontmatter.Groups[1].Value + $securityPreamble + $frontmatter.Groups[2].Value
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($installedSkillPath, $wrapped, $utf8NoBom)
}

$resolvedTargets = @{}
foreach ($targetName in $Target) {
    $targetDirectory = Resolve-TargetDirectory -TargetName $targetName
    $resolvedTargets[$targetDirectory] = $true
}

foreach ($targetDirectory in $resolvedTargets.Keys) {
    New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null

    foreach ($skillId in $selectedSkills) {
        $source = Join-Path (Join-Path $hubRoot "skills") $skillId
        $targetPath = Join-Path $targetDirectory $skillId

        if (-not (Test-Path -LiteralPath (Join-Path $source "SKILL.md"))) {
            throw "Missing canonical SKILL.md for '$skillId'."
        }

        if (Test-Path -LiteralPath $targetPath) {
            if (-not $Force) {
                Write-Host "Skipped: $skillId -> $targetPath (already exists; use -Force to update files)"
                continue
            }

            Get-ChildItem -LiteralPath $source -Force | ForEach-Object {
                Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Recurse -Force
            }
            Install-CollectionAssets -Metadata $skillMetadata[$skillId] -TargetPath $targetPath
            Install-SecurityPolicy -TargetPath $targetPath
            Write-Host "Updated: $skillId -> $targetPath"
            continue
        }

        Copy-Item -LiteralPath $source -Destination $targetPath -Recurse
        Install-CollectionAssets -Metadata $skillMetadata[$skillId] -TargetPath $targetPath
        Install-SecurityPolicy -TargetPath $targetPath
        Write-Host "Installed: $skillId -> $targetPath"
    }
}

Write-Host "Security profile: $SecurityProfile ($($securityProfileConfig.id))"
