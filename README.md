# MetroidFlasher

## Download

**[MetroidFlasher 1.1.1 (zip, Windows, 12 MB)](https://github.com/AtoZ0to90to9AtoZ/MetroidFlasher-releases/releases/download/v1.1.1/MetroidFlasher-1.1.1.zip)**
· [release page](https://github.com/AtoZ0to90to9AtoZ/MetroidFlasher-releases/releases/tag/v1.1.1)
· [XDA thread (support and reports)](https://xdaforums.com/t/oneclick-unbrick-flash-1-0-2-metroidflasher-unbrick-custom-roms-gapps-from-only-the-bootloader.4808789/)

| File | SHA-256 |
|---|---|
| `MetroidFlasher-1.1.1.zip` (the tool) | `ed3203af3a2dbd839c6650e3d56d7f6e2531e0ed0f9373160249e6aba6b00f17` |
| `MetroidFlasher-1.1.1-source.zip` (the source) | `1196253091578b60d16013a463380b120505e49d028352417ab6f99a3e126d6a` |

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

Put your files next to `MetroidFlasher.exe`, in the numbered folders, or use **Choose folder...** next to a job
to use a folder you already have (no copying; remembered). Only fill the ones you need. New files show up by
themselves.

| Folder | What goes in it |
|---|---|
| `1-STOCK\` | The Nothing OS stock image folder (all the `.img` files). Used by **Unbrick**. |
| `2-ROM\` | One custom ROM: its `.zip` exactly as downloaded (unpacked for you), or its `.img` files. Used by **Install a custom ROM** and **ROM + Google apps**. |
| `3-GAPPS\` | The NikGapps or MindTheGapps zip(s) for the package you want (see below). Any file name: the tool finds them by checksum. |
| `4-BACKUPS\` | Leave empty for now. |

**platform-tools** (adb and fastboot) are not in the download. If you already have them (on PATH, in the
Android SDK, or in `C:\platform-tools` / `C:\Android\platform-tools`), the tool uses yours when its fastboot is
34.0.0 or newer. You can also pick the folder on the **Tools** tab ("Use my own platform-tools folder").
Otherwise it downloads them from Google on first run: a fixed version (r37.0.1) checked against its SHA-256.
Nothing else is downloaded.

## Google apps (GApps): how it works

### Baked in, not installed
MetroidFlasher doesn't install GApps the usual way. There's no recovery, no sideload and no install script.
On your PC it opens the ROM's own `system`, `system_ext` and `product` images, puts the Google apps inside them
exactly where the GApps installer would, removes the ROM apps they replace, and builds one new super image.
The phone gets **one flash** and boots straight into Google's setup.

Your zips are found by **checksum**, not by name, so a renamed or different download can never be baked by
mistake. Each supported zip was mapped and test-flashed on a real Nothing Phone (3).

### Pick ONE choice. These are the only ones:

| Choice in the menu | What you get | Zips it uses from `3-GAPPS` |
|---|---|---|
| **Core** | Google Play only: Play Services, Play Store, Google's setup wizard. The ROM keeps its own dialer, contacts, messages, clock, keyboard and launcher. | NikGapps **basic** + **SetupWizard** add-on |
| **Core + Pixel setup** | Core, with the Pixel setup wizard instead (Pixel welcome screens + Pixel restore). | NikGapps **basic** + **PixelSetupWizard** add-on |
| **Basic** | Core + Google Dialer, Contacts, Messages and Clock (the ROM's versions are removed). | NikGapps **basic** + **SetupWizard** add-on |
| **Basic + Pixel setup** | Basic, with the Pixel setup wizard instead. | NikGapps **basic** + **PixelSetupWizard** add-on |
| **Omni** | The everyday Google set. Has its own setup wizard. | NikGapps **omni** (nothing else) |
| **Stock** | The most Google apps that fit this phone (Pixel Launcher, Gboard and more). Has its own setup wizard. | NikGapps **stock** (nothing else) |
| **MindTheGapps** | The MindTheGapps set. The ROM keeps its own dialer, messages, keyboard and launcher. | **MindTheGapps** (nothing else) |

The rule, in one line: **Core or Basic take exactly one setup wizard (Google's or Pixel's). Omni, Stock and
MindTheGapps stand alone. Nothing else is ever added.**

- Core and Basic have no setup wizard of their own, so they need one of the two wizard add-ons. If you have
  **both** wizard zips in `3-GAPPS`, only the one your choice names is used. The other is ignored.
- Omni, Stock and MindTheGapps ignore **every** other zip in the folder, the wizard add-ons included.

Exact file names (any name works, these are just what the downloads are called):
- `NikGapps-basic-arm64-17-20261110-signed.zip`
- `NikGapps-Addon-17-SetupWizard-20261110-signed.zip`
- `NikGapps-Addon-17-PixelSetupWizard-20261110-signed.zip`
- `NikGapps-omni-arm64-17-20261110-signed.zip`
- `NikGapps-stock-arm64-17-20261110-signed.zip`
- `MindTheGapps-17.0.0-arm64-20261022_132445.zip`

### Extra add-ons are never baked
You can leave anything in `3-GAPPS`: Gmail, YouTube, Maps, PixelSpecifics, debloaters, older builds.
**None of it is baked in.** The window lists every zip in the folder as **USED** or **not used**, with the
reason, so nothing is silently skipped:

```
USED      NikGapps-basic-arm64-17-20261110-signed.zip
USED      NikGapps-Addon-17-PixelSetupWizard-20261110-signed.zip
not used  NikGapps-Addon-17-SetupWizard-20261110-signed.zip - not used by Basic + Pixel setup.
not used  NikGapps-Addon-17-Gmail-20261110-signed.zip - add-ons aren't baked. Sideload it after the flash.
```

Want more Google apps? Get them from the **Play Store**, or **sideload** them yourself after the flash.

**Why no add-ons?** Every extra app eats space in the phone's `super` partition (Stock already uses 8.8 of
9.0 GB), add-ons can clash with each other or with the package, and every combination we ship has been
flashed and booted on a real phone. Seven tested choices beat thousands of untested ones.

### What gets removed from the ROM
Only what the GApps replace:
- Always: the ROM's microG pieces (Google's real Play Services goes in instead).
- The ROM app a Google app replaces, as listed by that Google app's own installer. For example Google Dialer
  replaces the ROM's Dialer, a Google setup wizard replaces the ROM's setup wizard.

Anything the GApps don't replace stays: eSIM, core system apps, the ROM's own settings.

### Customize (optional, remove only)
**Customize...** lets you untick apps from the choice you picked. It **only removes**: you can't add apps that
aren't in the package. When you leave a Google app out, the ROM's own app for that job stays. Play Services,
Play Store and the setup wizard can't be unticked (the phone needs them).

### Changing your mind later
Keeping data (no wipe) only works when the ROM **and** the GApps choice are exactly the same as last time.
Switching choices (say Basic to Stock) means a wiped flash: Google apps and the ROM apps they replaced live
at different paths, and Android gets confused if you swap them under existing data.

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

The source is in [`source/`](source) (Python, no outside packages) and as `MetroidFlasher-1.1.1-source.zip` on
the release page. Run it with `py gui.py`; tests: `py tests/run_tests.py`.
It's **source-available** (see [LICENSE.txt](LICENSE.txt)): read it, build it, change it for your own use, send
fixes back. Please don't publish modified builds or present them as MetroidFlasher.
