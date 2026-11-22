# MetroidFlasher

Windows flasher for the **Nothing Phone (3)** (`metroid`). It works from the plain bootloader screen: no
fastbootd or working recovery needed, which are exactly the parts that break when this phone gets stuck.

## Download

**[MetroidFlasher 1.0 (zip, Windows, 12 MB)](https://github.com/AtoZ0to90to9AtoZ/MetroidFlasher-releases/releases/download/v1.0/MetroidFlasher-1.0.zip)**

SHA-256 (tool zip): `fc6a20f71eb81ebd13e3b756f48d59f76f9901b68325298e181b11e7ebc53576`

Release page: [v1.0](https://github.com/AtoZ0to90to9AtoZ/MetroidFlasher-releases/releases/tag/v1.0) ·
Support and reports: [XDA thread](https://xdaforums.com/t/tool-unbrick-autoflash-beta-metroidflasher-unbrick-custom-rom-gapps-from-the-plain-bootloader.4808789/)

## What it does

- **Unbrick** to stock Nothing OS
- **Install a custom ROM** (tested with crDroid 13.0, Android 17)
- **Install a ROM with Google apps built in**: the apps are baked into the ROM's images on your PC, then
  flashed in one go. No recovery step, nothing to tap on the phone.
  - Packages: **Core** (Play only), **Basic** (+ Dialer, Contacts, Messages, Clock), **Omni**, **Stock**
    (the most that fits) and **MindTheGapps**. All five were flashed and booted on a real phone.
  - **Customize**: untick apps you don't want. **Root (optional)**: bring your own patched init_boot/boot.
- Dry run first for every job. adb and fastboot are downloaded from Google on first run (pinned version,
  checksum checked).

Every job erases the phone unless keep data is allowed. Back up first. Needs an unlocked bootloader.

## Source

The source is in [`source/`](source) (Python, no outside packages) and as `MetroidFlasher-1.0-source.zip`
on the release page (SHA-256 `07e0cce9f9fbc92a50604897e206048c86941d49c7587a6aa3306869f5bcc212`). Run it with `py gui.py`; tests: `py tests/run_tests.py`.
It's **source-available** (see [LICENSE.txt](LICENSE.txt)): read it, build it, change it for your own use,
send fixes back. Please don't publish modified builds or present them as MetroidFlasher.

Not affiliated with Nothing Technology Limited.
