# MetroidFlasher

## Download

**[MetroidFlasher 1.0.1 (zip, Windows, 12 MB)](https://github.com/AtoZ0to90to9AtoZ/MetroidFlasher-releases/releases/download/v1.0.1/MetroidFlasher-1.0.1.zip)**
· [release page](https://github.com/AtoZ0to90to9AtoZ/MetroidFlasher-releases/releases/tag/v1.0.1)
· [XDA thread (support and reports)](https://xdaforums.com/t/tool-unbrick-autoflash-beta-metroidflasher-unbrick-custom-rom-gapps-from-the-plain-bootloader.4808789/)

| File | SHA-256 |
|---|---|
| `MetroidFlasher-1.0.1.zip` (the tool) | `59f911efc36c5504da3003248f5afe069d85e935996faf91eab4873e714ddd12` |
| `MetroidFlasher-1.0.1-source.zip` (the source) | `c7ca95c7f6937e2da61eabfda1c370327780f7e9cbe81623abed4bb239f443c5` |

A Windows flasher for the **Nothing Phone (3)** ("metroid", fastboot product `sun`). It works from the plain
bootloader screen, so it still works when recovery and fastbootd are broken.

It does three jobs:
- **Unbrick to stock Nothing OS.**
- **Install a custom ROM** (crDroid is the tested one).
- **Install a ROM with Google apps built in.** The Google apps are baked into the ROM's images on your PC
  before anything is sent to the phone. No recovery, no sideloading, no taps on the phone during the flash.

Optional with either ROM job: **flash your own pre-rooted image** (Magisk / KernelSU `init_boot.img`) in the
same run, so the phone boots rooted. The tool checks it matches the ROM and never patches anything itself.

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

**platform-tools** (adb and fastboot) are not in the download. If you already have them (on PATH, in the
Android SDK, or in `C:\platform-tools` / `C:\Android\platform-tools`), the tool uses yours when its fastboot is
34.0.0 or newer. You can also pick the folder on the **Tools** tab ("Use my own platform-tools folder").
Otherwise it downloads them from Google on first run: a fixed version (r37.0.1) checked against its SHA-256.
Nothing else is downloaded.

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
4. Pick the job. For Google apps: pick the package, and optionally Customize. For root (either ROM job): pick
   your pre-rooted image with the Root option (it can be in any folder).
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

## Rescuing a stuck phone

Phone stuck after a bad flash, update or mod (with any tool)? Pick the first line that fits:
1. **Recovery or fastbootd still works:** the normal stock `flash_all` from the community stock package can
   fix it.
2. **Only the bootloader works** (recovery, fastbootd or super are broken): **MetroidFlasher** -> *Unbrick to
   stock Nothing OS*. This is what it was built for.
3. **Nothing works and the phone shows up as Qualcomm 9008 (EDL):** use Nothing's official tool or service.
   MetroidFlasher does not do EDL.

## If MetroidFlasher stops

The dry run checks the phone, your files and that everything fits before anything is flashed, so almost every
problem stops there with nothing sent to the phone. If a real flash stops partway (a cable pulled, for example),
the tool tells you where; nothing after that point was sent and the phone stays in the bootloader. Fix the cause
and run it again.

## Tips
- After a wipe, Android asks you to **Allow USB debugging** for this PC again.
- Mobile data may start switched off after a wipe. Turn it on in Settings.

## Logs and reports
Each run writes a log to `tool\work\flash_*.log` (or use **Save log...**). When reporting a problem, post the
log, your ROM name, the package you picked and what happened.

Get MetroidFlasher only from the official release page. Don't edit or replace anything in `tool\`: the tool
checks its own files and refuses to flash with a modified copy.

## Source

The source is in [`source/`](source) (Python, no outside packages) and as `MetroidFlasher-1.0.1-source.zip` on
the release page. Run it with `py gui.py`; tests: `py tests/run_tests.py`.
It's **source-available** (see [LICENSE.txt](LICENSE.txt)): read it, build it, change it for your own use, send
fixes back. Please don't publish modified builds or present them as MetroidFlasher.

Not affiliated with Nothing Technology Limited.
