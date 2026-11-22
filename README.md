# MetroidFlasher

Windows flasher for the **Nothing Phone (3)** (`metroid`). It works from the plain bootloader screen: no
fastbootd or working recovery needed, which are exactly the parts that break when this phone gets stuck.

## Download

**[MetroidFlasher 1.0.1 (zip, Windows, 12 MB)](https://github.com/AtoZ0to90to9AtoZ/MetroidFlasher-releases/releases/download/v1.0.1/MetroidFlasher-1.0.1.zip)**

SHA-256 (tool zip): `59f911efc36c5504da3003248f5afe069d85e935996faf91eab4873e714ddd12`

Release page: [v1.0.1](https://github.com/AtoZ0to90to9AtoZ/MetroidFlasher-releases/releases/tag/v1.0.1) ·
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
  checksum checked), unless you already have a recent adb + fastboot: then yours are used.

Every job erases the phone unless keep data is allowed. Back up first. Needs an unlocked bootloader.

## Source

The source is in [`source/`](source) (Python, no outside packages) and as `MetroidFlasher-1.0.1-source.zip`
on the release page (SHA-256 `c7ca95c7f6937e2da61eabfda1c370327780f7e9cbe81623abed4bb239f443c5`). Run it with `py gui.py`; tests: `py tests/run_tests.py`.
It's **source-available** (see [LICENSE.txt](LICENSE.txt)): read it, build it, change it for your own use,
send fixes back. Please don't publish modified builds or present them as MetroidFlasher.

Not affiliated with Nothing Technology Limited.
