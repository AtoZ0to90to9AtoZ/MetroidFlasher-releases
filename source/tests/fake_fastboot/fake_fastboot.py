r"""A fake `fastboot` for MetroidFlasher's tests. It never talks to a phone.

It is driven by a folder named in the FAKE_FB_DIR environment variable:

  FAKE_FB_DIR\config.json   what to answer (written by the test)
      {
        "device": true,                       # show up in `fastboot devices`
        "vars": {"product": "sun", ...},      # getvar answers
        "device_info": "...",                 # text for `oem device-info`
        "super_path": "...\\super.bin",       # optional: apply `flash super` pieces to this file
        "fail": {"flash abl_a": "FAILED (remote: '...')",   # command prefix -> failure text
                 "flash super": {"msg": "FAILED (...)", "skip": 1}}   # ...or fail after N successes
      }
  FAKE_FB_DIR\commands.log  one JSON list per call, appended (the test reads it back)

`flash super <sparse file>` pieces are checked (magic, block size, total size) and their
RAW chunks are written into super_path, so a test can read the finished super back.
"""
import json
import os
import struct
import sys

SPARSE_MAGIC = 0xED26FF3A


def apply_raw(img, super_path, super_size):
    """A whole (non-sparse) super.img, as MetroidFlasher 0.7+ sends it: must be exactly super-sized."""
    if os.path.getsize(img) != super_size:
        return f"FAILED (remote: 'image size {os.path.getsize(img)} != super size {super_size}')"
    with open(img, "rb") as f, open(super_path, "r+b") as out:
        while True:
            buf = f.read(1 << 20)
            if not buf:
                break
            out.write(buf)
    return None


def apply_sparse(piece, super_path, super_size):
    with open(piece, "rb") as f:
        if struct.unpack("<I", f.read(4))[0] != SPARSE_MAGIC:
            return apply_raw(piece, super_path, super_size)
    with open(piece, "rb") as f, open(super_path, "r+b") as out:
        magic, _, _, fh, ch, blk, total, nch, _ = struct.unpack("<IHHHHIIII", f.read(28))
        if total * blk != super_size:
            return f"FAILED (remote: 'sparse size {total * blk} != super size {super_size}')"
        f.seek(fh)
        pos = 0
        for _ in range(nch):
            kind, _, blocks, tsz = struct.unpack("<HHII", f.read(ch)[:12])
            n = blocks * blk
            if kind == 0xCAC1:
                out.seek(pos)
                left = n
                while left:
                    buf = f.read(min(left, 1 << 20))
                    if not buf:
                        return "FAILED (remote: 'truncated RAW chunk')"
                    out.write(buf)
                    left -= len(buf)
            elif kind in (0xCAC2, 0xCAC4):
                f.seek(tsz - ch, 1)
                if kind == 0xCAC2:
                    return "FAILED (remote: 'fake does not apply FILL')"
            elif kind != 0xCAC3:
                return f"FAILED (remote: 'bad chunk {kind:#x}')"
            pos += n
        if pos != super_size:
            return f"FAILED (remote: 'chunks cover {pos} of {super_size} bytes')"
    return None


def main(argv):
    state = os.environ.get("FAKE_FB_DIR")
    if not state:
        print("fake_fastboot: FAKE_FB_DIR not set", file=sys.stderr)
        return 1
    with open(os.path.join(state, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    # one-phone lock (0.7+): every command is `-s SERIAL ...`. Only our serial answers; the log
    # records the command without the prefix so tests read the same lists as before.
    if argv[:1] == ["-s"] and len(argv) >= 2:
        if argv[1] != "FAKE0001" or not cfg.get("device", True):
            print(f"fastboot: error: device '{argv[1]}' not found")
            return 1
        argv = argv[2:]
    logpath = os.path.join(state, "commands.log")
    with open(logpath, "a", encoding="utf-8") as f:
        f.write(json.dumps(argv) + "\n")

    line = " ".join(argv)
    for prefix, rule in cfg.get("fail", {}).items():
        if not line.startswith(prefix):
            continue
        # rule: "message" (fail every time) or {"msg": "...", "skip": N} (succeed N times first)
        msg, skip = (rule, 0) if isinstance(rule, str) else (rule["msg"], rule.get("skip", 0))
        with open(logpath, encoding="utf-8") as f:
            seen = sum(1 for ln in f if " ".join(json.loads(ln)).startswith(prefix))
        if seen > skip:
            print(msg)
            return 1

    if argv[:1] == ["--version"]:
        print("fastboot version FAKE (MetroidFlasher tests)")
    elif argv[:1] == ["devices"]:
        if cfg.get("device", True):
            print("FAKE0001\t fastboot")  # real fastboot prints tab + space
    elif argv[:1] == ["getvar"] and len(argv) == 2:
        val = cfg.get("vars", {}).get(argv[1], "")
        print(f"{argv[1]}: {val}")
        print("Finished. Total time: 0.001s")
    elif argv[:2] == ["oem", "device-info"]:
        print(cfg.get("device_info", ""))
        print("OKAY [  0.001s]")
    elif argv[:1] == ["flash"] and len(argv) == 3:
        if not os.path.isfile(argv[2]):
            print(f"fastboot: error: cannot load '{argv[2]}'")
            return 1
        if argv[1] == "super" and cfg.get("super_path"):
            size = int(cfg["vars"]["partition-size:super"], 16)
            err = apply_sparse(argv[2], cfg["super_path"], size)
            if err:
                print(err)
                return 1
        print(f"Sending '{argv[1]}' (fake)")
        print("OKAY [  0.001s]")
        print(f"Writing '{argv[1]}'")
        print("OKAY [  0.001s]")
    elif argv[:1] == ["set_active"] and len(argv) == 2:
        # like a real phone: the next `getvar current-slot` reports the new slot
        cfg.setdefault("vars", {})["current-slot"] = argv[1]
        with open(os.path.join(state, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        print("OKAY [  0.001s]")
    elif argv[:1] in (["erase"], ["-w"], ["reboot"]):
        print("OKAY [  0.001s]")
    else:
        print(f"fake_fastboot: unknown command {argv}")
        return 1
    print("Finished. Total time: 0.001s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
