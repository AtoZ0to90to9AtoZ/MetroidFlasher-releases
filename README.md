# MetroidFlasher

Bootloader-only flashing tool for the **Nothing Phone (3)** (`metroid`) on Windows.

Flashes stock Nothing OS, custom ROMs, and ROM + GApps **from the plain bootloader** —
no fastbootd or working recovery needed. It writes the dynamic `super` partition piece by
piece straight from the bootloader, so it can also bring back a phone that only boots to
the bootloader.

## Status

**Beta — coming soon.** Downloads will appear under [Releases](../../releases) only.
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
