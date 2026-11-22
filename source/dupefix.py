#!/usr/bin/env python3
"""dupefix - one command: scan a ROM against your GApps and build the flashable removal zip.

For people who just want the result. Wraps appscan + debloatzip so you run ONE thing instead of two:

  python dupefix.py <rom> <gapps.zip> [more gapps...] [--out MetroidDebloat.zip] [--pick] [--report DIR]

  <rom>     a folder of partition images (system.img, product.img, ...), an already-unpacked ROM
            folder, or a manifests.json saved by appscan --save-manifests.
  <gapps>   one or more GApps packages (NikGapps / MindTheGapps / LiteGapps zips) or folders.

  --out     name of the zip to write           (default: MetroidDebloat.zip)
  --pick    open the picker window to tick/untick apps before building the zip
  --report  also save the full human-readable appscan report into this folder

It prints exactly what it will remove, then writes the zip. Flash that zip in recovery
(adb sideload) right AFTER your GApps zip, before the first boot.

Nothing here is phone- or ROM-specific: it decides what duplicates what by what each app DOES
(its manifest roles), and it refuses to touch anything on appscan's PROTECTED list, so it is safe
to point at any A/B ROM + any GApps package.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import appscan      # noqa: E402  (sibling scripts, must adjust sys.path first)
import debloatzip   # noqa: E402


def _take(argv, flag):
    """Pull '--flag value' out of argv, return the value or None."""
    if flag in argv:
        i = argv.index(flag)
        val = argv[i + 1]
        del argv[i:i + 2]
        return val
    return None


def main():
    argv = sys.argv[1:]
    pick = "--pick" in argv
    argv = [a for a in argv if a != "--pick"]
    out = _take(argv, "--out") or "MetroidDebloat.zip"
    report_dir = _take(argv, "--report")

    if len(argv) < 2:
        print(__doc__)
        sys.exit(1)
    rom, gapps_srcs = argv[0], argv[1:]

    # 1. scan the ROM --------------------------------------------------------
    print(f"[1/3] Scanning ROM: {rom}")
    rom_apps = appscan.apps_from_raw(appscan.load_rom(rom))
    print(f"      {len(rom_apps)} apps in the ROM")

    # 2. scan the GApps ------------------------------------------------------
    print(f"[2/3] Scanning GApps: {', '.join(os.path.basename(g) for g in gapps_srcs)}")
    g_items = []
    for g in gapps_srcs:
        if os.path.isdir(g):
            g_items += list(appscan.iter_apks_in_folder(g))
        else:
            with open(g, "rb") as fh:
                g_items += list(appscan.iter_apks_in_blob(os.path.basename(g), fh.read()))
    g_apps = appscan.apps_from_raw(appscan.scan_raw(g_items))
    print(f"      {len(g_apps)} apps in the GApps")

    conflicts, g_roles = appscan.find_conflicts(rom_apps, g_apps)
    report = {"rom": rom_apps, "gapps": g_apps, "conflicts": conflicts}
    remove = [c for c in conflicts if c["action"] == "remove"]
    ask = [c for c in conflicts if c["action"] == "ask"]
    print(f"      duplicates: {len(remove)} to remove, {len(ask)} to ask about")
    for c in remove:
        print(f"        - {c['package']}   ({os.path.dirname(c['path'])})")
    for c in ask:
        print(f"        ? {c['package']}   (ask first: {c['reason'][:70]})")

    if report_dir:
        os.makedirs(report_dir, exist_ok=True)
        with open(os.path.join(report_dir, "appscan.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
        print(f"      report saved: {os.path.join(report_dir, 'appscan.json')}")

    # 3. build the flashable zip --------------------------------------------
    print("[3/3] Building removal zip")
    sel = debloatzip.selection_from_report(report, pick)
    if not sel:
        print("      nothing selected - no zip made.")
        return
    paths = sorted({os.path.dirname(c["path"]) for c in sel})
    debloatzip.build_zip(paths, out)

    print(f"\nDone. {len(paths)} app folder(s) will be removed on flash:")
    for p in paths:
        print(f"  {p}")
    print(f"\nZip: {os.path.abspath(out)}")
    print("Flash it in recovery (adb sideload) right AFTER your GApps zip, before the first boot.")


if __name__ == "__main__":
    main()
