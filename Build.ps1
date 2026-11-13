<#
.SYNOPSIS
Build Repair Transfer as a SnowPatcher package, without installing anything.
.DESCRIPTION
Requires Python 3.10+ for the existing Repair Transfer compiler. Players need
only the resulting ZIP. Compiled game files stay in a temporary build directory.
.EXAMPLE
.\Build.ps1 -GameRoot 'D:\OriginalSnowRunner' -SnowPatcherRoot 'D:\Projects\Snowpatcher' -OutputPath '.\artifacts\RepairTransfer.zip'
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$GameRoot,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string]$Python = 'python',
    [string]$SnowPatcherRoot = (Join-Path $PSScriptRoot '..\..\Snowpatcher')
)
$work = $null
try {
    $toolsRoot = Join-Path (Get-Item -LiteralPath $SnowPatcherRoot).FullName 'tools'
    if (-not (Test-Path -LiteralPath (Join-Path $toolsRoot 'New-ModPackage.ps1') -PathType Leaf)) { throw 'Select the SnowPatcher source folder with -SnowPatcherRoot.' }
    . (Join-Path $toolsRoot '_Common.ps1')
    . (Join-Path $toolsRoot '_Authoring.ps1')
    $script:SnowAuthorGameRoot = (Get-Item -LiteralPath $GameRoot).FullName.TrimEnd('\','/')
    Assert-SnowNotLinked $script:SnowAuthorGameRoot
    $destination = [IO.Path]::GetFullPath($OutputPath)
    if ($destination.StartsWith($script:SnowAuthorGameRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Keep build output outside the game folder.' }
    if ([IO.File]::Exists($destination) -or [IO.Directory]::Exists($destination)) { throw "Output already exists: $destination" }
    $pythonCommand = @(Get-Command $Python -CommandType Application -ErrorAction Stop)[0]
    & $pythonCommand.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'The developer build needs Python 3.10 or newer. Players do not need Python.' }
    $pak = Get-SnowAuthorFile $script:SnowAuthorGameRoot 'preload/paks/client/initial.pak'
    $metadata = Get-SnowAuthorFile $script:SnowAuthorGameRoot 'Sources/BinEditor/meta_desc_ssl.ps'
    $beforePak = Get-SnowFileHash $pak
    $beforeMetadata = Get-SnowFileHash $metadata
    $baseline = Read-SnowAuthorJson ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'baseline.json')))
    if ($beforeMetadata -cne $baseline.native_metadata_sha256) { throw 'Required native definitions changed. Review this mod before rebuilding.' }

    $temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\','/')
    $work = Join-Path $temporaryRoot ('SnowPatcherRepairTransfer-' + [Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory((Join-Path $work 'inputs'))
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'snowpatcher.source.json') -Destination (Join-Path $work 'snowpatcher.source.json')
    $source = Read-SnowAuthorJson ([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'snowpatcher.source.json')))
    foreach ($path in $source.include) {
        $inputFile = Get-SnowAuthorFile $PSScriptRoot $path
        $stagedFile = Join-Path $work (Get-SnowSafePath $path)
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($stagedFile))
        [IO.File]::Copy($inputFile, $stagedFile, $true)
    }
    foreach ($operation in $source.operations) {
        if ($operation.Contains('editsFile')) {
            $inputFile = Get-SnowAuthorFile $PSScriptRoot $operation.editsFile
            $stagedFile = Join-Path $work (Get-SnowSafePath $operation.editsFile)
            [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($stagedFile))
            [IO.File]::Copy($inputFile, $stagedFile, $true)
        }
    }
    $script:SnowAuthorBytes = @{}
    $script:SnowAuthorContainers = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    $script:SnowAuthorObserved = [Collections.Specialized.OrderedDictionary]::new([StringComparer]::Ordinal)
    foreach ($variant in @('release','profile','debug')) {
        $name = 'initial_' + $variant + '.sslbundle'
        $target = @{file='preload/paks/client/initial.pak';members=@(@{format='zip';name=('[ssl_cache]/' + $name)})}
        [IO.File]::WriteAllBytes((Join-Path $work ('inputs/' + $name)), (Get-SnowAuthorResource $target))
    }
    foreach ($name in $baseline.ui_config_sha256.Keys) {
        $target = @{file='preload/paks/client/initial.pak';members=@(@{format='zip';name='initial.cache_block'},@{format='cache';name=$name})}
        [IO.File]::WriteAllBytes((Join-Path $work ('inputs/' + [IO.Path]::GetFileName($name))), (Get-SnowAuthorResource $target))
    }
    & $pythonCommand.Source -B (Join-Path $PSScriptRoot '_prepare.py') --work $work --baseline (Join-Path $PSScriptRoot 'baseline.json')
    if ($LASTEXITCODE -ne 0) { throw 'Repair Transfer payload preparation failed. No package was released.' }
    $package = Join-Path $work 'RepairTransfer.zip'
    & (Join-Path $toolsRoot 'New-ModPackage.ps1') $work -GameRoot $script:SnowAuthorGameRoot -OutputPath $package -Strict
    if (-not [IO.File]::Exists($package)) { throw 'Package generation failed.' }
    if ((Get-SnowFileHash $pak) -cne $beforePak -or (Get-SnowFileHash $metadata) -cne $beforeMetadata) { throw 'Game files changed during the build. Try again with a stable original copy.' }
    $parent = [IO.Path]::GetDirectoryName($destination)
    [void][IO.Directory]::CreateDirectory($parent)
    Assert-SnowNotLinked $parent
    [IO.File]::Move($package, $destination)
    Write-Output $destination
} catch {
    Write-Verbose $_.ScriptStackTrace
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    if ($work -and [IO.Directory]::Exists($work)) {
        $resolved = [IO.Path]::GetFullPath($work)
        if ([IO.Path]::GetDirectoryName($resolved) -eq $temporaryRoot -and [IO.Path]::GetFileName($resolved) -cmatch '^SnowPatcherRepairTransfer-[0-9a-f]{32}$') {
            Assert-SnowNotLinked $resolved
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
}
