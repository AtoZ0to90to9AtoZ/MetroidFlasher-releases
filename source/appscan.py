#!/usr/bin/env python3
"""appscan - find which ROM apps a GApps package would duplicate, by what the apps DO.

Reads AndroidManifest.xml (binary AXML) from every APK in a ROM (extracted folder, or zip/image unpacked by
the caller) and in a GApps package (any zip: NikGapps, MindTheGapps, LiteGapps...; nested zips/tar.xz are opened),
assigns each app "roles" (keyboard, dialer, launcher, ...), and reports ROM apps whose role is also provided by the
GApps package. Output: a readable report plus a debloat list (paths) usable for NikGapps' debloater.config or for
post-boot `pm uninstall -k --user 0`.

  python appscan.py <rom folder> <gapps zip or folder> [more gapps zips...] [--out report_dir]
"""
import io
import json
import lzma
import os
import struct
import sys
import tarfile
import zipfile

# ----------------------------------------------------------------------------- binary AXML

RES_STRING_POOL = 0x0001
RES_XML = 0x0003
RES_XML_START_ELEMENT = 0x0102
TYPE_STRING = 0x03


def _read_string_pool(buf, off):
    _, header_size, size = struct.unpack_from("<HHI", buf, off)
    count, _style_count, flags, strings_start, _styles_start = struct.unpack_from("<IIIII", buf, off + 8)
    utf8 = bool(flags & 0x100)
    offsets = struct.unpack_from(f"<{count}I", buf, off + header_size)
    base = off + strings_start
    out = []
    for o in offsets:
        p = base + o
        try:
            if utf8:
                n = buf[p]
                p += 2 if n & 0x80 else 1
                n = buf[p]
                if n & 0x80:
                    n = ((n & 0x7F) << 8) | buf[p + 1]
                    p += 2
                else:
                    p += 1
                out.append(buf[p:p + n].decode("utf-8", "replace"))
            else:
                n = struct.unpack_from("<H", buf, p)[0]
                p += 2
                if n & 0x8000:
                    n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", buf, p)[0]
                    p += 2
                out.append(buf[p:p + n * 2].decode("utf-16-le", "replace"))
        except Exception:
            out.append("")
    return out, off + size


def parse_axml(buf):
    """Yield (depth, tag, {attr: value}) for every start element."""
    typ, hsize, _ = struct.unpack_from("<HHI", buf, 0)
    if typ != RES_XML:
        return
    strings = []
    off = hsize
    depth = 0
    while off + 8 <= len(buf):
        ctype, chsize, csize = struct.unpack_from("<HHI", buf, off)
        if csize == 0:
            break
        if ctype == RES_STRING_POOL:
            strings, _ = _read_string_pool(buf, off)
        elif ctype == RES_XML_START_ELEMENT:
            ext = off + chsize
            _ns, name, attr_start, attr_size, attr_count = struct.unpack_from("<iiHHH", buf, ext)
            tag = strings[name] if 0 <= name < len(strings) else ""
            attrs = {}
            a = ext + attr_start
            for i in range(attr_count):
                _ans, aname, raw, _sz, _r, dtype, data = struct.unpack_from("<iiiHBBI", buf, a + i * attr_size)
                key = strings[aname] if 0 <= aname < len(strings) else ""
                if raw >= 0 and raw < len(strings):
                    val = strings[raw]
                elif dtype == TYPE_STRING and data < len(strings):
                    val = strings[data]
                else:
                    val = data
                attrs[key] = val
            depth += 1
            yield depth, tag, attrs
        elif ctype == 0x0103:  # end element
            depth -= 1
        off += csize


# ----------------------------------------------------------------------------- roles

def manifest_info(apk_bytes):
    with zipfile.ZipFile(io.BytesIO(apk_bytes)) as z:
        try:
            buf = z.read("AndroidManifest.xml")
        except KeyError:
            return None
    info = {"package": None, "filters": [], "service_perms": set(), "uses_perms": set()}
    cur = None
    for _d, tag, at in parse_axml(buf):
        if tag == "manifest":
            info["package"] = at.get("package")
        elif tag in ("activity", "activity-alias", "service", "receiver", "provider"):
            if tag == "service" and isinstance(at.get("permission"), str):
                info["service_perms"].add(at["permission"])
            cur = {"kind": tag, "actions": set(), "categories": set(), "mimes": set(), "schemes": set(), "hosts": set()}
        elif tag == "intent-filter" and cur is not None:
            cur = {"kind": cur["kind"], "actions": set(), "categories": set(), "mimes": set(), "schemes": set(), "hosts": set()}
            info["filters"].append(cur)
        elif cur is not None and tag == "action" and isinstance(at.get("name"), str):
            cur["actions"].add(at["name"])
        elif cur is not None and tag == "category" and isinstance(at.get("name"), str):
            cur["categories"].add(at["name"])
        elif cur is not None and tag == "data":
            for k, s in (("mimeType", "mimes"), ("scheme", "schemes"), ("host", "hosts")):
                if isinstance(at.get(k), str):
                    cur[s].add(at[k])
        elif tag == "original-package" and isinstance(at.get("name"), str):
            info.setdefault("original", set()).add(at["name"])
        elif tag == "uses-permission" and isinstance(at.get("name"), str):
            info["uses_perms"].add(at["name"])
    return info


CORE_PACKAGES = {
    "gms_core": {"com.google.android.gms", "org.microg.gms", "com.google.android.gsf", "com.google.android.gsf.login"},
    "play_store": {"com.android.vending", "org.microg.vending", "com.aurora.store"},
    "webview": {"com.android.webview", "com.google.android.webview", "org.lineageos.webview"},
}


def roles_for(info):
    r = set()
    pkg = info["package"] or ""
    for role, pkgs in CORE_PACKAGES.items():
        if pkg in pkgs:
            r.add(role)
    if "android.permission.BIND_INPUT_METHOD" in info["service_perms"]:
        r.add("keyboard")
    for f in info["filters"]:
        a, c, m, s, h = f["actions"], f["categories"], f["mimes"], f["schemes"], f["hosts"]
        if "android.view.InputMethod" in a:
            r.add("keyboard")
        if "android.intent.category.HOME" in c and "android.intent.action.MAIN" in a:
            r.add("launcher")
        if "android.intent.action.DIAL" in a and ("tel" in s or not s) and f["kind"] != "service":
            r.add("dialer")
        if "android.provider.Telephony.SMS_DELIVER" in a:
            r.add("sms")
        if "android.intent.category.APP_BROWSER" in c and f["kind"] != "service":
            r.add("browser")
        if {"android.media.action.STILL_IMAGE_CAMERA", "android.media.action.IMAGE_CAPTURE"} & a:
            r.add("camera")
        cat_roles = {
            "android.intent.category.APP_GALLERY": "gallery",
            "android.intent.category.APP_CONTACTS": "contacts",
            "android.intent.category.APP_CALENDAR": "calendar",
            "android.intent.category.APP_CALCULATOR": "calculator",
            "android.intent.category.APP_MUSIC": "music",
            "android.intent.category.APP_EMAIL": "email",
            "android.intent.category.APP_MAPS": "maps",
            "android.intent.category.APP_MESSAGING": "messaging",
            "android.intent.category.APP_FILES": "files",
        }
        for cat, role in cat_roles.items():
            if cat in c and f["kind"] != "service":
                r.add(role)
        if "android.intent.action.SET_ALARM" in a:
            r.add("clock")
        if "android.intent.action.OPEN_DOCUMENT" in a and "android.intent.category.OPENABLE" not in c and \
                pkg.endswith("documentsui"):
            r.add("file_picker")
        if "android.intent.category.SETUP_WIZARD" in c or any(x.startswith("com.android.setupwizard.") for x in a) \
                or "com.google.android.setupwizard.HOME" in a:
            r.add("setup_wizard")
        if "android.intent.action.TTS_SERVICE" in a:
            r.add("tts")
        if "android.provider.MediaStore.RECORD_SOUND" in a:
            r.add("recorder")
        if "android.os.storage.action.MANAGE_STORAGE" in a:
            r.add("storage_manager")
        if "android.intent.action.EDIT" in a and any(x.startswith("image/") for x in m) and f["kind"] != "service":
            r.add("image_editor")
        if "com.android.camera.action.REVIEW" in a:
            r.add("photo_review")
    if pkg.endswith(".documentsui"):
        r.add("file_picker")
    # Apps that answer HOME only as a fallback or during setup are not launchers.
    if "setup_wizard" in r or pkg in NOT_A_LAUNCHER:
        r.discard("launcher")
    # Packages that register helper services for a role but are not that app.
    for role, pkgs in ROLE_HELPERS.items():
        if pkg in pkgs:
            r.discard(role)
    return r


NOT_A_LAUNCHER = {"android", "com.android.settings", "com.android.managedprovisioning", "com.google.android.gms",
                  "com.google.android.apps.restore", "com.google.android.apps.work.clouddpc",
                  "com.android.provision", "com.android.tv.settings", "com.google.android.gms.supervision"}
ROLE_HELPERS = {
    "keyboard": {"com.google.android.gms", "com.google.android.googlequicksearchbox", "com.google.android.marvin.talkback",
                 "com.google.android.tts", "com.android.talkback"},
}
# Never offered for removal: the phone does not boot or set up without them.
PROTECTED = {"android", "com.android.settings", "com.android.systemui", "com.android.phone", "com.android.server.telecom",
             "com.android.providers.settings", "com.android.permissioncontroller", "com.android.packageinstaller",
             "com.android.shell", "com.android.managedprovisioning", "com.android.networkstack",
             # eSIM stack: must never be removed (user, 2026-11-14)
             "com.android.euicc.partner", "org.lineageos.euicc", "com.google.android.euicc"}


# ----------------------------------------------------------------------------- walking sources

def iter_apks_in_folder(root):
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if f.lower().endswith(".apk"):
                p = os.path.join(dp, f)
                with open(p, "rb") as fh:
                    yield os.path.relpath(p, root).replace("\\", "/"), fh.read()


def nikgapps_skips(z):
    """App sets / packages a NikGapps zip will NOT install, from its own afzc/nikgapps.config (UseZipConfig=1).
    Without UseZipConfig=1 the installer reads /sdcard instead, which we can't see: assume everything installs."""
    try:
        text = z.read("afzc/nikgapps.config").decode("utf-8", "replace")
    except KeyError:
        return set(), set()
    vals = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip()
    if vals.get("UseZipConfig") != "1":
        return set(), set()
    sets = {k for k, v in vals.items() if v == "0" and not k.startswith(">>")}
    pkgs = {k[2:] for k, v in vals.items() if v == "0" and k.startswith(">>")}
    return sets, pkgs


def iter_apks_in_blob(name, data, depth=0):
    """Open zips, tar(.xz), and nested archives; yield (path, apk bytes)."""
    if depth > 4:
        return
    low = name.lower()
    if low.endswith(".apk"):
        yield name, data
        return
    try:
        if low.endswith((".tar.xz", ".txz")):
            data = lzma.decompress(data)
            low = low[:-3]
        if low.endswith(".tar"):
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                for m in t.getmembers():
                    if m.isfile():
                        yield from iter_apks_in_blob(f"{name}!{m.name}", t.extractfile(m).read(), depth + 1)
            return
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                skip_sets, skip_pkgs = nikgapps_skips(z) if depth == 0 else (set(), set())
                for n in z.namelist():
                    parts = n.split("/")
                    if len(parts) >= 3 and parts[0] == "AppSet" and (
                            parts[1] in skip_sets or os.path.splitext(parts[2])[0] in skip_pkgs):
                        print(f"  (config skips {parts[1]}/{parts[2]})", file=sys.stderr)
                        continue
                    if n.lower().endswith((".apk", ".zip", ".tar", ".tar.xz", ".txz")):
                        yield from iter_apks_in_blob(f"{name}!{n}", z.read(n), depth + 1)
    except Exception as e:
        print(f"  ! could not open {name}: {e}", file=sys.stderr)


def norm_path(path):
    """system.img unpacks as system/system/... (system-as-root); on the phone that is /system/..."""
    p = path.replace("\\", "/").lstrip("/")
    if p.startswith("system/system/"):
        p = p[len("system/"):]
    return "/" + p


def _to_json(info):
    return {"package": info["package"], "service_perms": sorted(info["service_perms"]),
            "uses_perms": sorted(info["uses_perms"]), "original": sorted(info.get("original", ())),
            "filters": [{k: (sorted(v) if isinstance(v, set) else v) for k, v in f.items()} for f in info["filters"]]}


def _from_json(d):
    return {"package": d["package"], "service_perms": set(d["service_perms"]), "uses_perms": set(d["uses_perms"]),
            "filters": [{k: (set(v) if isinstance(v, list) else v) for k, v in f.items()} for f in d["filters"]]}


def scan_raw(items):
    """Parse manifests once; the result is saved so later scans don't need the images."""
    raw = []
    for path, data in items:
        try:
            info = manifest_info(data)
        except Exception as e:
            print(f"  ! {path}: {e}", file=sys.stderr)
            continue
        if info and info["package"]:
            raw.append({"path": norm_path(path), **_to_json(info)})
    return raw


def apps_from_raw(raw):
    return [{"path": r["path"], "package": r["package"], "roles": sorted(roles_for(_from_json(r))),
             "original": r.get("original", [])} for r in raw]


APP_PARTITIONS = ("system", "system_ext", "product", "odm", "vendor")


def find_7zip():
    import shutil
    for p in (os.environ.get("METROID_7Z"), shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe",
              "/usr/bin/7z", "/usr/local/bin/7z", "/opt/homebrew/bin/7z"):
        if p and os.path.isfile(p):
            return p
    raise SystemExit("7-Zip (7z) is needed to read .img files; install it or pass an unpacked folder")


def iter_apks_in_images(folder):
    """Read APKs straight out of a ROM's partition images (ext4/erofs), so each path carries its real partition."""
    import subprocess
    import tempfile
    sz = find_7zip()
    for part in APP_PARTITIONS:
        img = os.path.join(folder, part + ".img")
        if not os.path.isfile(img):
            continue
        with tempfile.TemporaryDirectory(prefix=f"appscan_{part}_") as tmp:
            r = subprocess.run([sz, "x", img, f"-o{tmp}", "-r", "*.apk", "-y", "-bso0", "-bsp0"],
                               capture_output=True, text=True)
            if r.returncode not in (0, 1):  # 1 = warnings (e.g. symlinks), still usable
                print(f"  ! 7z could not read {part}.img: {r.stderr.strip()[:200]}", file=sys.stderr)
                continue
            n = 0
            for rel, data in iter_apks_in_folder(tmp):
                if part == "system" and rel.startswith("system/"):
                    rel = rel[len("system/"):]          # system-as-root image: /system/... on the phone
                if part != "system" and rel.startswith(part + "/"):
                    rel = rel[len(part) + 1:]
                n += 1
                yield f"{part}/{rel}", data
            print(f"  {part}.img: {n} APKs")


def load_rom(src):
    """A ROM image folder (system.img, product.img, ...), a folder of unpacked partitions, or a manifests.json
    saved by --save-manifests."""
    if src.lower().endswith(".json"):
        with open(src, encoding="utf-8") as f:
            return json.load(f)
    if any(os.path.isfile(os.path.join(src, p + ".img")) for p in APP_PARTITIONS):
        return scan_raw(iter_apks_in_images(src))
    return scan_raw(iter_apks_in_folder(src))


ASK_ROLES = {"launcher": "removing the ROM launcher can break the recents screen unless the new launcher is the "
                         "ROM's recents provider (Pixel Launcher needs PixelSpecifics/overlays)",
             "keyboard": "keep the ROM keyboard as a fallback: if the GApps keyboard crashes (seen with Gboard on "
                         "crDroid, 2026-11-14) the phone has no way to type",
             "photo_review":"the ROM camera may open its own photo-review app after a shot; removing it can "
                             "break the camera's thumbnail button"}


def find_conflicts(rom_apps, g_apps):
    g_roles = {}
    for a in g_apps:
        for r in a["roles"]:
            g_roles.setdefault(r, set()).add(a["package"])
    g_by_pkg = {a["package"]: a for a in g_apps}
    # Folder names the GApps install into (NikGapps: "___priv-app___Name/x.apk", MTG: ".../priv-app/Name/x.apk").
    # A ROM folder with the same name gets overwritten in place, so deleting it afterwards would delete Google's app.
    g_dirs = {os.path.basename(os.path.dirname(a["path"].split("!")[-1])).split("___")[-1].lower() for a in g_apps}
    out = []
    for a in rom_apps:
        if a["package"] in PROTECTED:
            continue
        if os.path.basename(os.path.dirname(a["path"])).lower() in g_dirs:
            continue
        renamer = next((g for g in g_apps if a["package"] in g.get("original", ())), None)
        if renamer:
            out.append({**a, "action": "remove",
                        "reason": f"{renamer['package']} declares <original-package {a['package']}>: if this ROM app "
                                  f"is present, Android renames the GApps app to the old name and it crashes "
                                  f"(Gboard on crDroid, 2026-11-14). Must be removed before first boot"})
            continue
        if a["package"] in g_by_pkg:
            out.append({**a, "action": "remove",
                        "reason": "same package name as the GApps app but a different build/signature "
                                  "(e.g. microG vs Google Play services): two copies conflict"})
            continue
        overlap = [r for r in a["roles"] if r in g_roles]
        if not overlap:
            continue
        why = ", ".join(f"{r} -> {'/'.join(sorted(g_roles[r]))}" for r in overlap)
        ask = [r for r in overlap if r in ASK_ROLES]
        if ask and len(ask) == len(overlap):
            out.append({**a, "action": "ask", "reason": f"same role: {why}. {ASK_ROLES[ask[0]]}"})
        else:
            out.append({**a, "action": "remove", "reason": f"same role: {why}"})
    return out, g_roles


def main():
    argv = sys.argv[1:]
    opts = {}
    for flag in ("--out", "--save-manifests"):
        if flag in argv:
            i = argv.index(flag)
            opts[flag] = argv[i + 1]
            del argv[i:i + 2]
    out_dir = opts.get("--out", "appscan_report")
    rom, gapps_srcs = argv[0], argv[1:]
    print(f"Scanning ROM: {rom}")
    raw = load_rom(rom)
    if "--save-manifests" in opts:
        os.makedirs(os.path.dirname(os.path.abspath(opts["--save-manifests"])), exist_ok=True)
        with open(opts["--save-manifests"], "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=0)
        print(f"  manifests saved: {opts['--save-manifests']}")
    rom_apps = apps_from_raw(raw)
    print(f"  {len(rom_apps)} apps")
    if not gapps_srcs:
        return
    g_items = []
    for g in gapps_srcs:
        if os.path.isdir(g):
            g_items += list(iter_apks_in_folder(g))
        else:
            with open(g, "rb") as fh:
                g_items += list(iter_apks_in_blob(os.path.basename(g), fh.read()))
    print(f"Scanning GApps: {len(gapps_srcs)} package(s)")
    g_apps = apps_from_raw(scan_raw(g_items))
    print(f"  {len(g_apps)} apps")

    conflicts, g_roles = find_conflicts(rom_apps, g_apps)
    remove = [c for c in conflicts if c["action"] == "remove"]
    ask = [c for c in conflicts if c["action"] == "ask"]

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "appscan.json"), "w", encoding="utf-8") as f:
        json.dump({"rom": rom_apps, "gapps": g_apps, "conflicts": conflicts}, f, indent=1)
    with open(os.path.join(out_dir, "debloat_paths.txt"), "w", encoding="utf-8") as f:
        f.writelines(os.path.dirname(c["path"]) + "\n" for c in remove)
    with open(os.path.join(out_dir, "debloat_packages.txt"), "w", encoding="utf-8") as f:
        f.writelines(c["package"] + "\n" for c in remove)
    with open(os.path.join(out_dir, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write(f"# appscan report\n\nROM: `{rom}` ({len(rom_apps)} apps)  \n"
                f"GApps: {', '.join(os.path.basename(g) for g in gapps_srcs)} ({len(g_apps)} apps)\n\n")
        for title, rows in (("Remove (duplicates a GApps app)", remove), ("Ask the user first", ask)):
            f.write(f"## {title}\n\n| ROM app | package | why |\n|---|---|---|\n")
            f.writelines(f"| `{c['path']}` | {c['package']} | {c['reason']} |\n" for c in rows)
            f.write("\n")
        f.write("## Roles the GApps provide\n\n")
        f.writelines(f"- **{r}**: {', '.join(sorted(p))}\n" for r, p in sorted(g_roles.items()))
    print(f"\nRemove: {len(remove)}   Ask first: {len(ask)}")
    for c in remove + ask:
        print(f"  [{c['action']}] {c['path']}  ({c['package']})  {c['reason'][:110]}")
    print(f"\nReport: {os.path.join(out_dir, 'REPORT.md')}")


if __name__ == "__main__":
    main()
