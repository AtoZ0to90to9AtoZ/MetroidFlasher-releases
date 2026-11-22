"""bakewin - bake a NikGapps package into a ROM's images on ANY OS (pure Python; no WSL, no debugfs).

Same rules as bakegapps.py (the Linux version, proven on the phone for Core/Basic/Omni/Stock 2026-11-20/21):
  1. the GApps zip(s) must be known packages (KNOWN_GAPPS), unless --unknown-gapps
  2. every path in the removal map must exist (a changed ROM STOPS the bake), then Nik's own removals
     (folder names, only where they really exist) - each Google app takes out the ROM app it replaces
  3. GApps files: 0644 files / 0755 folders, root:root, u:object_r:system_file:s0
  4. Nik's set_prop lines go into product/etc/build.prop exactly like his set_prop
  5. every image is rebuilt FRESH by ext4write.py, sized to its content (no stranded free space)

    py bakewin.py --gapps PKG.zip [ADDON.zip ...] --remove map.list --rom ROMDIR --out OUTDIR
                  [--customize-remove PKG,PKG]  (drop those app packages from the GApps set: Customize)
ROMDIR holds the ROM's system.img, system_ext.img, product.img. OUTDIR gets the rebuilt ones.
"""
import argparse
import hashlib
import io
import os
import re
import stat
import sys
import tempfile
import time
import zipfile

import bakegapps
import ext4write

LABEL = bakegapps.LABEL
IMG_ROOT = bakegapps.IMG_ROOT
PARTS = ("product", "system_ext", "system")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(16 * 2**20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_props(system_img):
    """{key: value} from the ROM's /system/build.prop (system-as-root image)."""
    img = ext4write.ext4read.Ext4Image(system_img)
    try:
        n = img.inode(2)
        for part in ("system", "build.prop"):
            n = img.inode(dict(img.readdir(n))[part])
        text = img.read_data(n).decode("utf-8", "replace")
    finally:
        img.close()
    return dict(l.split("=", 1) for l in text.splitlines() if "=" in l and not l.startswith("#"))


def main(argv=None, log=print):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gapps", required=True, nargs="+")
    ap.add_argument("--remove", required=True)
    ap.add_argument("--rom", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--customize-remove", default="", help="comma list of app packages to leave out")
    ap.add_argument("--unknown-gapps", action="store_true")
    a = ap.parse_args(argv)
    removals = [l.strip() for l in open(a.remove, encoding="utf-8") if l.strip() and not l.startswith("#")]
    roms = {p: os.path.join(a.rom, p + ".img") for p in PARTS if os.path.isfile(os.path.join(a.rom, p + ".img"))}
    bake(a.gapps, removals, roms, a.out, a.customize_remove, a.unknown_gapps, log)


def bake(gapps_specs, removals, rom_imgs, out_dir, customize="", unknown_ok=False, log=print, extras=()):
    """Bake GApps into the ROM's images. rom_imgs: {"system"|"system_ext"|"product": path}.
    Returns {part: new image path} in out_dir. Raises SystemExit with a clear message on any STOP.
    extras: [(part, path, bytes, mode, selinux_label)] extra files to add (developer test builds only)."""
    class A:
        pass
    a = A()
    a.gapps, a.out, a.customize_remove, a.unknown_gapps = gapps_specs, out_dir, customize or "", unknown_ok
    t0 = time.time()

    # 1. packages
    pkgs = {}
    for spec in a.gapps:
        path, _, only = spec.partition("::")
        s = sha256(path)
        if s in bakegapps.KNOWN_GAPPS:
            log(f"GApps zip verified: {bakegapps.KNOWN_GAPPS[s]}" + (f" (only {only})" if only else ""))
        elif a.unknown_gapps:
            log(f"WARNING: unknown GApps zip {os.path.basename(path)} (sha256 {s}); --unknown-gapps given")
        else:
            raise SystemExit(f"STOP: {os.path.basename(path)} is not a GApps zip MetroidFlasher has mapped.")
        for name, pk in bakegapps.read_packages(path, only or None).items():
            pkgs[name] = pk
    for name in filter(None, (x.strip() for x in a.customize_remove.split(","))):
        if name not in pkgs:
            raise SystemExit(f"Customize: '{name}' is not in this GApps set ({', '.join(sorted(pkgs))})")
        del pkgs[name]
        log(f"  Customize: left out {name} (its ROM counterpart stays)")
    adds, props = {}, []
    for name, p in sorted(pkgs.items()):
        adds.update(p["files"])
        props += p["props"]
    log(f"GApps: {len(pkgs)} packages, {len(adds)} files, {sum(r[2] for r in adds.values()) / 2**20:.0f} MiB, "
        f"{len(props)} props")

    # 2. trees straight out of the ROM images
    trees = {part: ext4write.Tree.from_image(src) for part, src in rom_imgs.items()}
    if any(pp not in trees for pp, _ in adds):
        raise SystemExit("STOP: the package writes into a partition whose image is missing")

    # 3. removals: the map first (every path must exist), then Nik's folder names where present
    for path in removals:
        part, rel = path.split("/")[1], "/" + "/".join(path.split("/")[2:])
        if part not in trees:
            raise SystemExit(f"removal {path}: no {part}.img given")
        if trees[part].remove(IMG_ROOT[part] + rel) == 0:
            raise SystemExit(f"STOP: {path} is not in the ROM - the map no longer matches this ROM build")
        log(f"  removed {path}")
    for name in sorted({r for p in pkgs.values() for r in p["removes"]}):
        for part, t in trees.items():
            for d in ("/app/", "/priv-app/"):
                if t.remove(IMG_ROOT[part] + d + name):
                    log(f"  removed /{part}{d}{name} (named by the GApps package)")

    # 4. GApps files - extracted once to a temp folder (inner zips are read one at a time)
    os.makedirs(a.out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="mfbake-", dir=a.out)
    label = {"security.selinux": LABEL}
    by_zip = {}
    for (part, rel), ref in adds.items():
        by_zip.setdefault(ref[0] if ref[0] != "text" else ("text",), []).append((part, rel, ref))
    k = 0
    for key, items in by_zip.items():
        inner = None
        if key != ("text",):
            zp, n = key
            if zp == "top":                 # MindTheGapps: files sit straight in the zip
                inner = zipfile.ZipFile(n)
            else:
                inner = zipfile.ZipFile(io.BytesIO(zipfile.ZipFile(zp).read(n)))
        for part, rel, ref in items:
            local = os.path.join(tmp, f"f{k}")
            k += 1
            if inner is None:
                open(local, "wb").write(ref[1])
            else:
                with inner.open(ref[1]) as src, open(local, "wb") as dst:
                    while True:
                        b = src.read(16 * 2**20)
                        if not b:
                            break
                        dst.write(b)
            t = trees[part]
            full = IMG_ROOT[part] + rel
            parts = full.strip("/").split("/")
            for i in range(1, len(parts)):
                d = "/" + "/".join(parts[:i])
                if d not in t.nodes:
                    t.add(ext4write.Node(d, stat.S_IFDIR | 0o755, 0, 0, label))
            mode = ref[3] if len(ref) > 3 else 0o644
            t.add(ext4write.Node(full, stat.S_IFREG | mode, 0, 0, label, size=os.path.getsize(local),
                                 data=(lambda p=local: open(p, "rb").read())))
    log(f"  added {k} files")

    # 5. build.prop (product), exactly like Nik's set_prop: replace if the key appears, else append
    bp = trees["product"].nodes["/etc/build.prop"]
    text = (bp.data() if callable(bp.data) else bp.data).decode("utf-8")
    for key, v in props:
        if key in text:
            text = re.sub(rf"({re.escape(key)})=.*", lambda m: f"{m.group(1)}={v}", text)
        else:
            text += f"{key}={v}\n"
    new = text.encode("utf-8")
    trees["product"].add(ext4write.Node("/etc/build.prop", bp.mode, bp.uid, bp.gid, bp.xattrs,
                                        size=len(new), data=new))
    log(f"  build.prop: +{len(props)} props")

    for part, path, data, mode, lab in extras:
        trees[part].add(ext4write.Node(path, stat.S_IFREG | mode, 0, 0, {"security.selinux": lab.encode() + b"\0"},
                                       size=len(data), data=data))
        log(f"  DEVELOPER EXTRA: {part}{path}")

    # 6. write fresh images, sized to content
    result = {}
    for part, t in trees.items():
        out = os.path.join(a.out, part + ".img")
        log(f"{part}:")
        ext4write.write_image(t, out, log=lambda s: log(s))
        t._img.close()
        result[part] = out
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    log(f"BAKE OK ({time.time() - t0:.0f} s)")
    return result


if __name__ == "__main__":
    main()
