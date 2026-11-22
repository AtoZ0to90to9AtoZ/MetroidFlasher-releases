"""gapps - the GApps packages MetroidFlasher can bake into a ROM, and each ROM's removal map.

Packages (author, 2026-11-21): Core, Basic, Omni, Stock. Stock is the maximum - no add-ons on top.
  core  = NikGapps Basic's AppSet/Core (Play Services, Play Store, GSF) + the SetupWizard add-on
  basic = NikGapps Basic + the SetupWizard add-on (Core and Basic have no setup wizard of their own)
  omni  = NikGapps Omni
  stock = NikGapps Stock (only fits because every flash rewrites super, and images are sized to content)
  mindthegapps = MindTheGapps (another maintainer's set; added 2026-11-22)
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
    "a6844eeb2613541558237e467b20feb01f8b45b2ebab0109834140ea59b6ca4d": ("mtg", "MindTheGapps-17.0.0-arm64-20261022_132445.zip"),
}
PACKAGES = {   # package -> [(zip key, AppSet prefix or None)]
    "core": [("basic", "AppSet/Core/"), ("setupwizard", None)],
    "basic": [("basic", None), ("setupwizard", None)],
    "omni": [("omni", None)],
    "stock": [("stock", None)],
    "mindthegapps": [("mtg", None)],
}
ORDER = ["core", "basic", "omni", "stock", "mindthegapps"]
NAMES = {"core": "Core", "basic": "Basic", "omni": "Omni", "stock": "Stock", "mindthegapps": "MindTheGapps"}
WHAT = {"core": "Google Play only", "basic": "+ Dialer, Contacts, Messages, Clock", "omni": "the everyday set",
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
LOCKED = {"GmsCore", "GooglePlayStore", "GoogleServicesFramework", "SetupWizard",
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


def rom_map(build_props, package):
    """Removal list for this ROM + package. build_props: {key: value} from the ROM's system build.prop."""
    for key, m in ROMS.items():
        if all(build_props.get(k) == v for k, v in m["match"].items()):
            return key, m["always"] + m["extra"].get(package, [])
    raise LookupError("MetroidFlasher has no GApps removal map for this ROM yet - flash it without GApps, "
                      "or ask for the ROM to be mapped.")
