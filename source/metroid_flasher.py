#!/usr/bin/env python3
"""MetroidFlasher - bootloader-only flasher for the Nothing Phone 3 (codename "metroid", product "sun").

Installs stock Nothing OS (unbrick) or any custom ROM using ONLY the regular bootloader
fastboot mode. It never needs fastbootd or a working recovery: the system partitions are
packed on the PC into ONE super.img (verified byte for byte before anything is sent) and
written with a plain `fastboot flash super`. Every command is pinned to one phone.

  python metroid_flasher.py stock <NothingFlasher folder>     unbrick to stock
  python metroid_flasher.py rom   <rom.zip | images folder>   install a custom ROM
  add --dry-run to check everything without touching the phone.

See README.md before using it.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lpsuper  # noqa: E402

TOOL_VERSION = "1.1.2"
EXPECTED_PRODUCT = "sun"
DEFAULT_SUPER_SIZE = 0x240000000
GROUP = "qti_dynamic_partitions"

LOGICAL = ["system", "system_ext", "product", "vendor", "odm", "vendor_dlkm", "system_dlkm", "odm_dlkm"]
BOOT_SET = ["boot", "init_boot", "vendor_boot", "vendor_kernel_boot", "dtbo", "recovery", "pvmfw"]
VBMETA = ["vbmeta", "vbmeta_system", "vbmeta_vendor"]
FIRMWARE = [
    "abl", "aop", "aop_config", "bluetooth", "cpucp", "cpucp_dtb", "devcfg", "dsp", "featenabler",
    "hyp", "imagefv", "keymaster", "modem", "multiimgoem", "multiimgqti", "qupfw", "shrm",
    "soccp_dcd", "soccp_debug", "tz", "uefi", "uefisecapp", "xbl", "xbl_config", "xbl_ramdump",
]
# Device-unique or security partitions. This tool never writes these, whatever is in the input.
NEVER_TOUCH = {
    "persist", "modemst1", "modemst2", "fsg", "fsc", "devinfo", "frp", "keystore", "secdata",
    "storsec", "uefivarstore", "ddr", "cdt", "spunvm", "vm-persist", "qweslicstore", "rawdump",
    "logfs", "apdp", "apdpb", "dpm", "binfo", "ssd", "toolsfv", "tzsc", "connsec", "oem", "qmcs",
}
CRITICAL_HINT = (
    "The bootloader refused a critical partition. Unlock critical partitions first:\n"
    "    fastboot flashing unlock_critical\n"
    "(confirm on the phone; this WIPES the phone), or rerun with --skip-firmware."
)


class FlashError(Exception):
    pass


# --------------------------------------------------------------------------- utilities

def log(msg=""):
    print(msg, flush=True)
    if LOGFILE:
        LOGFILE.write(msg + "\n")
        LOGFILE.flush()


LOGFILE = None


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def find_exe(name, hints):
    for d in hints:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return os.path.abspath(p)
    return shutil.which(name)


def free_bytes(path):
    return shutil.disk_usage(path).free


# --------------------------------------------------------------------------- image access

SPARSE_MAGIC = 0xED26FF3A


class Image:
    """A partition image, raw or Android-sparse, readable as a raw byte range."""

    def __init__(self, name, path):
        self.name = name
        self.path = path
        with open(path, "rb") as f:
            head = f.read(28)
        self.sparse = len(head) == 28 and struct.unpack_from("<I", head, 0)[0] == SPARSE_MAGIC
        if self.sparse:
            self._index_sparse()
        else:
            self.size = os.path.getsize(path)

    def _index_sparse(self):
        self.chunks = []  # (raw_off, raw_len, kind, file_off, fill4)
        with open(self.path, "rb") as f:
            _, _, _, fh, ch, blk, tot, nch, _ = struct.unpack("<IHHHHIIII", f.read(28))
            f.seek(fh)
            raw = 0
            for _ in range(nch):
                hdr = f.read(ch)
                kind, _, blocks, total = struct.unpack_from("<HHII", hdr, 0)
                data_off = f.tell()
                length = blocks * blk
                if kind == 0xCAC1:
                    self.chunks.append((raw, length, "raw", data_off, None))
                elif kind == 0xCAC2:
                    self.chunks.append((raw, length, "fill", None, f.read(4)))
                elif kind == 0xCAC3:
                    self.chunks.append((raw, length, "zero", None, None))
                elif kind != 0xCAC4:
                    raise FlashError(f"{self.path}: unknown sparse chunk type {hex(kind)}")
                f.seek(data_off + total - ch)
                raw += length
            self.size = tot * blk

    def reader(self, src_off, length, bufsize=16 * 1024 * 1024):
        if not self.sparse:
            return lpsuper.file_reader(self.path, src_off, length, bufsize)

        def gen():
            end = src_off + length
            with open(self.path, "rb") as f:
                for raw, clen, kind, foff, fill in self.chunks:
                    lo, hi = max(raw, src_off), min(raw + clen, end)
                    if lo >= hi:
                        continue
                    pos = lo
                    while pos < hi:
                        n = min(bufsize, hi - pos)
                        if kind == "raw":
                            f.seek(foff + (pos - raw))
                            buf = f.read(n)
                        elif kind == "fill":
                            ph = (pos - raw) % 4  # keep the 4-byte pattern in phase
                            buf = (fill * (n // 4 + 2))[ph:ph + n]
                        else:
                            buf = bytes(n)
                        pos += n
                        yield buf
        return gen


# --------------------------------------------------------------------------- fastboot wrapper

def has_fastboot_device(devices_output):
    """True if `fastboot devices` lists a phone. Real fastboot prints 'SERIAL<TAB> fastboot'
    (tab, then a space), so match any whitespace between serial and state."""
    return any(re.match(r"^\S+\s+fastboot\s*$", line) for line in devices_output.splitlines())


def is_network_serial(serial):
    """Wireless/TCP connections (ip:port, mDNS _adb-tls-connect names). A flasher only ever uses the cable."""
    return ":" in serial or "_adb-tls" in serial or "._tcp" in serial


class Fastboot:
    """Every command is pinned to ONE phone (-s SERIAL). Once a phone is locked, ANDROID_SERIAL is also set
    for this process, so any adb/fastboot started afterwards (metroid.py's sideloads and reboots included)
    can only reach that phone - never a second phone on USB or wireless adb."""

    def __init__(self, exe, dry=False):
        self.exe = exe
        self.dry = dry
        self.serial = None

    def find_phones(self):
        """[(serial, product)] for every phone in bootloader fastboot over USB."""
        out = subprocess.run([self.exe, "devices"], capture_output=True, text=True, errors="replace").stdout
        phones = []
        for line in out.splitlines():
            m = re.match(r"^(\S+)\s+fastboot\s*$", line)
            if not m or is_network_serial(m.group(1)):
                continue
            r = subprocess.run([self.exe, "-s", m.group(1), "getvar", "product"],
                               capture_output=True, text=True, errors="replace", timeout=30)
            p = re.search(r"^product:\s*(\S*)", r.stdout + r.stderr, re.M)
            phones.append((m.group(1), p.group(1) if p else ""))
        return phones

    def lock(self, serial):
        self.serial = serial
        os.environ["ANDROID_SERIAL"] = serial

    def run(self, *args, check=True, echo=True, timeout=None):
        cmd = [self.exe, *(["-s", self.serial] if self.serial else []), *args]
        if echo:
            log("  > fastboot " + " ".join(a if " " not in a else f'"{a}"' for a in args))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
        out = []
        start = time.time()
        for line in proc.stdout:
            line = line.rstrip()
            out.append(line)
            if echo and line:
                log("    " + line)
            if timeout and time.time() - start > timeout:
                proc.kill()
                raise FlashError(f"fastboot {' '.join(args)} timed out")
        proc.wait()
        text = "\n".join(out)
        if check and (proc.returncode != 0 or "FAILED" in text):
            if "Critical Partitions" in text:
                raise FlashError(f"fastboot {' '.join(args)} failed.\n{CRITICAL_HINT}")
            raise FlashError(f"fastboot {' '.join(args)} failed:\n{text}")
        return text

    def getvar(self, name):
        text = self.run("getvar", name, check=False, echo=False, timeout=20)
        m = re.search(rf"^{re.escape(name)}:\s*(.*)$", text, re.M)
        return m.group(1).strip() if m else ""

    def wait_for_bootloader(self, seconds=180, want_serial=None, any_product=False):
        """Wait for exactly one Nothing Phone 3 in the bootloader and lock onto it."""
        log("Waiting for the phone in fastboot (bootloader) mode...")
        t0 = time.time()
        told = set()
        while time.time() - t0 < seconds:
            phones = self.find_phones()
            ours = [(s, p) for s, p in phones
                    if (p == EXPECTED_PRODUCT or any_product) and (not want_serial or s == want_serial)]
            for s, p in phones:
                if (s, p) not in ours and s not in told:
                    log(f"  ignoring {s} (product '{p or '?'}'): not the phone this run is for")
                    told.add(s)
            if len(ours) > 1:
                raise FlashError("More than one Nothing Phone 3 is in fastboot: "
                                 + ", ".join(s for s, _ in ours)
                                 + ".\nUnplug the others, or pick one with --serial SERIAL.")
            if ours:
                self.lock(ours[0][0])
                log(f"Locked onto {self.serial}: every command in this run goes to this phone only.")
                return
            time.sleep(2)
        raise FlashError("No Nothing Phone 3 in fastboot mode. Hold Power + Volume Down until the fastboot "
                         "screen shows" + (f" (looking for serial {want_serial})" if want_serial else "") + ".")


# --------------------------------------------------------------------------- input collection

def collect_dir(folder):
    images = {}
    for fn in os.listdir(folder):
        if fn.lower().endswith(".img"):
            images[fn[:-4]] = os.path.join(folder, fn)
    return images


def payload_partitions(dumper, zip_path):
    r = subprocess.run([dumper, "-l", zip_path], capture_output=True, text=True, errors="replace")
    text = r.stdout
    m = re.search(r"Found partitions:\s*(.+)", text, re.S)
    if not m:
        detail = (r.stdout + "\n" + r.stderr).strip() or "(the extractor printed nothing)"
        raise FlashError(f"Could not read the ROM's partition list from {zip_path}:\n{detail}\n"
                         "Is this a full ROM zip with payload.bin inside?")
    parts = {}
    for name, num, unit in re.findall(r"([\w-]+) \(([\d.]+) (\w+)\)", m.group(1)):
        mult = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9}.get(unit, 1e9)
        parts[name] = int(float(num) * mult)
    return parts


def sha256_file(path, bufsize=16 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(bufsize), b""):
            h.update(b)
    return h.hexdigest()


def check_cache(out, zip_sha, wanted):
    """Keep an extracted image only if it was extracted from THIS zip and is unchanged since.
    work/extracted/manifest.json records the zip's sha256 and each image's sha256 at extraction time.
    (2026-11-20: images left over from an experiment were silently reused and flashed.)"""
    mpath = os.path.join(out, "manifest.json")
    try:
        man = json.load(open(mpath, encoding="utf-8"))
    except (OSError, ValueError):
        man = {}
    if man.get("zip_sha256") != zip_sha:
        man = {"zip_sha256": zip_sha, "images": {}}
    for name in wanted:
        p = os.path.join(out, name + ".img")
        if not os.path.isfile(p):
            man["images"].pop(name, None)
            continue
        want = man["images"].get(name)
        if want and sha256_file(p) == want:
            continue
        log(f"  {name}.img in the work folder is not a verified copy from this ROM zip - extracting it again")
        os.remove(p)
        man["images"].pop(name, None)
    return man, mpath


def collect_zip(zip_path, workdir, dumper):
    if not dumper:
        raise FlashError("A ROM .zip needs payload-dumper-go.exe next to this tool or in the parent folder.")
    parts = payload_partitions(dumper, zip_path)
    log(f"ROM contains: {', '.join(sorted(parts))}")
    out = os.path.join(workdir, "extracted")
    os.makedirs(out, exist_ok=True)
    log("Checking the ROM zip and any images already extracted from it (SHA-256)...")
    zip_sha = sha256_file(zip_path)
    log(f"  ROM zip sha256 {zip_sha}")
    man, mpath = check_cache(out, zip_sha, [p for p in parts if p not in NEVER_TOUCH])
    need = sum(parts.values()) * 1.05
    have = free_bytes(workdir)
    already = sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out) if f.endswith(".img"))
    if need - already > have:
        raise FlashError(
            f"Extracting needs about {human(need)} free in {workdir}, only {human(have)} available.\n"
            "Use --work to point at a bigger drive, or extract the zip yourself and pass the folder."
        )
    wanted = [p for p in parts if p not in NEVER_TOUCH]
    missing = [p for p in wanted if not os.path.isfile(os.path.join(out, p + ".img"))]
    if missing:
        log(f"Extracting {len(missing)} images from the ROM into {out} ...")
        r = subprocess.run([dumper, "-c", "4", "-p", ",".join(missing), "-o", out, zip_path],
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            raise FlashError(f"payload-dumper-go failed:\n{r.stdout}\n{r.stderr}")
        for name in missing:
            p = os.path.join(out, name + ".img")
            if os.path.isfile(p):
                man["images"][name] = sha256_file(p)
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(man, f, indent=1)
    else:
        log("  all extracted images verified against this ROM zip")
    images = collect_dir(out)
    strays = sorted(n for n in images if n not in parts)
    if strays:   # an .img that isn't in this ROM at all must never ride along
        raise FlashError(f"The work folder {out} holds images that are not in this ROM: "
                         f"{', '.join(strays)}. Delete them and run again.")
    return images


def classify(images, mode, skip_firmware):
    plan = {"firmware": [], "boot": [], "logical": [], "ignored": []}
    for name in sorted(images):
        if name in NEVER_TOUCH:
            plan["ignored"].append((name, "device-unique / never flashed by this tool"))
        elif name in LOGICAL:
            plan["logical"].append(name)
        elif name in BOOT_SET or name in VBMETA:
            plan["boot"].append(name)
        elif name in FIRMWARE:
            if skip_firmware:
                plan["ignored"].append((name, "firmware skipped (--skip-firmware)"))
            else:
                plan["firmware"].append(name)
        else:
            plan["ignored"].append((name, "not a partition this tool knows"))
    plan["logical"].sort(key=LOGICAL.index)
    plan["boot"].sort(key=lambda n: (n in VBMETA, n))
    if mode == "stock":
        need = {"boot", "vendor_boot", "init_boot", "vbmeta", "system", "vendor"}
        missing = sorted(need - set(images))
        if missing:
            raise FlashError(f"This doesn't look like a full stock image folder (missing: {', '.join(missing)})")
    if not plan["logical"]:
        raise FlashError("No system images found (system.img, vendor.img, ...).")
    if "system" not in plan["logical"]:
        raise FlashError("system.img is missing.")
    return plan


# What every real image of each kind starts with (checked on the crDroid 13.0 and stock B4.1 images,
# 2026-11-23). A bad download or a failed payload extraction gets caught here, on the PC, before
# anything is written to the phone (a bad vbmeta.img used to be found only after super was flashed).
MAGIC = {
    "boot": b"ANDROID!", "init_boot": b"ANDROID!", "recovery": b"ANDROID!", "pvmfw": b"ANDROID!",
    "vendor_boot": b"VNDRBOOT", "dtbo": bytes.fromhex("d7b7ab1e"),
    "vbmeta": b"AVB0", "vbmeta_system": b"AVB0", "vbmeta_vendor": b"AVB0",
}


def check_headers(plan, paths):
    """Stop before touching the phone if a boot or vbmeta image isn't what its name says."""
    bad = []
    for name in plan["boot"]:
        want = MAGIC.get(name)
        if not want:
            continue
        p = paths[name]
        size = os.path.getsize(p)
        with open(p, "rb") as f:
            head = f.read(len(want))
        if head != want:
            what = "it's empty" if size == 0 else (
                "it's all zeros at the start" if not head.strip(b"\0") else f"it starts with {head.hex()}")
            bad.append(f"  {os.path.basename(p)} ({size:,} bytes): not a real {name} image, {what} "
                       f"(a real one starts with {want!r})")
    if bad:
        raise FlashError("These images are damaged or the wrong files. Nothing was flashed:\n" + "\n".join(bad) +
                         "\nExtract them again from the same ROM / firmware zip (a failed payload.bin "
                         "extraction or a partial download usually causes this), then run again.")


# --------------------------------------------------------------------------- GApps bake, own root image

def install_record(args, paths):
    """What decides whether keeping data is safe: the ROM's system image and the GApps choice."""
    sysimg = paths.get("system")
    return {"rom": [os.path.basename(sysimg), os.path.getsize(sysimg)] if sysimg else None,
            "gapps": args.gapps or "none",
            "customize": sorted(filter(None, (x.strip() for x in (args.customize_remove or "").split(","))))}


def remember_install(workdir, args, paths):
    rec = install_record(args, getattr(args, "_rom_paths", paths))
    with open(os.path.join(workdir, "last-install.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)


def bake_gapps(paths, args, workdir, here, parent):
    """Bake the chosen GApps package into system/system_ext/product on this PC (pure Python, no WSL).
    Returns paths with the baked images swapped in. Turns verity off (the images changed)."""
    import bakewin
    import gapps
    if args.mode != "rom":
        raise FlashError("--gapps is for custom ROMs (rom mode).")
    args._rom_paths = dict(paths)
    if args.keep_data:   # keeping data is only safe when nothing about the install changed (Gboard rename crash)
        last = os.path.join(workdir, "last-install.json")
        prev = json.load(open(last, encoding="utf-8")) if os.path.isfile(last) else None
        if prev != install_record(args, paths):
            raise FlashError("Keep data only works when the ROM and the GApps choice are exactly the same as "
                             "the last install. This is a different install, so the phone must be wiped "
                             "(run again without --keep-data).")
    gdir = args.gapps_dir or os.path.join(parent, "3-GAPPS")
    if not os.path.isdir(gdir):
        raise FlashError(f"GApps folder not found: {gdir}. Put the NikGapps / MindTheGapps zips there or pass --gapps-dir.")
    log(f"Looking for GApps zips in {gdir} (checked by SHA-256) ...")
    scan = gapps.scan_zips(gdir, os.path.join(workdir, "gapps-scan.json"))
    zips = gapps.zips_from_scan(scan)
    for f, used, why in gapps.zip_verdicts(scan, args.gapps):
        log(f"  {'USED    ' if used else '        '} {f}" + ("" if used else f"  - {why}"))
    try:
        specs = gapps.specs_for(args.gapps, zips)
        props = bakewin.build_props(paths["system"])
        rom_key, removals = gapps.rom_map(props, args.gapps)
    except (ValueError, FileNotFoundError, LookupError, KeyError) as e:
        raise FlashError(str(e))
    log(f"GApps: {args.gapps} on {gapps.ROMS[rom_key]['name']}")
    parts = {p: paths[p] for p in ("system", "system_ext", "product") if p in paths}
    out = os.path.join(workdir, "baked")
    need = sum(os.path.getsize(v) for v in parts.values()) + 3 * 2**30
    if free_bytes(workdir) < need:
        raise FlashError(f"Baking needs about {human(need)} free in {workdir}. Use --work on a bigger drive.")
    try:
        extras = []
        if getattr(args, "dev_adb_key", None):
            # Android reads preloaded trusted keys from /product/etc/security/adb_keys (adb_keys_file label)
            extras.append(("product", "/etc/security/adb_keys", open(args.dev_adb_key, "rb").read(), 0o644,
                           "u:object_r:adb_keys_file:s0"))
        baked = bakewin.bake(specs, removals, parts, out, args.customize_remove, log=log, extras=extras)
    except SystemExit as e:
        raise FlashError(f"GApps bake stopped: {e}")
    new = dict(paths)
    new.update(baked)
    args.disable_verity = True
    return new


def boot_header(path):
    """(kind, os_version, patch) from a boot/init_boot image header: kind 'boot' when it holds a kernel."""
    with open(path, "rb") as f:
        h = f.read(48)
    if h[:8] != b"ANDROID!":
        raise FlashError(f"{path} is not an Android boot image")
    kernel_size, _ramdisk, osv = struct.unpack_from("<III", h, 8)
    v, pl = osv >> 11, osv & 0x7FF
    return ("boot" if kernel_size else "init_boot"), f"{v >> 14}.{(v >> 7) & 0x7F}", f"{(pl >> 4) + 2000}-{pl & 0xF:02d}"


def swap_root_image(paths, root):
    """Your own pre-rooted image replaces the ROM's boot or init_boot (decided from its header: Magisk and
    KernelSU LKM patch init_boot; APatch and KernelSU GKI patch boot). It must come from THIS ROM: the
    Android version and security patch must match the ROM's own image. The tool never patches anything."""
    root = os.path.abspath(root.strip().strip('"'))
    if not os.path.isfile(root):
        raise FlashError(f"Root image not found: {root}")
    kind, ver, patch = boot_header(root)
    if kind not in paths:
        raise FlashError(f"Your root image is a {kind} image, but this ROM has no {kind}.img to replace.")
    rkind, rver, rpatch = boot_header(paths[kind])
    if (ver, patch) != (rver, rpatch):
        raise FlashError(f"Your rooted {kind} is from a different build (Android {ver}, patch {patch}); this ROM's "
                         f"is Android {rver}, patch {rpatch}. Patch THIS ROM's {kind}.img and try again.")
    log(f"Root: your {kind}.img (Android {ver}, patch {patch}) replaces the ROM's {kind}.img")
    new = dict(paths)
    new[kind] = root
    return new


# --------------------------------------------------------------------------- main flow

def preflight(fb, args):
    fb.wait_for_bootloader(want_serial=args.serial, any_product=args.any_product)
    info = {
        "product": fb.getvar("product"),
        "userspace": fb.getvar("is-userspace"),
        "unlocked": fb.getvar("unlocked"),
        "slot": fb.getvar("current-slot"),
        "super": fb.getvar("partition-size:super"),
    }
    devinfo = fb.run("oem", "device-info", check=False, echo=False, timeout=20)
    info["critical"] = "true" if re.search(r"critical unlocked:\s*true", devinfo, re.I) else "false"
    log(f"Phone: product={info['product']} slot={info['slot']} unlocked={info['unlocked']} "
        f"critical-unlocked={info['critical']} fastbootd={info['userspace']} super={info['super']}")
    if info["userspace"] == "yes":
        raise FlashError("The phone is in fastbootd. Run `fastboot reboot bootloader` and start again.")
    if info["product"] != EXPECTED_PRODUCT and not args.any_product:
        raise FlashError(f"This tool is for the Nothing Phone 3 (product '{EXPECTED_PRODUCT}'), "
                         f"but the phone says '{info['product']}'. Stopping.")
    if info["unlocked"] != "yes":
        raise FlashError("The bootloader is locked. Unlock it first (fastboot flashing unlock).")
    try:
        info["super_size"] = int(info["super"], 16)
    except ValueError:
        raise FlashError(f"Could not read the size of the super partition ('{info['super']}').")
    if info["slot"] not in ("a", "b"):
        raise FlashError(f"Could not read the current slot ('{info['slot']}').")
    return info


def print_plan(plan, images, slot, mode, super_size, layout, wipe):
    log("")
    log("=" * 64)
    log(f" PLAN  ({mode} install, target slot {slot})")
    log("=" * 64)
    if plan["firmware"]:
        fw = plan.get("firmware_slots") or ("both" if mode == "stock" else "target")
        where = "both slots" if fw == "both" else f"slot {slot}"
        log(f" Firmware -> {where}: {', '.join(plan['firmware'])}")
    log(f" Then ONE super.img (built + verified on the PC first), slot {slot}:")
    for name, off, size in layout:
        log(f"     {name + '_' + slot:16} {human(size):>10}   at {off:#x}")
    used = layout[-1][1] + layout[-1][2]
    log(f"   uses {human(used)} of {human(super_size)}; the partition table is rebuilt (a full super wipe)")
    log(f" Then boot images -> {'both slots' if plan.get('boot_slots', 'both') == 'both' else 'slot ' + slot}, "
        f"vbmeta last: {', '.join(plan['boot']) or '(none)'}")
    if plan.get("disable_verity"):
        log(f" vbmeta images flashed with --disable-verity (verity off, verification stays ON)")
    if plan.get("both_slots"):
        other = "b" if slot == "a" else "a"
        log(f" Both slots bootable: the {other} names point at the same blocks (experimental)")
    log(f" Then: erase misc{', wipe data (fastboot -w)' if wipe else ' (data kept)'}, set active slot {slot}, reboot")
    for name, why in plan["ignored"]:
        log(f" skipped: {name}  ({why})")
    log("=" * 64)


def stream_sha256(reader):
    h = hashlib.sha256()
    for b in reader():
        h.update(b)
    return h.hexdigest()


def build_super(images, layout, meta, super_size, slot, out):
    """Write ONE sparse super.img (partition table + every logical image), then re-read it on its own and
    check the table and every partition byte for byte against the source images. Nothing is flashed from
    a file that has not passed this check."""
    log(f"Building super.img ({human(layout[-1][1] + layout[-1][2])}) in {os.path.dirname(out)} ...")
    regions = [(0, len(meta), lambda: [meta])]
    regions += [(off, size, images[name].reader(0, size)) for name, off, size in layout]
    lpsuper.write_sparse_image(out, super_size, regions)
    log("Verifying super.img against the source images ...")
    chunks = sorted(lpsuper.read_sparse_raw_chunks(out))
    with open(out, "rb") as f:
        f.seek(chunks[0][2])
        table = lpsuper.parse_metadata_region(f.read(chunks[0][1]))
    want = {f"{n}_{slot}": (off, size) for n, off, size in layout}
    got = {p[0]: (p[2], p[3]) for p in table["partitions"] if p[3]}
    if chunks[0][0] != 0 or got != want or table["super_size"] != super_size:
        raise FlashError("super.img verify failed: its partition table does not match the plan. Nothing flashed.")
    for name, off, size in layout:
        if lpsuper.sparse_region_sha256(out, chunks, off, size) != stream_sha256(images[name].reader(0, size)):
            raise FlashError(f"super.img verify failed: {name} differs from {name}.img. Nothing flashed.")
        log(f"  {name:12} OK")
    log(f"  partition table OK ({table['copies']} copies) - super.img sha256 {sha256_file(out)[:16]}...")


def recheck_phone(fb, slot):
    """Right before flashing: the locked phone must still answer in the plain bootloader. If it's gone (cable,
    reboot, screen timeout on some setups), wait for it to come back - nothing has been written yet."""
    serial = fb.serial
    if fb.getvar("product") != EXPECTED_PRODUCT:
        log("The phone isn't answering in the bootloader any more. Nothing has been flashed yet.")
        log("Put it back in the bootloader (hold Power + Volume Down) and plug it in; waiting up to 5 minutes...")
        fb.wait_for_bootloader(seconds=300, want_serial=serial)
    if fb.getvar("is-userspace") == "yes":
        raise FlashError("The phone is in fastbootd now. Run `fastboot reboot bootloader` and start again. "
                         "Nothing has been flashed.")
    if fb.getvar("product") != EXPECTED_PRODUCT:
        raise FlashError("The phone stopped answering right before flashing. Nothing has been flashed. "
                         "Check the cable and start again.")
    log("Phone check before flashing: still in the bootloader, same phone. Flashing now.")


def flash_all(fb, args, plan, images, slot, mode, super_size, layout, workdir):
    """Order proven on the phone 2026-11-20: firmware -> ONE super.img -> boot images -> vbmeta LAST ->
    erase misc -> wipe -> set_active -> reboot. Bootloader only: no fastbootd, recovery or adb."""
    other = "b" if slot == "a" else "a"
    fw_both = {"both": True, "target": False}.get(getattr(args, "firmware_slots", None) or "", mode == "stock")
    boot_both = (getattr(args, "boot_slots", None) or "both") == "both"
    # 0. build + verify super.img BEFORE touching the phone, so a bad build can't leave it half-flashed
    meta = lpsuper.metadata_region(layout, super_size, slot, GROUP, getattr(args, 'both_slots', False))
    lpsuper.parse_metadata_region(meta)
    super_img = os.path.join(workdir, "super.img")
    build_super(images, layout, meta, super_size, slot, super_img)
    # 0b. building super can take a long time on a slow PC: make sure the SAME phone is still in the plain
    #     bootloader (not rebooted, not unplugged, not in fastbootd) right before the first write
    recheck_phone(fb, slot)
    # 1. firmware
    for name in plan["firmware"]:
        for s in ([slot, other] if fw_both else [slot]):
            fb.run("flash", f"{name}_{s}", images[name].path)
    # 2. super, in one go (fastboot splits it to the phone's max-download-size by itself)
    try:
        fb.run("flash", "super", super_img)
    finally:
        os.remove(super_img)
    # 3. boot images, then vbmeta LAST (plan["boot"] is sorted with the vbmetas at the end).
    #    Never --disable-verification: on this phone that makes the bootloader mark the slot unbootable.
    #    --disable-verity (only that flag) when system/product were changed (GApps).
    for name in plan["boot"]:
        for s in ((slot, other) if boot_both else (slot,)):
            if getattr(args, "disable_verity", False) and name in VBMETA:
                fb.run("--disable-verity", "flash", f"{name}_{s}", images[name].path)
            else:
                fb.run("flash", f"{name}_{s}", images[name].path)
    # 4. clean up boot state, wipe, activate, reboot
    fb.run("erase", "misc")
    if not args.keep_data:
        fb.run("-w")
    fb.run("set_active", slot)
    now = fb.getvar("current-slot")
    if now != slot:
        raise FlashError(f"Slot check failed: the phone reports slot '{now}', not {slot}. Not rebooting. "
                         f"Run: fastboot set_active {slot}")
    log(f"Slot check: active slot is {now}.")
    if not args.no_reboot:
        fb.run("reboot")


def main():
    global LOGFILE
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    import gapps
    ap = argparse.ArgumentParser(description="Bootloader-only flasher for the Nothing Phone 3.")
    ap.add_argument("mode", choices=["stock", "rom"], help="stock = unbrick to Nothing OS, rom = custom ROM")
    ap.add_argument("source", help="stock: the NothingFlasher image folder. rom: the ROM .zip or a folder of .img files")
    ap.add_argument("--dry-run", action="store_true", help="check and plan everything, flash nothing")
    ap.add_argument("--slot", choices=["a", "b"], help="slot to install to (default: b; the tool switches to it first)")
    ap.add_argument("--skip-firmware", action="store_true", help="do not flash firmware partitions")
    ap.add_argument("--keep-data", action="store_true", help="do not wipe data (only for updating the same ROM)")
    ap.add_argument("--no-reboot", action="store_true", help="stay in fastboot at the end")
    ap.add_argument("--disable-verity", action="store_true",
                    help="flash vbmeta with verity off (needed before GApps/debloat zips change system/product)")
    ap.add_argument("--boot-slots", choices=["target", "both"], default="both",
                    help="boot images + vbmeta: target slot only, or both slots (default both)")
    ap.add_argument("--firmware-slots", choices=["target", "both"],
                    help="firmware: target slot only, or both (default: both for stock, target for ROMs)")
    ap.add_argument("--both-slots", action="store_true",
                    help="experimental: the other slot's system/vendor/... point at the same blocks in super, "
                         "so the phone boots from either slot")
    ap.add_argument("--work", help="folder for temporary files (default: next to this tool)")
    ap.add_argument("--serial", help="flash only the phone with this USB serial (when several are connected)")
    ap.add_argument("--fastboot", help="path to fastboot.exe")
    ap.add_argument("--super-size", help="super size for --dry-run without a phone (hex), default 0x240000000")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--gapps", choices=gapps.ORDER,
                    help="bake Google apps into the ROM's images on this PC (rom mode). Core/Basic come with ONE "
                         "setup wizard (-pixel = PixelSetupWizard); Omni, Stock, MindTheGapps alone. No other add-ons")
    ap.add_argument("--gapps-dir", help="folder holding the GApps zips (found by checksum, any file name)")
    ap.add_argument("--customize-remove", default="",
                    help="comma list of GApps app packages to leave out (their ROM counterparts stay)")
    ap.add_argument("--root-image", help="your own pre-rooted init_boot.img or boot.img (Magisk, KernelSU, "
                                         "APatch) made from THIS ROM; it replaces the ROM's own")
    ap.add_argument("--any-product", action="store_true", help=argparse.SUPPRESS)
    # developer test builds only: bake this PC's adb public key into /adb_keys so a wiped test phone
    # trusts the PC without a tap. Hidden from --help; never used by the GUI.
    ap.add_argument("--dev-adb-key", help=argparse.SUPPRESS)
    args = ap.parse_args()

    workdir = os.path.abspath(args.work or os.path.join(here, "work"))
    os.makedirs(workdir, exist_ok=True)
    LOGFILE = open(os.path.join(workdir, f"flash_{time.strftime('%Y%m%d_%H%M%S')}.log"), "w", encoding="utf-8")
    log(f"MetroidFlasher {TOOL_VERSION}  mode={args.mode}  source={args.source}")

    try:
        hints = [here, parent, os.path.join(parent, "PlatformTools"), os.path.join(parent, "platform-tools"),
                 os.path.join(args.source, "platform-tools") if os.path.isdir(args.source) else here]
        fastboot = args.fastboot or find_exe("fastboot.exe", hints) or find_exe("fastboot", hints)
        dumper = find_exe("payload-dumper-go.exe", [here, parent]) or shutil.which("payload-dumper-go")

        # images
        src = os.path.abspath(args.source.strip().strip('"'))
        if not os.path.exists(src):
            raise FlashError(f"File or folder not found:\n  {src}\n"
                             "Drag the ROM .zip (or its image folder) into the window, or type the full path.")
        if os.path.isdir(src):
            paths = collect_dir(src)
        elif src.lower().endswith(".zip"):
            if args.mode == "stock":
                raise FlashError("stock mode takes the NothingFlasher folder of .img files, not a zip.")
            paths = collect_zip(src, workdir, dumper)
        else:
            raise FlashError(f"{src} is not a folder or a .zip")
        if args.gapps:
            paths = bake_gapps(paths, args, workdir, here, parent)
        if args.root_image:
            paths = swap_root_image(paths, args.root_image)
        plan = classify(paths, args.mode, args.skip_firmware)
        check_headers(plan, paths)
        images = {n: Image(n, p) for n, p in paths.items()
                  if n in plan["logical"] or n in plan["boot"] or n in plan["firmware"]}

        # phone
        if args.dry_run and not fastboot:
            log("(dry run: fastboot not found, planning without a phone)")
        fb = Fastboot(fastboot) if fastboot else None
        if fb and not args.dry_run:
            info = preflight(fb, args)
        elif fb and args.dry_run:
            out = subprocess.run([fastboot, "devices"], capture_output=True, text=True).stdout
            info = preflight(fb, args) if has_fastboot_device(out) else None
        else:
            info = None
        if not args.dry_run and not fb:
            raise FlashError("fastboot.exe not found. Put it next to this tool or pass --fastboot.")
        if info:
            super_size, slot = info["super_size"], args.slot or "b"
            if plan["firmware"] and info["critical"] != "true":
                raise FlashError("Firmware needs critical partitions unlocked.\n" + CRITICAL_HINT)
            # Always install to slot b: make b active FIRST and confirm it reads back, so the slot we flash
            # is the slot the phone is on (and the one it will boot).
            if info["slot"] != slot:
                if args.dry_run:
                    log(f"Active slot is {info['slot']}: the real flash will switch to slot {slot} first.")
                else:
                    log(f"Active slot is {info['slot']}: switching to slot {slot} before flashing.")
                    fb.run("set_active", slot)
                    now = fb.getvar("current-slot")
                    if now != slot:
                        raise FlashError(f"Could not switch to slot {slot} (the phone still reports '{now}'). "
                                         f"Nothing was flashed.")
                    log(f"Slot check: active slot is now {now}.")
            else:
                log(f"Slot check: active slot is {slot}.")
        else:
            super_size = int(args.super_size or hex(DEFAULT_SUPER_SIZE), 16)
            slot = args.slot or "b"
            log(f"(no phone connected: planning for slot {slot}, super {super_size:#x})")

        try:
            layout = lpsuper.plan_layout([(n, images[n].size) for n in plan["logical"]], super_size)
            check = lpsuper.parse_metadata_region(lpsuper.metadata_region(layout, super_size, slot, GROUP, getattr(args, 'both_slots', False)))
        except ValueError as e:  # layout does not fit / bad image size: stop cleanly, nothing sent
            raise FlashError(f"The system images do not fit this phone's super partition: {e}")
        for (name, off, size), (pname, _, start, psize) in zip(layout, check["partitions"]):
            if pname != f"{name}_{slot}" or start != off or psize != size:
                raise FlashError("internal check failed: partition table does not read back correctly")
        plan["disable_verity"] = args.disable_verity
        plan["both_slots"] = args.both_slots
        plan["boot_slots"] = args.boot_slots
        plan["firmware_slots"] = args.firmware_slots
        print_plan(plan, images, slot, args.mode, super_size, layout, not args.keep_data)
        log(f" partition table self-check: OK ({check['copies']} copies verified)")

        need = layout[-1][1] + layout[-1][2] + 256 * 1024 * 1024
        if free_bytes(workdir) < need:
            raise FlashError(f"Building super.img needs {human(need)} free in {workdir}, only "
                             f"{human(free_bytes(workdir))} available. Use --work to point at a bigger drive.")

        if args.dry_run:
            log("\nDry run finished. Nothing was sent to the phone.")
            return 0
        if not args.yes:
            log("\nThis ERASES the phone (unless --keep-data). Type FLASH to continue:")
            if input("> ").strip() != "FLASH":
                log("Cancelled. Nothing was flashed.")
                return 1
        t0 = time.time()
        flash_all(fb, args, plan, images, slot, args.mode, super_size, layout, workdir)
        remember_install(workdir, args, paths)
        if args.no_reboot:
            # more steps follow (e.g. the GApps sideloads); "DONE" here would read as the whole job being done
            log(f"\nROM part finished in {int(time.time() - t0)}s. Continuing with the next steps...")
        else:
            log(f"\nDONE in {int(time.time() - t0)}s. First boot can sit on the logo for a while - give it some time.")
        return 0
    except FlashError as e:
        log(f"\nSTOPPED: {e}")
        log("Nothing after this point was flashed. The phone is still in fastboot.")
        return 2
    except KeyboardInterrupt:
        log("\nInterrupted. Leave the phone in fastboot and run the tool again.")
        return 3
    finally:
        if LOGFILE:
            log(f"(log: {LOGFILE.name})")
            LOGFILE.close()


if __name__ == "__main__":
    sys.exit(main())
