**MetroidFlasher 1.1.0 - Nothing Phone (3) "metroid" only.**

## New in 1.1.0 (since 1.0.2)
- **Drop the ROM .zip straight in.** Put the custom ROM's .zip into 2-ROM exactly as you downloaded it (the kind
  with payload.bin inside) - no unzipping or payload dumping on your side. Works for "Install a custom ROM" and
  "Install ROM + Google apps". A folder of .img files still works too.
- The zip is unpacked by **payload-dumper-go 2.1.0** (ssut, Apache-2.0). It is NOT shipped: the first time you
  flash a zip it is downloaded from its official GitHub release and checked by SHA-256 (pinned version; if the
  check fails nothing is used). Same idea as Google's platform-tools.
- The zip's SHA-256 is recorded with every unpacked image, so images from a different zip are never reused.
- Tested: crDroid 13.0 (Android 17) flashed from its downloaded zip onto a Nothing Phone (3), booted to setup.

---

## New in 1.0.2 (since 1.0.1)
- **Only ever your Nothing Phone (3).** The phone status panel and every Tools / command-box command now lock
  onto the Nothing Phone (3) on USB, by serial. Other phones, including ones on wireless adb, are ignored.
  (1.0.1's status panel could show another phone that was on wireless adb.) Flashing itself was always locked.
- **Phone check right before flashing.** Building super can take a while on a slow PC; right before the first
  write the tool checks the same phone is still in the bootloader. If it isn't, it waits for it (nothing has
  been written yet).
- **Choose folder...** next to each job: use the folder your files are already in (remembered). New files in
  the folders show up by themselves; the Refresh button is gone.
- The Flash tab fits a 1366x768 screen. "Check my files" moved to the Tools tab. No more "BETA" in the header.
- Typing FLASH and pressing Enter confirms; Esc cancels.

## New in 1.0.1
- **Uses your own adb + fastboot if you have them.** Found by itself on PATH, in the Android SDK or the usual
  folders (`C:\platform-tools`, `C:\Android\platform-tools`, ...), or pick the folder on the Tools tab. Your
  fastboot must be 34.0.0 or newer (older ones have trouble with big partitions like super); otherwise Google's
  is downloaded like in 1.0.
- Root wording: `init_boot.img` (Magisk, KernelSU LKM) is the usual and tested way on this phone; `boot.img`
  (APatch, KernelSU GKI mode) is accepted but untested.
- "Known quirks" are now plain tips. "(BETA)" is gone from the log window.

---

**MetroidFlasher 1.0 - Nothing Phone (3) "metroid" only. Every job erases the phone unless "keep data" is allowed (see below). Back up first.**

## New in 1.0 (since 0.7.1-beta)

### Google apps are built in on your PC
- **ROM + Google apps is now one flash.** The Google apps are baked into the ROM's images on your PC and flashed
  with the ROM in one go. There's no recovery step, no sideloading and nothing to tap on the phone. (0.7.x
  installed them from recovery over ADB.)
- **Pick a package: Core, Basic, Omni, Stock or MindTheGapps.**
  - Core = Google Play only (the ROM keeps its own apps).
  - Basic = Core + Google Dialer, Contacts, Messages, Clock.
  - Omni = the everyday set.
  - Stock = the most that fits. It's the maximum: nothing goes on top.
  - MindTheGapps = the MindTheGapps set (Play, the Google app, sync, Wellbeing, Android Auto, TalkBack), for
    people who prefer it to NikGapps. The ROM's own apps and setup wizard stay.
  All five were flashed and booted on a real phone.
- **Stock fits now.** Every image is rebuilt to the size of its content before `super` is built, so Stock fits
  with room to spare.
- **Native Windows build.** The image builder is the tool's own code. No WSL, Linux or extra programs are needed.
  Its output matches the Linux-built images exactly. A Stock bake takes about 2 minutes.
- **Zips are recognized by checksum**, so a renamed or different download can't be baked by mistake.
- Core and Basic get Google's setup wizard automatically (needs the SetupWizard add-on zip in `3-GAPPS`).

### Customize and Root
- **Customize...**: untick apps from the package you picked. Remove only. The ROM's own app stays for anything
  you leave out.
- **Root (optional)**: bring your own pre-rooted image made from the same ROM: `init_boot.img` for Magisk and
  KernelSU (LKM), the usual way on this phone and the one tested (KernelSU Next). `boot.img` (APatch, KernelSU GKI
  mode) is accepted too but untested on metroid. The dry run checks that it matches the ROM. The tool never patches anything itself.

### Safer data handling
- **Keep data only when nothing changed:** same ROM, same package, same Customize choices. Otherwise the data
  wipe is forced. A dirty flash with a different set of Google apps made Gboard crash in testing.
- **Super is rebuilt on every flash** (a fresh partition table), so nothing from older installs is left behind.

### Smaller download
- **adb and fastboot are no longer in the zip.** On first run they're downloaded from Google: a fixed version
  (platform-tools r37.0.1), checked against its SHA-256.
- The recovery sideload job is gone, since Google apps are now baked in.

### Tested
- Six full wipe-and-flash runs on a real Nothing Phone (3) before release: Core, Basic, Omni, Stock, Omni with
  Customize + Root, and MindTheGapps + Root. Each one booted with every expected Google app present, the replaced
  ROM apps gone and no app crashes.
- Automated tests for the image builder (exact read-back, links, large folders, big files), the package
  catalog, root-image checks and the keep-data rule.

### Tips
- After a wipe you'll need to tap **Allow** for USB debugging again.

SHA-256 of the zip: `[fill in]`
