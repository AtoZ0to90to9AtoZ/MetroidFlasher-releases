"""bakegapps - put a NikGapps package into a ROM's product/system_ext images WITHOUT running its scripts.

Runs on Linux (needs debugfs from e2fsprogs). Works on copies; the input images are never modified.

    python3 bakegapps.py --gapps NikGapps-omni.zip --remove debloat.list \
        --product product.img --system-ext system_ext.img --out OUTDIR

What it does, in order:
 1. reads every app package inside the GApps zip (AppSet/*/*.zip): its files, its default partition
    and its set_prop lines. Files are named like ___priv-app___GoogleDialer/GoogleDialer.apk.
 2. deletes every path in the removal list (our debloat map) from the right image; a path that is not
    there STOPS the build (that is how a changed ROM gets noticed, instead of Nik's silent misses)
 3. writes the GApps files: 0644 files, 0755 folders, root:root, SELinux u:object_r:system_file:s0
 4. adds the set_prop lines to product/etc/build.prop
 5. grows an image first if the files would not fit, and runs e2fsck on the result
"""
import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

LABEL = b"u:object_r:system_file:s0\0"
PARTS = ("product", "system_ext")
# Where a partition's own paths start inside its image. system.img is system-as-root: /system/... in Android
# is /system/... inside the image too. product and system_ext images start at their own root.
IMG_ROOT = {"product": "", "system_ext": "", "system": "/system"}

# GApps packages whose removal map has been made and tested. Nik may update his package at any time; a
# different file means a different package, so the bake refuses it until it has been mapped (author 2026-11-20).
KNOWN_GAPPS = {
    "a6844eeb2613541558237e467b20feb01f8b45b2ebab0109834140ea59b6ca4d":
        "MindTheGapps-17.0.0-arm64-20261022_132445.zip (MindTheGapps package, mapped 2026-11-22)",
    "bb8ce77643bc853c7d6aa9ebb4e733f558a1427b92c9521b7d60d8c31018e7f7":
        "NikGapps-omni-arm64-17-20261110-signed.zip (tested on crDroid 13.0 20261111, 2026-11-20)",
    "e917fb2cbcc56646d40589a7afc2221300e34beb2db5bc85e611ad7fa564ba93":
        "NikGapps-stock-arm64-17-20261110-signed.zip (crDroid 13.0 20261111: fits after shrink, 98 MiB spare; booted 2026-11-21)",
    "b16150b82e7a5ac62eda8fc254c8676c0e451d8aecd05f3cbc432282f479f7d4":
        "NikGapps-basic-arm64-17-20261110-signed.zip (Basic, and Core = its AppSet/Core/; built+verified 2026-11-21)",
    "ad6ed440431c9c0020e6a22eb21bd6458b3804116240830971a09a6c57688635":
        "NikGapps-Addon-17-SetupWizard-20261110-signed.zip (add-on for Core and Basic; built+verified 2026-11-21)",
    "6a0fe590484796241c305fb6c94e2b249d1bce27c8112698c68e4f4131ca8a3b":
        "NikGapps-Addon-17-PixelSetupWizard-20261110-signed.zip (the other setup wizard for Core and Basic; 2026-11-23)",
}


def debugfs(img, cmds, write=False):
    """Run debugfs with a command file; returns stdout. Raises on any reported error."""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".cmd") as f:
        f.write("\n".join(cmds) + "\n")
        name = f.name
    try:
        r = subprocess.run(["debugfs"] + (["-w"] if write else []) + ["-f", name, img],
                           capture_output=True, text=True)
    finally:
        os.unlink(name)
    errs = [l for l in r.stderr.splitlines() if l.strip() and not l.startswith("debugfs ")]
    if errs:
        raise SystemExit(f"debugfs on {img} reported:\n  " + "\n  ".join(errs[:20]))
    return r.stdout


def exists(img, path):
    r = subprocess.run(["debugfs", "-R", f"stat {path}", img], capture_output=True, text=True)
    return "Inode:" in r.stdout


def walk(img, path):
    """All entries under path, deepest first: list of (path, is_dir)."""
    out = []
    listing = subprocess.run(["debugfs", "-R", f"ls -p {path}", img], capture_output=True, text=True).stdout
    for line in listing.splitlines():
        f = line.strip("/").split("/")
        if len(f) < 5 or f[4] in (".", ".."):   # folders have an empty size field, so 5 fields
            continue
        mode, name = int(f[1], 8), f[4]
        child = f"{path}/{name}"
        if mode & 0o170000 == 0o040000:
            out += walk(img, child)
            out.append((child, True))
        else:
            out.append((child, False))
    return out


def free_bytes(img):
    out = subprocess.run(["dumpe2fs", "-h", img], capture_output=True, text=True).stdout
    get = lambda k: int(re.search(rf"^{k}:\s+(\d+)", out, re.M).group(1))
    return get("Free blocks") * get("Block size")


# Every NikGapps 17 zip targets Android 17, and so does every ROM MetroidFlasher bakes into.
ANDROID_VERSION = 17
_OPS = {"-ge": lambda a, b: a >= b, "-gt": lambda a, b: a > b, "-le": lambda a, b: a <= b,
        "-lt": lambda a, b: a < b, "-eq": lambda a, b: a == b, "-ne": lambda a, b: a != b}


def installer_props(sh, name="installer.sh"):
    """The set_prop lines Nik's installer.sh would really run, as [(key, value)].
    Follows `if [ "$androidVersion" -OP N ]; then / else / fi` the way the script does on Android 17
    (SetupWizardPixel sets glif_expressive on 17+ and glif_v3_light below; taking both = crash, 2026-11-23).
    A set_prop under any other kind of `if` stops the bake: it has to be looked at first."""
    out, stack = [], []          # stack of True / False / None (None = condition we don't evaluate)
    for line in sh.splitlines():
        t = line.strip()
        m = re.match(r'if \[ "?\$androidVersion"? (-ge|-gt|-le|-lt|-eq|-ne) "?(\d+)"? \]; then$', t)
        if m:
            stack.append(_OPS[m.group(1)](ANDROID_VERSION, int(m.group(2))))
        elif re.match(r'if\b', t):
            stack.append(None)
        elif t == "else" and stack:
            stack[-1] = None if stack[-1] is None else not stack[-1]
        elif t.startswith("elif") and stack:
            stack[-1] = None
        elif t == "fi" and stack:
            stack.pop()
        else:
            m = re.match(r'set_prop "([^"]+)" "([^"]*)" "\$product/etc/build.prop"', t)
            if m:
                if None in stack:
                    raise SystemExit(f"{name}: set_prop {m.group(1)} sits under an installer condition "
                                     "MetroidFlasher can't evaluate - not handled yet")
                if all(stack):
                    out.append((m.group(1), m.group(2)))
    return out


def read_packages(gapps_zip, only=None):
    """{pkg: {"partition": p, "files": {dest_rel: bytes}, "props": [(k, v)]}}
    only: keep just the app packages under this AppSet prefix (Core = Basic's "AppSet/Core/")."""
    pkgs = {}
    top = zipfile.ZipFile(gapps_zip)
    if not any(n.startswith("AppSet/") for n in top.namelist()) and "system/product/" in "".join(top.namelist()[:80]):
        return read_mtg(gapps_zip, top)
    for n in top.namelist():
        if not (n.startswith("AppSet/") and n.endswith(".zip")):
            continue
        if only and not n.startswith(only):
            continue
        inner = zipfile.ZipFile(io.BytesIO(top.read(n)))
        sh = inner.read("installer.sh").decode()
        part = re.search(r'^default_partition="([^"]+)"', sh, re.M).group(1)
        if part not in PARTS:
            raise SystemExit(f"{n}: installs to '{part}', which this baker does not handle")
        files = {}
        for e in inner.namelist():
            if e.endswith("/") or e in ("installer.sh", "uninstaller.sh"):
                continue
            if e.endswith(".prop") and "___etc___permissions" in e:
                continue        # Nik's own bookkeeping file; his installer keeps it in /system, not in the ROM
            rel = re.sub(r"^___", "", e).replace("___", "/")
            first = rel.split("/")[0]
            if first == "system":       # e.g. Stock's Pixel text-classifier models in /system/etc
                dest_part, rel = "system", rel[len("system/"):]
            elif first in PARTS or first == "vendor":
                raise SystemExit(f"{n}: {e} forces partition '{first}' - not handled yet")
            else:
                dest_part = "product" if first == "overlay" else part
            files[(dest_part, "/" + rel)] = ((gapps_zip, n), e, inner.getinfo(e).file_size)
        # In recovery Nik's script sees partitions at /system/<part>; the text it writes says so (and
        # /system/product -> /product on a running phone), so write the same strings for a byte-identical result.
        mount = "/system/" + part
        props = [(k, v.replace("$install_partition", mount)) for k, v in installer_props(sh, n)]
        # files the installer writes itself: script_text="..." then echo -e "$script_text" > $install_partition/...
        for body, target in re.findall(r'script_text="(.*?)"\s*\n\s*echo -e "\$script_text" > (\S+)', sh, re.S):
            text = body.replace('\\"', '"').replace("$install_partition", mount) + "\n"
            rel = target.replace("$install_partition", "")
            if not rel.startswith("/") or "$" in rel:
                raise SystemExit(f"{n}: can't place generated file {target}")
            files[(part, rel)] = ("text", text.encode(), len(text))
        removes = re.search(r'remove_aosp_apps_from_rom="(.*?)"', sh, re.S).group(1).split()
        pkgs[os.path.basename(n)[:-4]] = {"partition": part, "files": files, "props": props, "removes": removes}
    return pkgs


# MindTheGapps: one plain tree (system/product/..., system/system_ext/...), no per-app packages and no removal
# list of its own. Its installer (update-binary) only: drops VelvetTitan unless the device is tangorpro, gives
# every file 0644 (0755 for .sh / no extension), root:root, system_file, and removes Provision when SetupWizard
# goes in. addon.d (survive an OTA in recovery) is not needed: MetroidFlasher rebuilds super on every flash.
# The bake splits the tree into one package per app folder (for Customize); everything shared (Play services
# APEX, overlays, permissions, sysconfig, libs) is the locked "GooglePlayServices" package.
MTG_SKIP = ("VelvetTitan",)
MTG_REMOVES = {"GooglePlayServices": ["GmsCore"],          # the ROM's microG (same package name as the APEX)
               "SetupWizard": ["Provision"]}


def read_mtg(gapps_zip, top):
    pkgs = {}
    for info in top.infolist():
        e = info.filename
        if e.endswith("/") or not e.startswith("system/"):
            continue
        parts = e.split("/")
        if parts[1] not in PARTS:
            if parts[1] == "addon.d":
                continue
            raise SystemExit(f"{e}: MindTheGapps file outside product/system_ext - not handled yet")
        part, rel = parts[1], "/" + "/".join(parts[2:])
        if len(parts) >= 5 and parts[2] in ("app", "priv-app"):
            name = parts[3]
            if name in MTG_SKIP:
                continue
        else:
            name = "GooglePlayServices"
        base = parts[-1]
        mode = 0o755 if base.endswith(".sh") or "." not in base else 0o644
        pk = pkgs.setdefault(name, {"partition": part, "files": {}, "props": [], "removes": list(MTG_REMOVES.get(name, []))})
        pk["files"][(part, rel)] = (("top", gapps_zip), e, info.file_size, mode)
    return pkgs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gapps", required=True, nargs="+",
                    help="GApps zip(s): the package, then any add-ons (e.g. SetupWizard). ZIP::AppSet/Core/ keeps only "
                         "that folder's apps (Core = Basic's AppSet/Core + the SetupWizard add-on)")
    ap.add_argument("--remove", required=True,
                    help="debloat list: one /product/..., /system_ext/... or /system/... path per line")
    ap.add_argument("--product", required=True)
    ap.add_argument("--system-ext", required=True)
    ap.add_argument("--system", help="system.img; needed when the package or the removal list touches /system")
    ap.add_argument("--out", required=True)
    ap.add_argument("--unknown-gapps", action="store_true",
                    help="bake a GApps zip that is not in KNOWN_GAPPS (for mapping a new package; not for users)")
    a = ap.parse_args()

    sources = []
    for spec in a.gapps:
        path, _, only = spec.partition("::")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(16 * 2**20), b""):
                h.update(chunk)
        gsha = h.hexdigest()
        if gsha in KNOWN_GAPPS:
            print(f"GApps zip verified: {KNOWN_GAPPS[gsha]}" + (f" (only {only})" if only else ""))
        elif a.unknown_gapps:
            print(f"WARNING: unknown GApps zip {os.path.basename(path)} (sha256 {gsha}); baking anyway because --unknown-gapps was given")
        else:
            raise SystemExit(f"STOP: {os.path.basename(path)} is not a GApps zip MetroidFlasher has mapped (sha256 {gsha}).\n"
                             "A changed package can install or remove different files. Supported: "
                             + "; ".join(KNOWN_GAPPS.values()))
        sources.append((path, only or None))

    os.makedirs(a.out, exist_ok=True)
    imgs = {}
    for part, src in (("product", a.product), ("system_ext", a.system_ext), ("system", a.system)):
        if not src:
            continue
        dst = os.path.join(a.out, part + ".img")
        shutil.copyfile(src, dst)
        imgs[part] = dst

    pkgs = {}
    for path, only in sources:
        for name, pk in read_packages(path, only).items():
            if name in pkgs:
                print(f"  note: {name} comes from more than one zip; using the one in {os.path.basename(path)}")
            pkgs[name] = pk
    adds = {}
    props = []
    for name, p in sorted(pkgs.items()):
        adds.update(p["files"])
        props += p["props"]
    print(f"GApps: {len(pkgs)} packages, {len(adds)} files, {sum(r[2] for r in adds.values()) / 2**20:.0f} MiB, {len(props)} props")
    if any(pp not in imgs for pp, _ in adds):
        raise SystemExit("STOP: this package writes into /system - pass --system system.img")
    tops, inner_cache = {}, {}

    def extract(ref, local):
        key, e, _ = ref
        if key == "text":
            open(local, "wb").write(e)
            return
        if key not in inner_cache:
            inner_cache.clear()
            zp, n = key
            if zp not in tops:
                tops[zp] = zipfile.ZipFile(zp)
            inner_cache[key] = zipfile.ZipFile(io.BytesIO(tops[zp].read(n)))
        with inner_cache[key].open(e) as src, open(local, "wb") as dst:
            shutil.copyfileobj(src, dst, 16 * 2**20)

    # 1. removals - every path must exist
    removals = [l.strip() for l in open(a.remove) if l.strip() and not l.startswith("#")]
    for path in removals:
        part, rel = path.split("/")[1], "/" + "/".join(path.split("/")[2:])
        if part not in imgs:
            raise SystemExit(f"removal {path}: partition '{part}' not given (pass --{part.replace('_', '-')})")
        rel = IMG_ROOT[part] + rel
        if not exists(imgs[part], rel):
            raise SystemExit(f"STOP: {path} is not in the ROM - the map no longer matches this ROM build")
        entries = walk(imgs[part], rel) + [(rel, True)]
        debugfs(imgs[part], [("rmdir " if d else "rm ") + p for p, d in entries], write=True)
        print(f"  removed {path} ({sum(1 for _, d in entries if not d)} files)")

    # 1b. Nik's own removals (folder names), where they really exist - e.g. GoogleRestore removes Seedvault
    for name in sorted({r for p in pkgs.values() for r in p["removes"]}):
        for part, img in imgs.items():
            for d in ("/app/", "/priv-app/"):
                at = IMG_ROOT[part] + d + name
                if exists(img, at):
                    entries = walk(img, at) + [(at, True)]
                    debugfs(img, [("rmdir " if x else "rm ") + p for p, x in entries], write=True)
                    print(f"  removed /{part}{d}{name} (named by the GApps package)")

    # 2. space check, grow if needed (+64 MiB headroom)
    for part, img in imgs.items():
        # system only gets a few replaced files (Stock's text-classifier models), so it keeps a small margin
        # instead of growing - every MiB an image grows comes out of super's free space
        need = sum(r[2] + 4096 for (pp, _), r in adds.items() if pp == part) + (8 if part == "system" else 64) * 2**20
        free = free_bytes(img)
        if need > free:
            grow = need - free
            new = os.path.getsize(img) + grow + (-grow % 2**20)
            os.truncate(img, new)
            subprocess.run(["e2fsck", "-fy", img], capture_output=True)
            r = subprocess.run(["resize2fs", img], capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(f"resize2fs {part} failed:\n{r.stderr}")
            print(f"  grew {part} by {grow / 2**20:.0f} MiB")

    # 3. add files
    with tempfile.TemporaryDirectory() as tmp:
        label = os.path.join(tmp, "label")
        open(label, "wb").write(LABEL)
        for part, img in imgs.items():
            mine = sorted(((IMG_ROOT[part] + rel, ref) for (pp, rel), ref in adds.items() if pp == part),
                          key=lambda x: (str(x[1][0]), x[0]))   # group by source zip; "text" = generated files
            if not mine:
                continue
            cmds, made = [], set()
            for i, (rel, ref) in enumerate(mine):
                parts = rel.strip("/").split("/")
                for k in range(1, len(parts)):
                    d = "/" + "/".join(parts[:k])
                    if d in made or exists(img, d):
                        made.add(d)
                        continue
                    cmds += [f"mkdir {d}", f"sif {d} mode 040755", f"sif {d} uid 0", f"sif {d} gid 0",
                             f"ea_set -f {label} {d} security.selinux"]
                    made.add(d)
                local = os.path.join(tmp, f"f{i}")
                extract(ref, local)
                if exists(img, rel):
                    cmds.append(f"rm {rel}")
                cmds += [f"write {local} {rel}", f"sif {rel} mode 0100644", f"sif {rel} uid 0", f"sif {rel} gid 0",
                         f"ea_set -f {label} {rel} security.selinux"]
            debugfs(img, cmds, write=True)
            print(f"  wrote {len(mine)} files into {part}")

        # 4. build.prop
        bp = os.path.join(tmp, "build.prop")
        debugfs(imgs["product"], [f"dump /etc/build.prop {bp}"])
        text = open(bp, encoding="utf-8", newline="").read()
        for k, v in props:      # exactly like Nik's set_prop: replace if the key appears, else append
            if k in text:
                text = re.sub(rf"({re.escape(k)})=.*", lambda m: f"{m.group(1)}={v}", text)
            else:
                text += f"{k}={v}\n"
        open(bp, "w", encoding="utf-8", newline="").write(text)
        debugfs(imgs["product"], ["rm /etc/build.prop", f"write {bp} /etc/build.prop", "sif /etc/build.prop mode 0100600",
                                  "sif /etc/build.prop uid 0", "sif /etc/build.prop gid 0",
                                  f"ea_set -f {label} /etc/build.prop security.selinux"], write=True)
        print(f"  build.prop: +{len(props)} props")

    # 5. shrink every image to its content. Free space left inside one image can't be used by another, and
    #    super only has so much room (Stock on crDroid fits ONLY after this: 2026-11-21, ~103 MiB spare).
    for part, img in imgs.items():
        subprocess.run(["e2fsck", "-fy", img], capture_output=True)
        r = subprocess.run(["resize2fs", "-M", img], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"resize2fs -M {part} failed:\n{r.stderr}")
        out = subprocess.run(["dumpe2fs", "-h", img], capture_output=True, text=True).stdout
        get = lambda k: int(re.search(rf"^{k}:\s+(\d+)", out, re.M).group(1))
        os.truncate(img, get("Block count") * get("Block size"))

    # 6. verify
    for part, img in imgs.items():
        r = subprocess.run(["e2fsck", "-fn", img], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"e2fsck found problems in {part}:\n{r.stdout[-2000:]}")
        h = hashlib.sha256()
        with open(img, "rb") as f:
            for chunk in iter(lambda: f.read(16 * 2**20), b""):
                h.update(chunk)
        print(f"  e2fsck {part}: clean, {free_bytes(img) / 2**20:.0f} MiB free, sha256 {h.hexdigest()[:16]}")
    print("BAKE OK")


if __name__ == "__main__":
    main()
