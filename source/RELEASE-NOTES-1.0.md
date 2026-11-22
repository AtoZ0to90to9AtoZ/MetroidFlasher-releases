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
- **Root (optional)**: bring your own pre-rooted `init_boot.img` / `boot.img` (Magisk, KernelSU, APatch) made from
  the same ROM. The dry run checks that it matches the ROM. The tool never patches anything itself.

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

### Known quirks
- Windows can lose the phone on USB after it reboots into Android. Unplug and replug the cable.
- After a wipe you'll need to tap **Allow** for USB debugging again.
- A harmless "Unexpected safety source: AdvancedProtection" background crash may show at first boot with newer
  Play Services.

SHA-256 of the zip: `[fill in]`
