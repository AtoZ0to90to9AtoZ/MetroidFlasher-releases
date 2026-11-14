# MetroidFlasher

Bootloader-only flashing tool for the **Nothing Phone (3)** (`metroid`) on Windows.

Flashes stock Nothing OS, custom ROMs, and ROM + GApps **from the plain bootloader** —
no fastbootd or working recovery needed. It writes the dynamic `super` partition piece by
piece straight from the bootloader, so it can also bring back a phone that only boots to
the bootloader.

## ⬇️ Download

**[MetroidFlasher 0.2.0 beta — download the zip](https://github.com/AtoZ0to90to9AtoZ/MetroidFlasher-releases/releases/download/v0.2.0-beta/MetroidFlasher-0.2.0-beta.zip)** (19 MB, Windows)

SHA-256: `64936f097a61f03c16f14de127b77c9d5284494465082baee28c3a943f065df1`

💬 **Support, questions and test reports:** [XDA thread](https://xdaforums.com/t/tool-unbrick-autoflash-beta-metroidflasher-unbrick-custom-rom-gapps-from-the-plain-bootloader.4808789/)

Release page: [v0.2.0-beta](https://github.com/AtoZ0to90to9AtoZ/MetroidFlasher-releases/releases/tag/v0.2.0-beta)

## Status

**Beta.** Downloads only from this repo's Releases.
Do not use re-uploads or modified copies: the tool checks its own files and refuses to
flash if they were changed.

## What it does (v0.2.0 beta)

1. Unbrick / install stock Nothing OS
2. Flash a custom ROM
3. Flash ROM + GApps (NikGapps basic or omni, or MindTheGapps) — removes the ROM apps
   your GApps duplicate before first boot, handles verity and the wipe
4. Check your downloaded files (SHA-256)

Every option offers a dry run first.

## Tested on a real phone

- Stock Nothing OS install, and rescue from erased recovery + super
- crDroid v13.0 (Android 17)
- crDroid + NikGapps omni and crDroid + MindTheGapps (dual SIM / eSIM working)

## Testers wanted

Brick testers and custom-ROM flashers. Report your ROM, GApps, region/firmware, what
happened, and the log from `tool\work\flash_*.log` in an issue.

**You flash at your own risk. Not affiliated with Nothing.**
