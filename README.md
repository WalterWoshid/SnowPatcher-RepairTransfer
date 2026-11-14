<!--suppress HtmlDeprecatedAttribute -->
<h1 align="center">Repair Parts &amp; Spare Wheel Transfer</h1>

<p align="center">
  <img alt="Version 1.0.4" src="https://img.shields.io/badge/Version-1.0.4-458588?labelColor=ebdbb2&amp;style=flat">
  <img alt="SnowPatcher package" src="https://img.shields.io/badge/Package-SnowPatcher-512BD4?labelColor=ebdbb2&amp;style=flat">
  <a href="https://mod.io/g/snowrunner/m/repair-parts-spare-wheel-transfer"><img alt="mod.io" src="https://img.shields.io/badge/mod.io-Mod%20page-8F5BFF?labelColor=ebdbb2&amp;style=flat"></a>
</p>

<p align="center">Transfer repair parts and spare wheels through SnowRunner's Functions menu.</p>

Move supplies between storage addons and maintenance trailers, with inventory
limits and whole-wheel transfers. Preserves the single-player checks and rollback.
Created by **WalterWoshid**.

[Download](https://mod.io/g/snowrunner/m/repair-parts-spare-wheel-transfer) ·
[SnowPatcher](https://github.com/WalterWoshid/SnowPatcher/releases/latest)

## Installation

Requires SnowPatcher 1.1.0 or newer.

Close SnowRunner, add the built ZIP to SnowPatcher, enable the mod and click Apply.
Players do not need Python or an installer. The source folder itself is not an
installable package.

## Languages

Includes English, French, Italian, German, Spanish (Spain), Czech, Japanese,
Korean, Polish, Portuguese (Brazil), Russian, Simplified Chinese and Traditional
Chinese. Menu labels, hints and transfer units use the selected game language.

## Development

Requires PowerShell 5.1+, Python 3.10+, a SnowPatcher source checkout and known
original game files. Run from this folder:

```powershell
.\Build.ps1 -GameRoot 'D:\OriginalSnowRunner' -SnowPatcherRoot 'D:\Projects\Snowpatcher' -OutputPath '.\artifacts\RepairTransfer.zip'
```

`snowpatcher.source.json` defines the package, `captions` holds one file per
language, and `_bytecode.py` contains the mod logic. Keep both `{STRING}`
placeholders in `UI_TRANSFER_PROMPT` when editing translations. `baseline.json`
guards required game code. Building reads the game files and cleans up temporary payloads.

Changed required modules or native definitions need a reviewed rebuild. Other
mods editing the same HUD module conflict; independent configuration edits,
including Deliver Unpacked Cargo, can be combined.

The private maintenance workflow builds reviewed source against verified Steam
files and publishes compatible releases. Public checks need no game credentials.

## Show your support

Give a ⭐ if this project helped you!
