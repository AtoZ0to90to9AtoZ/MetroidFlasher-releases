#!/usr/bin/env python3
r"""MetroidFlasher - start menu (this is what MetroidFlasher.exe runs).

Folder layout next to the exe:
  1-STOCK\    Nothing OS stock image folder (NothingFlasher .img files)
  2-ROM\      custom ROM: .img files
  3-GAPPS\    one GApps zip (NikGapps omni or MindTheGapps)
  4-BACKUPS\  reserved for backups
  tool\       platform-tools (downloaded from Google on first run)
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time

import appscan
import bakegapps
import debloatzip
import gapps
import metroid_flasher

APP_VERSION = "1.0.2"
TITLE = f"MetroidFlasher {APP_VERSION}  -  Nothing Phone (3)"

try:
    from release_manifest import TOOL_HASHES  # generated at build time
except ImportError:
    TOOL_HASHES = {}

# Set by gui.py: no keyboard prompts. The GUI asks its own questions (dry run or real, typed
# FLASH confirmation) before it calls a job, so the jobs must not wait for input().
GUI = False
GUI_DRY = True


# ----------------------------------------------------------------------------- paths

def root_dir():
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    for d in (here, os.path.dirname(here)):
        if os.path.isdir(os.path.join(d, "tool")) or os.path.isdir(os.path.join(d, "1-STOCK")):
            return d
    return here


ROOT = root_dir()
D_STOCK, D_ROM, D_GAPPS, D_BACK, D_TOOL = (os.path.join(ROOT, n) for n in
                                           ("1-STOCK", "2-ROM", "3-GAPPS", "4-BACKUPS", "tool"))
WORK = os.path.join(D_TOOL, "work")
EXE = ".exe" if os.name == "nt" else ""
FASTBOOT = os.path.join(D_TOOL, "platform-tools", "fastboot" + EXE)
ADB = os.path.join(D_TOOL, "platform-tools", "adb" + EXE)
DEFAULT_DIRS = {"stock": D_STOCK, "rom": D_ROM, "gapps": D_GAPPS}
FOLDERS_FILE = os.path.join(WORK, "folders.json")


def set_folder(kind, path):
    """Use another folder for stock / rom / gapps (remembered). An empty path goes back to the default."""
    global D_STOCK, D_ROM, D_GAPPS
    import json
    path = os.path.normpath(path) if path else DEFAULT_DIRS[kind]
    if kind == "stock":
        D_STOCK = path
    elif kind == "rom":
        D_ROM = path
    else:
        D_GAPPS = path
    chosen = {}
    try:
        chosen = json.load(open(FOLDERS_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if os.path.normcase(path) == os.path.normcase(DEFAULT_DIRS[kind]):
        chosen.pop(kind, None)
    else:
        chosen[kind] = path
    try:
        os.makedirs(WORK, exist_ok=True)
        json.dump(chosen, open(FOLDERS_FILE, "w", encoding="utf-8"), indent=1)
    except OSError:
        pass


def load_folders():
    import json
    try:
        chosen = json.load(open(FOLDERS_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return
    for kind, path in chosen.items():
        if kind in DEFAULT_DIRS and os.path.isdir(path):
            set_folder(kind, path)


load_folders()


# ----------------------------------------------------------------------------- small helpers

def say(msg=""):
    print(msg, flush=True)


def ask(prompt, choices):
    while True:
        a = input(f"{prompt} ").strip().lower()
        if a in choices:
            return a
        say(f"  Please type one of: {', '.join(choices)}")


def pause():
    if GUI:
        return
    input("\nPress Enter to return to the menu...")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def find_images(folder):
    """The folder itself or its first subfolder that holds .img files."""
    if not os.path.isdir(folder):
        return None
    if any(f.lower().endswith(".img") for f in os.listdir(folder)):
        return folder
    for dp, _dn, fn in os.walk(folder):
        if any(f.lower().endswith(".img") for f in fn):
            return dp
    return None


def find_zips(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".zip"))


# ----------------------------------------------------------------------------- integrity

# Google's platform-tools are NOT shipped: they are downloaded from Google on first run, pinned to one
# version and checked by SHA-256 (same idea as Spike's flash_all, but pinned so a new adb/fastboot can't
# change behaviour silently). If the pinned link ever dies, the latest version is used, with a warning.
PT_URL = "https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip"
PT_SHA256 = "45f4d63113e895ebde0c90f194099a4676b6ac653bd28d54314a9e022bbc1a99"
PT_LATEST = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"


# Most people already have platform-tools. If theirs is new enough it's used and nothing is downloaded (one adb
# version on the PC also means no "adb server version doesn't match" fights). Older fastboot builds have known
# trouble with big partitions, so anything older than MIN_FASTBOOT is skipped and Google's copy is downloaded.
MIN_FASTBOOT = (34, 0, 0)
OWN_PT_FILE = os.path.join(WORK, "platform-tools-path.txt")


def use_platform_tools(folder):
    global FASTBOOT, ADB
    FASTBOOT = os.path.join(folder, "fastboot" + EXE)
    ADB = os.path.join(folder, "adb" + EXE)


def fastboot_version(fastboot):
    """(major, minor, patch) from 'fastboot --version', or None."""
    try:
        out = subprocess.run([fastboot, "--version"], capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        m = re.search(r"fastboot version (\d+)\.(\d+)\.(\d+)", out)
        return tuple(int(x) for x in m.groups()) if m else None
    except Exception:  # noqa: BLE001
        return None


def check_platform_tools(folder):
    """'' when folder holds a usable adb + fastboot, else the reason it can't be used."""
    fb, adb = os.path.join(folder, "fastboot" + EXE), os.path.join(folder, "adb" + EXE)
    if not (os.path.isfile(fb) and os.path.isfile(adb)):
        return "no adb.exe + fastboot.exe in that folder"
    v = fastboot_version(fb)
    if v is None:
        return "fastboot there doesn't run"
    if v < MIN_FASTBOOT:
        return f"fastboot {'.'.join(map(str, v))} is too old (needs {'.'.join(map(str, MIN_FASTBOOT))} or newer)"
    return ""


def own_platform_tools_candidates():
    import shutil
    out = []
    try:
        out.append(open(OWN_PT_FILE, encoding="utf-8").read().strip())
    except OSError:
        pass
    w = shutil.which("fastboot")
    if w:
        out.append(os.path.dirname(os.path.realpath(w)))
    env = os.environ
    for base in (env.get("ANDROID_HOME"), env.get("ANDROID_SDK_ROOT"),
                 os.path.join(env.get("LOCALAPPDATA", ""), "Android", "Sdk")):
        if base:
            out.append(os.path.join(base, "platform-tools"))
    out += [r"C:\platform-tools", r"C:\Android\platform-tools", r"C:\adb",
            os.path.join(env.get("USERPROFILE", ""), "platform-tools"),
            os.path.join(env.get("USERPROFILE", ""), "Downloads", "platform-tools")]
    seen, res = set(), []
    for d in out:
        k = os.path.normcase(os.path.abspath(d)) if d else ""
        if k and k not in seen:
            seen.add(k)
            res.append(d)
    return res


def set_own_platform_tools(folder):
    """Use (and remember) the user's own platform-tools folder. Returns '' or the reason it can't be used."""
    why = check_platform_tools(folder)
    if why:
        return why
    use_platform_tools(folder)
    os.makedirs(WORK, exist_ok=True)
    with open(OWN_PT_FILE, "w", encoding="utf-8") as f:
        f.write(folder)
    return ""


def ensure_platform_tools(log=None):
    """Make sure there's a usable fastboot + adb: tool\\platform-tools, else the user's own (PATH, SDK, the usual
    folders), else Google's download. Returns True when they are there."""
    log = log or say
    if os.path.isfile(FASTBOOT) and os.path.isfile(ADB):
        return True
    for d in own_platform_tools_candidates():
        if os.path.isdir(d) and not check_platform_tools(d):
            use_platform_tools(d)
            v = fastboot_version(FASTBOOT)
            log(f"Using your own platform-tools: {d} (fastboot {'.'.join(map(str, v))}). Nothing to download.")
            return True
    use_platform_tools(os.path.join(D_TOOL, "platform-tools"))
    import shutil
    import tempfile
    import urllib.request
    import zipfile
    dest = os.path.dirname(FASTBOOT)
    os.makedirs(D_TOOL, exist_ok=True)
    for url, want in ((PT_URL, PT_SHA256), (PT_LATEST, None)):
        try:
            log(f"Downloading Google's platform-tools (adb + fastboot) from {url} ...")
            fd, tmp = tempfile.mkstemp(suffix=".zip", dir=D_TOOL)
            os.close(fd)
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            got = sha256(tmp)
            if want and got != want:
                log(f"!! Downloaded file does not match the expected checksum - not used.")
                os.remove(tmp)
                continue
            if not want:
                log(f"!! Using Google's LATEST platform-tools (the pinned version wasn't available). "
                    f"sha256 {got}. It should work; report any fastboot trouble.")
            with zipfile.ZipFile(tmp) as z:
                if any(n.startswith("/") or ".." in n.split("/") for n in z.namelist()):
                    raise ValueError("unexpected paths in the zip")
                z.extractall(D_TOOL)          # the zip holds platform-tools/...
            os.remove(tmp)
            if os.path.isfile(FASTBOOT):
                log(f"   platform-tools ready in {dest}")
                return True
        except Exception as e:  # noqa: BLE001 - try the next source, then give up with a clear message
            log(f"   download failed: {e}")
            try:
                os.remove(tmp)
            except Exception:  # noqa: BLE001
                pass
    log("!! Could not get platform-tools. Check the internet connection, or unzip Google's platform-tools "
        f"yourself into {dest}")
    return False


def check_tool_integrity():
    """Refuse to flash with modified or missing helper programs (our own files; platform-tools are
    Google's, downloaded and checked by ensure_platform_tools)."""
    if not TOOL_HASHES:
        return True
    bad = []
    for rel, want in TOOL_HASHES.items():
        if rel.lower().startswith("platform-tools"):
            continue
        p = os.path.join(D_TOOL, rel)
        if not os.path.isfile(p) or sha256(p) != want:
            bad.append(rel)
    if bad:
        say("\n!! The tool folder has been changed or is incomplete:")
        for b in bad:
            say(f"   - tool\\{b}")
        say("!! For your phone's safety MetroidFlasher will not flash with a modified copy.")
        say("!! Download a fresh copy from the official release page.")
        return False
    return True


def hash_check_menu():
    say("\n== File check (SHA-256) ==")
    say("If a folder has a .sha256 / .sha256sum file from the download page, files are compared to it.\n")
    for folder in (D_STOCK, D_ROM, D_GAPPS):
        if not os.path.isdir(folder):
            continue
        sums = {}
        for dp, _dn, fn in os.walk(folder):
            for f in fn:
                if f.lower().endswith((".sha256", ".sha256sum")):
                    for line in open(os.path.join(dp, f), encoding="utf-8", errors="replace"):
                        parts = line.split()
                        if len(parts) >= 2 and len(parts[0]) == 64:
                            sums[os.path.basename(parts[-1].lstrip("*"))] = parts[0].lower()
        for dp, _dn, fn in os.walk(folder):
            for f in sorted(fn):
                if not f.lower().endswith((".zip", ".img")):
                    continue
                h = sha256(os.path.join(dp, f))
                verdict = ("MATCH" if sums[f] == h else "MISMATCH - re-download!") if f in sums else ""
                say(f"  {os.path.relpath(os.path.join(dp, f), ROOT)}\n      {h}  {verdict}")
    pause()


# ----------------------------------------------------------------------------- phone helpers

def locked_serial():
    """The phone metroid_flasher locked onto (it sets ANDROID_SERIAL, so adb/fastboot can't reach any other)."""
    return os.environ.get("ANDROID_SERIAL", "")


def phone_state():
    """'fastboot', 'sideload', 'recovery', 'device', ... for the LOCKED phone only, else ''."""
    serial = locked_serial()
    if not serial:
        return ""
    out = run([FASTBOOT, "devices"], 15).stdout
    if any(line.split()[:1] == [serial] for line in out.splitlines()):
        return "fastboot"
    for line in run([ADB, "devices"], 15).stdout.splitlines()[1:]:
        p = line.split()
        if len(p) >= 2 and p[0] == serial:
            return p[1]
    return ""


def fastboot_devices():
    try:
        return phone_state() == "fastboot"
    except Exception:
        return False


def wait_for(state, what, seconds=900, leave=False):
    """Wait until the locked phone is in `state` (or, with leave=True, until it is no longer in it)."""
    say(f"   waiting for the phone ({what})...")
    end = time.time() + seconds
    while time.time() < end:
        try:
            now = phone_state()
        except Exception:
            now = ""
        if (now != state) if leave else (now == state):
            return True
        time.sleep(2)
    return False


def sideload(zip_path, label):
    """Returns adb's exit code. Output goes to the screen AND the log, so nothing happens unrecorded."""
    say(f"\n>> ON THE PHONE: Apply update -> Apply from ADB   (for: {label})")
    if not wait_for("sideload", "sideload mode"):
        raise metroid_flasher.FlashError("The phone never entered 'Apply from ADB'.")
    say(f"   sending {os.path.basename(zip_path)} ...")
    r = subprocess.run([ADB, "sideload", zip_path], capture_output=True, text=True, errors="replace")
    for line in (r.stdout + r.stderr).splitlines():
        if line.strip():
            say("   " + line.strip())
    say(f"   adb sideload exit code: {r.returncode}")
    # recovery stays in 'sideload' for a moment after the transfer; wait for it to leave, so the next
    # sideload waits for the user to tap 'Apply from ADB' again instead of firing early
    wait_for("sideload", "recovery to finish the install", seconds=180, leave=True)
    return r.returncode


EXTRA_FLASH_ARGS = []   # set by the GUI, e.g. ["--both-slots"]


def flasher(args):
    """Run the flasher engine with its normal options; returns its exit code."""
    argv = ["metroid_flasher", *args, *EXTRA_FLASH_ARGS, "--fastboot", FASTBOOT, "--work", WORK]
    if GUI and "--dry-run" not in args and "--yes" not in args:
        argv.append("--yes")  # the GUI already got a typed FLASH confirmation
    old = sys.argv
    sys.argv = argv
    try:
        return metroid_flasher.main()
    finally:
        sys.argv = old


def ask_dry_run():
    if GUI:
        return GUI_DRY
    return ask("Do a DRY RUN first (checks everything, flashes nothing)? [y/n]", ("y", "n")) == "y"


def warnings_block(lines):
    say("\n" + "!" * 70)
    for l in lines:
        say("!  " + l)
    say("!" * 70)


# ----------------------------------------------------------------------------- the three jobs

def job_unbrick():
    src = find_images(D_STOCK)
    if not src:
        say("\nPut the Nothing OS stock image folder (the .img files) into 1-STOCK first.")
        return pause()
    warnings_block(["UNBRICK: installs stock Nothing OS from the bootloader.",
                    "ERASES everything on the phone. Back up first.",
                    "Needs bootloader AND critical partitions unlocked.",
                    "Keep the USB cable connected until it says DONE."])
    dry = ask_dry_run()
    flasher(["stock", src, "--dry-run"] if dry else ["stock", src])
    pause()


def rom_source():
    # Beta: image folders only (ROM .zip extraction is not proven on a phone yet).
    return find_images(D_ROM)


def job_custom_rom():
    src = rom_source()
    if not src:
        say("\nPut ONE custom ROM into 2-ROM as .img files (ROM .zip support comes in the next version).")
        return pause()
    warnings_block(["CUSTOM ROM: installs the ROM in 2-ROM from the bootloader.",
                    "ERASES everything on the phone. Back up first.",
                    "Your firmware stays as it is: use firmware the ROM asks for.",
                    "Keep the USB cable connected until it says DONE."])
    dry = ask_dry_run()
    flasher(["rom", src, *root_args(), "--dry-run"] if dry else ["rom", src, *root_args()])
    pause()


# GApps are BAKED into the ROM's images on this PC (1.0): no recovery, no sideloads, no install scripts.
# Packages: Core, Basic, Omni, Stock (Stock is the maximum), MindTheGapps. The zips go in 3-GAPPS (found by checksum).
GUI_GAPPS = "omni"          # set by gui.py
GUI_CUSTOMIZE = []          # app packages to leave out (Customize)
GUI_ROOT_IMAGE = ""         # optional pre-rooted init_boot/boot made from this ROM


def available_gapps():
    """{package: True/False} - which packages the zips in 3-GAPPS can build (cached by name/size/date)."""
    zips = gapps.find_zips_cached(D_GAPPS, os.path.join(WORK, "gapps-cache.json"))
    return {p: all(k in zips for k, _ in gapps.PACKAGES[p]) for p in gapps.ORDER}, zips


def package_apps(package, zips):
    """[(app package name, locked)] in a GApps package - for the Customize list."""
    out = []
    for key, only in gapps.PACKAGES[package]:
        for name in sorted(bakegapps.read_packages(zips[key], only)):
            out.append((name, name in gapps.LOCKED))
    return out


def root_args():
    return ["--root-image", GUI_ROOT_IMAGE] if GUI_ROOT_IMAGE else []


def job_rom_gapps():
    rom = find_images(D_ROM)
    if not rom:
        say("ROM + GApps needs the ROM as .img files in 2-ROM.")
        return pause()
    have, _ = available_gapps()
    if not have.get(GUI_GAPPS):
        say(f"3-GAPPS doesn't hold the zip(s) for '{GUI_GAPPS}'. See the list under the button.")
        return pause()
    warnings_block([f"ROM + GAPPS: the ROM with Google apps ({gapps.NAMES.get(GUI_GAPPS, GUI_GAPPS)}) built into it, in ONE flash.",
                    "ERASES everything on the phone. Back up first.",
                    "No recovery steps and nothing to tap: the apps are baked in on this PC.",
                    "Keep the USB cable connected until it says DONE."])
    args = ["rom", rom, "--gapps", GUI_GAPPS, "--gapps-dir", D_GAPPS]
    if GUI_CUSTOMIZE:
        args += ["--customize-remove", ",".join(GUI_CUSTOMIZE)]
    args += root_args()
    dry = ask_dry_run()
    flasher(args + ["--dry-run"] if dry else args)
    pause()


# ----------------------------------------------------------------------------- menu

def ensure_layout():
    for d in (D_STOCK, D_ROM, D_GAPPS, D_BACK, WORK):
        os.makedirs(d, exist_ok=True)


def main():
    ensure_layout()
    ensure_platform_tools()
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        say("=" * 70)
        say(f"  {TITLE}")
        say("  Only for the Nothing Phone (3) 'metroid'. You flash at your own risk.")
        say("=" * 70)
        say("  1  Unbrick / install stock Nothing OS      (uses 1-STOCK)")
        say("  2  Flash a custom ROM                       (uses 2-ROM)")
        say("  3  Flash ROM + GApps                         (uses 2-ROM + 3-GAPPS)")
        say("  4  Check my downloaded files (SHA-256)")
        say("  Q  Quit")
        c = ask("\nChoose:", ("1", "2", "3", "4", "q"))
        if c == "q":
            return 0
        if c == "4":
            hash_check_menu()
            continue
        if not check_tool_integrity():
            pause()
            continue
        if not os.path.isfile(FASTBOOT):
            say("\nfastboot is missing from tool\\platform-tools. Download a fresh copy.")
            pause()
            continue
        {"1": job_unbrick, "2": job_custom_rom, "3": job_rom_gapps}[c]()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\nBye.")
