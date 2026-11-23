"""gapps - the GApps packages MetroidFlasher can bake into a ROM, and each ROM's removal map.

The ONLY combinations MetroidFlasher builds (author, final 2026-11-23):
  Core or Basic + exactly ONE setup wizard add-on (Google's SetupWizard, or PixelSetupWizard)
  Omni on its own
  Stock on its own
  MindTheGapps on its own (another maintainer's set; added 2026-11-22)
No other add-ons, ever. Customize only takes apps OUT. Anything more: sideload it yourself after the flash.
  core  = NikGapps Basic's AppSet/Core (Play Services, Play Store, GSF) + a setup wizard add-on
  basic = NikGapps Basic + a setup wizard add-on (Core and Basic have no setup wizard of their own)
  core-pixel / basic-pixel = the same with PixelSetupWizard (Google's wizard + the Pixel screens + Restore)
  stock = NikGapps Stock (only fits because every flash rewrites super, and images are sized to content)
All four were flashed and booted on crDroid 13.0 (20261111), 2026-11-20/21.

Zips are found by SHA-256 (any file name, anywhere under the folder given), so a renamed or different
download can never be baked by mistake.
"""
import hashlib
import os

ZIPS = {   # sha256 -> (key, description)
    "bb8ce77643bc853c7d6aa9ebb4e733f558a1427b92c9521b7d60d8c31018e7f7": ("omni", "NikGapps-omni-arm64-17-20261110-signed.zip"),
    "e917fb2cbcc56646d40589a7afc2221300e34beb2db5bc85e611ad7fa564ba93": ("stock", "NikGapps-stock-arm64-17-20261110-signed.zip"),
    "b16150b82e7a5ac62eda8fc254c8676c0e451d8aecd05f3cbc432282f479f7d4": ("basic", "NikGapps-basic-arm64-17-20261110-signed.zip"),
    "ad6ed440431c9c0020e6a22eb21bd6458b3804116240830971a09a6c57688635": ("setupwizard", "NikGapps-Addon-17-SetupWizard-20261110-signed.zip"),
    "6a0fe590484796241c305fb6c94e2b249d1bce27c8112698c68e4f4131ca8a3b": ("pixelsetupwizard", "NikGapps-Addon-17-PixelSetupWizard-20261110-signed.zip"),
    "a6844eeb2613541558237e467b20feb01f8b45b2ebab0109834140ea59b6ca4d": ("mtg", "MindTheGapps-17.0.0-arm64-20261022_132445.zip"),
}
PACKAGES = {   # package -> [(zip key, AppSet prefix or None)]
    "core": [("basic", "AppSet/Core/"), ("setupwizard", None)],
    "basic": [("basic", None), ("setupwizard", None)],
    "core-pixel": [("basic", "AppSet/Core/"), ("pixelsetupwizard", None)],
    "basic-pixel": [("basic", None), ("pixelsetupwizard", None)],
    "omni": [("omni", None)],
    "stock": [("stock", None)],
    "mindthegapps": [("mtg", None)],
}
ORDER = ["core", "core-pixel", "basic", "basic-pixel", "omni", "stock", "mindthegapps"]
NAMES = {"core": "Core", "core-pixel": "Core + Pixel setup", "basic": "Basic", "basic-pixel": "Basic + Pixel setup",
         "omni": "Omni", "stock": "Stock", "mindthegapps": "MindTheGapps"}
KEY_BY_NAME = {v: k for k, v in NAMES.items()}
WHAT = {"core": "Google Play only, Google's setup wizard", "core-pixel": "Google Play only, Pixel setup wizard",
        "basic": "+ Dialer, Contacts, Messages, Clock, Google's setup wizard",
        "basic-pixel": "+ Dialer, Contacts, Messages, Clock, Pixel setup wizard", "omni": "the everyday set",
        "stock": "the most Google apps", "mindthegapps": "the MindTheGapps set (Play, Google app, sync adapters)"}


def is_gapps_zip_name(f):
    f = f.lower()
    return f.endswith(".zip") and ("nikgapps" in f or "mindthegapps" in f)

# Per-ROM removal maps. "always": ROM paths every package removes (microG's pieces - every package brings
# Google's real Play Services). Everything else comes out only when the matching Google app goes in (each
# package's own folder names, handled by the bake). "extra": proven per-package additions.
# Every path must exist, or the bake STOPS: that is how a changed ROM build gets caught.
ROMS = {
    "crdroid-metroid": {
        "name": "crDroid (Nothing Phone 3)",
        "match": {"ro.crdroid.device": "metroid"},
        "always": ["/product/app/GsfProxy", "/product/app/MicrogCompanion"],
        "extra": {"omni": ["/product/app/Canvas"], "stock": ["/product/app/Canvas"]},
    },
}


def sha256(path, bufsize=16 * 2**20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(bufsize), b""):
            h.update(chunk)
    return h.hexdigest()


def find_zips(folder, log=print):
    """{zip key: path} for every known GApps zip under folder (searched two levels deep)."""
    found = {}
    for root, dirs, files in os.walk(folder):
        if root[len(folder):].count(os.sep) > 2:
            dirs[:] = []
            continue
        for f in files:
            if is_gapps_zip_name(f):
                p = os.path.join(root, f)
                key = ZIPS.get(sha256(p), (None,))[0]
                if key and key not in found:
                    found[key] = p
    return found


def specs_for(package, zips):
    """The --gapps list for bakewin: 'path' or 'path::AppSet/Core/'."""
    if package not in PACKAGES:
        raise ValueError(f"unknown GApps package '{package}' (choose {', '.join(ORDER)})")
    out = []
    for key, only in PACKAGES[package]:
        if key not in zips:
            raise FileNotFoundError(f"GApps package '{package}' needs {ZIPS_BY_KEY[key]} - not found")
        out.append(zips[key] + (f"::{only}" if only else ""))
    return out


ZIPS_BY_KEY = {k: d for _, (k, d) in ZIPS.items()}

# Apps Customize can't leave out: Google Play itself, and the setup wizard (skipping Google's setup wizard
# while Play services installs hangs the phone - learned 2026-11-14)
LOCKED = {"GmsCore", "GooglePlayStore", "GoogleServicesFramework", "SetupWizard", "SetupWizardPixel", "PixelSetupWizard",
          "GooglePlayServices", "Phonesky"}      # the last two: MindTheGapps' names


def find_zips_cached(folder, cache_path):
    """find_zips, but a zip whose name, size and date are unchanged isn't hashed again (2 GB of zips)."""
    import json
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    found, new_cache = {}, {}
    for root, dirs, files in os.walk(folder):
        if root[len(folder):].count(os.sep) > 2:
            dirs[:] = []
            continue
        for f in files:
            if not is_gapps_zip_name(f):
                continue
            p = os.path.join(root, f)
            st = os.stat(p)
            tag = f"{st.st_size}:{int(st.st_mtime)}"
            sha = cache.get(p, {}).get("sha") if cache.get(p, {}).get("tag") == tag else None
            sha = sha or sha256(p)
            new_cache[p] = {"tag": tag, "sha": sha}
            key = ZIPS.get(sha, (None,))[0]
            if key and key not in found:
                found[key] = p
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        json.dump(new_cache, open(cache_path, "w", encoding="utf-8"), indent=1)
    except OSError:
        pass
    return found


RULE = ("MetroidFlasher only builds: Core or Basic + ONE setup wizard (SetupWizard or PixelSetupWizard), "
        "Omni alone, Stock alone, or MindTheGapps alone. Anything else: sideload it yourself after the flash.")


def scan_zips(folder, cache_path=None):
    """[(path, zip key or None)] for EVERY .zip under folder (two levels deep), hashed once and cached by
    name/size/date, so nothing in the folder is ever silently ignored."""
    import json
    cache = {}
    if cache_path:
        try:
            cache = json.load(open(cache_path, encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    out, new_cache = [], {}
    for root, dirs, files in os.walk(folder):
        if root[len(folder):].count(os.sep) > 2:
            dirs[:] = []
            continue
        for f in sorted(files):
            if not f.lower().endswith(".zip"):
                continue
            p = os.path.join(root, f)
            st = os.stat(p)
            tag = f"{st.st_size}:{int(st.st_mtime)}"
            c = cache.get(p, {})
            sha = (c.get("sha") if c.get("tag") == tag else None) or sha256(p)
            new_cache[p] = {"tag": tag, "sha": sha}
            out.append((p, ZIPS.get(sha, (None,))[0]))
    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            json.dump(new_cache, open(cache_path, "w", encoding="utf-8"), indent=1)
        except OSError:
            pass
    return out


def zips_from_scan(scan):
    """{zip key: path} - the first copy of each known zip (same as find_zips)."""
    found = {}
    for p, key in scan:
        if key and key not in found:
            found[key] = p
    return found


def zip_verdicts(scan, package=None):
    """[(file name, used, why)] from scan_zips' list. package: the chosen package (which zips it uses)."""
    want = {k for k, _ in PACKAGES.get(package, [])}
    out, seen = [], set()
    for p, key in scan:
        f = os.path.basename(p)
        low = f.lower()
        if key is None:
            if "addon" in low:
                why = "NOT USED: add-ons aren't baked (only a setup wizard, with Core/Basic). Sideload it after the flash."
            elif "nikgapps-core" in low:
                why = "NOT USED: pick Core instead (MetroidFlasher makes it from the Basic zip)."
            elif "nikgapps" in low or "mindthegapps" in low:
                why = "NOT USED: not a known build of a supported package (different date or variant)."
            else:
                why = "NOT USED: not a GApps zip MetroidFlasher knows."
            out.append((f, False, why))
        elif key in seen:
            out.append((f, False, "NOT USED: same zip as another one here."))
        else:
            seen.add(key)
            if package is None or key in want:
                out.append((f, True, "used" if package else "OK"))
            else:
                out.append((f, False, f"not used by {NAMES.get(package, package)}."))
    return out


def rom_map(build_props, package):
    """Removal list for this ROM + package. build_props: {key: value} from the ROM's system build.prop."""
    for key, m in ROMS.items():
        if all(build_props.get(k) == v for k, v in m["match"].items()):
            return key, m["always"] + m["extra"].get(package, [])
    raise LookupError("MetroidFlasher has no GApps removal map for this ROM yet - flash it without GApps, "
                      "or ask for the ROM to be mapped.")
