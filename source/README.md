# MetroidFlasher 1.0

A Windows flasher for the **Nothing Phone (3)** ("metroid", fastboot product `sun`). It works from the plain
bootloader screen, so it still works when recovery and fastbootd are broken.

It does three jobs:
- **Unbrick to stock Nothing OS.**
- **Install a custom ROM** (crDroid is the tested one).
- **Install a ROM with Google apps built in.** The Google apps are baked into the ROM's images on your PC
  before anything is sent to the phone. No recovery, no sideloading, no taps on the phone during the flash.

> **Every job erases the phone** unless you use "keep data" exactly as described below. Back up first.
> Needs an unlocked bootloader. Unbricking to stock also needs critical partitions unlocked.

## Folders

Put your files next to `MetroidFlasher.exe`, in the numbered folders. Only fill the ones you need.

| Folder | What goes in it |
|---|---|
| `1-STOCK\` | The Nothing OS stock image folder (all the `.img` files). Used by **Unbrick**. |
| `2-ROM\` | One custom ROM as `.img` files. Used by **Install a custom ROM** and **ROM + Google apps**. |
| `3-GAPPS\` | The NikGapps or MindTheGapps zip(s) for the package you want (see below). Any file name: the tool finds them by checksum. |
| `4-BACKUPS\` | Leave empty for now. |

**platform-tools** (adb and fastboot) are not in the download. On first run the tool downloads them from
Google, a fixed version (r37.0.1) checked against its SHA-256. Nothing else is downloaded.

## Google apps packages

Pick one package. Every package brings Google's real Play Services and removes the ROM's microG pieces.
Packages that bring Google's setup wizard get it automatically.

| Package | What you get | Zip(s) needed in `3-GAPPS` |
|---|---|---|
| **Core** | Google Play only: Play Services, Play Store, Google setup wizard. The ROM keeps its own dialer, contacts, messages, clock, keyboard and launcher. | NikGapps **basic** + the **SetupWizard** add-on |
| **Basic** | Core + Google Dialer, Contacts, Messages and Clock. The ROM's versions of those are removed. | NikGapps **basic** + the **SetupWizard** add-on |
| **Omni** | The everyday Google set. | NikGapps **omni** |
| **Stock** | The most Google apps that fit this phone (Pixel Launcher, Gboard and more). This is the maximum: nothing can be added on top. | NikGapps **stock** |
| **MindTheGapps** | The MindTheGapps set: Play Services, Play Store, the Google app, contact/calendar sync, Digital Wellbeing, Android Auto, Speech Services, TalkBack. The ROM keeps its own dialer, messages, keyboard and launcher, and its own setup wizard runs before Google's (same as MindTheGapps on LineageOS). | **MindTheGapps-17.0.0-arm64-20261022_132445.zip** |

All four have been flashed and booted on a real Nothing Phone (3) with crDroid 13.0.

### Customize (optional)
**Customize...** lets you untick apps from the package you picked. It only removes: you can't add apps that
aren't in the package (get extras from the Play Store). When you leave a Google app out, the ROM's own app for
that job stays.

### Root (optional)
You can give the tool **your own pre-rooted `init_boot.img`** (Magisk, KernelSU LKM: the usual way on this phone,
and the one tested) **or `boot.img`** (APatch, KernelSU GKI mode: accepted, untested on metroid). It
must be made **from this exact ROM**. The tool doesn't patch anything itself. It checks that your image matches
the ROM (Android version and security patch) and refuses one that doesn't. Your image replaces the ROM's own.

## How to use

1. Fill the folders you need.
2. Phone: power off, hold **Power + Volume Down** to reach the bootloader, then plug in USB.
3. Double-click `MetroidFlasher.exe`. The top line shows when it sees the phone.
4. Pick the job. For Google apps: pick the package, and optionally Customize and Root.
5. **Run a dry run first** (it's ticked by default). It checks the phone, your files, the bake and that
   everything fits, prints the full plan, and flashes nothing. The real flash only unlocks after a dry run of the
   same files passes.
6. Run it for real and type `FLASH` when asked. Don't unplug the cable while it works.

The ROM goes to **slot b**. The first boot can sit on the boot logo for several minutes. That is normal.

## Wipes: what gets erased and when

There are two different wipes:
- **Super is rebuilt on every flash.** Every job writes one new `super` image with a fresh partition table.
  Everything that was in `super` before (system, vendor, product and so on, on both slots) is gone. That's what
  makes room for Stock, and it's how leftovers from older installs are cleared.
- **Data wipe** (your apps, settings and files) happens on every job by default.
  **Keep data** is allowed only when you're flashing the **same ROM with the same Google apps package and the same
  Customize choices** as last time. If anything changed, the tool refuses to keep data. Mixing old data with a
  different set of Google apps breaks apps (for example, Gboard crashes on start).

## What it never touches

Device-unique partitions such as `persist`, `modemst1/2`, `fsg`, `fsc` (IMEI, radio calibration, sensors),
`devinfo`, `frp`, `keystore` and `secdata`, even if they're in your folder. It never flashes vbmeta with
`--disable-verification`.

## If something goes wrong (brick ladder)

Start at the top and go down only if the step above can't help.
1. **Phone boots to recovery or the bootloader, and fastbootd works:** the normal stock `flash_all` from the
   community stock package can fix it.
2. **Only the bootloader works** (recovery, fastbootd or super are broken): **MetroidFlasher** -> *Unbrick to
   stock Nothing OS*. This is what it was built for.
3. **Nothing works and the phone shows up as Qualcomm 9008 (EDL):** use Nothing's official tool or service.
   MetroidFlasher does not do EDL.

If a flash stops partway, the tool tells you where. Nothing after that point was sent and the phone stays in
the bootloader. Fix the cause and run it again.

## Known quirks
- **The phone doesn't show up after a reboot.** Windows sometimes loses the phone on USB after it reboots from
  the bootloader into Android. Unplug and replug the cable. Use a USB port directly on the PC, not a hub.
- After a wipe, Android asks you to **Allow USB debugging** for this PC again.
- Mobile data may start switched off after a wipe. Turn it on in Settings.
- With newer Google Play Services you may see a background "Unexpected safety source: AdvancedProtection" crash
  at first boot. It restarts by itself and does no harm.

## Logs and reports
Each run writes a log to `tool\work\flash_*.log` (or use **Save log...**). When reporting a problem, post the
log, your ROM name, the package you picked and what happened.

Get MetroidFlasher only from the official release page. Don't edit or replace anything in `tool\`: the tool
checks its own files and refuses to flash with a modified copy.
