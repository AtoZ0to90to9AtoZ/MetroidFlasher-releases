"""buildsuper - pack a ROM's logical images into ONE flashable super.img, then verify it.

    py -3 buildsuper.py <folder of .img files> --out super.img [--slot b] [--super-size 0x240000000]

Writing one complete super image is a "super wipe" every time: the partition table is rebuilt from
scratch and holds only the target slot's partitions, so leftovers can't eat space.
Flash from the bootloader with:  fastboot flash super super.img   (fastboot splits it by itself).
"""
import argparse
import hashlib
import os
import sys

import lpsuper

GROUP = "qti_dynamic_partitions"
LOGICAL = ["system", "system_ext", "product", "vendor", "odm", "vendor_dlkm", "system_dlkm", "odm_dlkm"]
BUF = 16 * 1024 * 1024


def sha256_file(path, off=0, length=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(off)
        left = os.path.getsize(path) - off if length is None else length
        while left:
            b = f.read(min(BUF, left))
            if not b:
                raise IOError(f"{path}: short read")
            h.update(b)
            left -= len(b)
    return h.hexdigest()


def build(src, out, slot, super_size):
    parts = [(n, os.path.getsize(os.path.join(src, n + ".img")))
             for n in LOGICAL if os.path.isfile(os.path.join(src, n + ".img"))]
    if not parts:
        sys.exit(f"No logical images ({', '.join(LOGICAL)}) found in {src}")
    for n, size in parts:
        with open(os.path.join(src, n + ".img"), "rb") as f:
            if int.from_bytes(f.read(4), "little") == lpsuper.SPARSE_MAGIC:
                sys.exit(f"{n}.img is a sparse image; unsparse it first (simg2img)")
    layout = lpsuper.plan_layout(parts, super_size)
    meta = lpsuper.metadata_region(layout, super_size, slot, GROUP)
    lpsuper.parse_metadata_region(meta)

    used = layout[-1][1] + layout[-1][2]
    print(f"Layout for slot {slot}  (super {super_size / 2**30:.2f} GiB):")
    for name, off, size in layout:
        print(f"  {name}_{slot:<14} {size / 2**20:9.1f} MiB  at {off:#x}")
    print(f"  uses {used / 2**30:.2f} GiB, {(super_size - used) / 2**20:.0f} MiB free")

    regions = [(0, len(meta), lambda: [meta])]
    for name, off, size in layout:
        regions.append((off, size, lpsuper.file_reader(os.path.join(src, name + ".img"), 0, size, BUF)))
    print(f"Writing {out} ...")
    lpsuper.write_sparse_image(out, super_size, regions)
    return layout


def verify(src, out, slot, super_size, layout):
    """Re-read the finished file on its own and compare every byte region to the source images."""
    chunks = sorted(lpsuper.read_sparse_raw_chunks(out))

    def region_sha(off, size):
        """sha256 of super bytes [off, off+size), which must be fully covered by RAW chunks."""
        h = hashlib.sha256()
        pos = off
        with open(out, "rb") as f:
            for c_off, c_len, f_off in chunks:
                if c_off + c_len <= pos or c_off >= off + size:
                    continue
                if c_off > pos:
                    sys.exit(f"VERIFY FAILED: gap in the image at {pos:#x}")
                f.seek(f_off + (pos - c_off))
                left = min(c_off + c_len, off + size) - pos
                while left:
                    b = f.read(min(BUF, left))
                    h.update(b)
                    left -= len(b)
                    pos += len(b)
        if pos != off + size:
            sys.exit(f"VERIFY FAILED: image ends early at {pos:#x}")
        return h.hexdigest()

    c_off, length, foff = chunks[0]
    if c_off != 0:
        sys.exit("VERIFY FAILED: partition table is not at the start of super")
    with open(out, "rb") as f:
        f.seek(foff)
        table = lpsuper.parse_metadata_region(f.read(length))
    want = {f"{n}_{slot}": (off, size) for n, off, size in layout}
    got = {p[0]: (p[2], p[3]) for p in table["partitions"] if p[3]}
    if got != want or table["super_size"] != super_size:
        sys.exit(f"VERIFY FAILED: partition table in the image does not match the plan\n{got}\n{want}")
    print(f"  partition table OK ({table['copies']} copies, {len(got)} partitions)")
    for name, off, size in layout:
        a = sha256_file(os.path.join(src, name + ".img"))
        b = region_sha(off, size)
        if a != b:
            sys.exit(f"VERIFY FAILED: {name} bytes differ from {name}.img")
        print(f"  {name:<12} matches {name}.img  sha256 {a[:16]}...")
    print("VERIFY OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="folder holding system.img, vendor.img, ...")
    ap.add_argument("--out", required=True)
    ap.add_argument("--slot", choices=["a", "b"], default="b")
    ap.add_argument("--super-size", default="0x240000000", help="phone's super size (fastboot getvar partition-size:super)")
    a = ap.parse_args()
    size = int(a.super_size, 0)
    layout = build(a.src, a.out, a.slot, size)
    print("Verifying ...")
    verify(a.src, a.out, a.slot, size, layout)
    print(f"super.img sha256: {sha256_file(a.out)}")


if __name__ == "__main__":
    main()
