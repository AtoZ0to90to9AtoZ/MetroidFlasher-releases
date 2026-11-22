#!/usr/bin/env python3
r"""MetroidFlasher GUI - a window over the same engine the menu (metroid.py) uses.

No flashing logic lives here. The buttons call metroid.py's jobs (which call metroid_flasher.py),
with metroid.GUI = True so the jobs never wait for keyboard input; this window asks its own
questions first (dry run first, then a typed FLASH confirmation).

Folder layout is the same as the menu version (next to the exe):
  1-STOCK  2-ROM  3-GAPPS  4-BACKUPS  tool\platform-tools (downloaded on first run)
"""
import contextlib
import io
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

import icon
import gapps
import metroid
import metroid_flasher

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
TITLE = f"MetroidFlasher {metroid.APP_VERSION}"
BANNER = (
    '   __  __   ___   _____   ___    ___    ___   ___\n'
    '  |  \\/  | | __| |_   _| | _ \\  / _ \\  |_ _| |   \\\n'
    '  | |\\/| | | _|    | |   |   / | (_) |  | |  | |) |\n'
    '  |_|  |_| |___|   |_|   |_|_\\  \\___/  |___| |___/\n'
    '      ___   _        _     ___   _  _   ___   ___\n'
    '     | __| | |      /_\\   / __| | || | | __| | _ \\\n'
    '     | _|  | |__   / _ \\  \\__ \\ | __ | | _|  |   /\n'
    '     |_|   |____| /_/ \\_\\ |___/ |_||_| |___| |_|_\\\n'
    '   >> unbrick . custom ROM . GApps  -  straight from the bootloader <<\n'
)

if os.name == "nt":
    # The engine starts fastboot/adb with plain subprocess calls; in a windowed app each one would
    # flash a black console window. Give every child process CREATE_NO_WINDOW instead.
    _Popen_init = subprocess.Popen.__init__

    def _silent_init(self, *args, **kw):
        kw["creationflags"] = kw.get("creationflags", 0) | NO_WINDOW
        _Popen_init(self, *args, **kw)

    subprocess.Popen.__init__ = _silent_init


def run_quiet(cmd, timeout=20):
    """Run fastboot/adb with no console window; returns stdout+stderr text ('' on failure)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=NO_WINDOW, errors="replace")
        return (r.stdout or "") + (r.stderr or "")
    except Exception:  # noqa: BLE001
        return ""


class QueueWriter(io.TextIOBase):
    """File-like object that sends everything printed to the GUI log."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(("log", s))
        return len(s)

    def flush(self):
        pass


# ----------------------------------------------------------------------------- phone status

class PhoneState:
    def __init__(self):
        self.mode = "none"      # none | fastboot | fastbootd | android | recovery | sideload | unauthorized
        self.serial = ""
        self.product = ""
        self.slot = ""
        self.unlocked = ""
        self.critical = ""

    def read(self, prev=None):
        """One light `fastboot devices` per call. The detailed getvars run only when a phone newly shows
        up in fastboot (or prev is None): hammering the bootloader with queries every few seconds made it
        drop off USB until the cable was replugged (2026-11-18)."""
        s = PhoneState()
        fb = run_quiet([metroid.FASTBOOT, "devices"], 10) if os.path.isfile(metroid.FASTBOOT) else ""
        if metroid_flasher.has_fastboot_device(fb):
            s.serial = fb.split()[0]
            if prev is not None and prev.serial == s.serial and prev.mode in ("fastboot", "fastbootd"):
                s.mode, s.product, s.slot, s.unlocked, s.critical = prev.mode, prev.product, prev.slot, prev.unlocked, prev.critical
                return s
            g = lambda n: (re.search(rf"^{n}:\s*(.*)$", run_quiet([metroid.FASTBOOT, "getvar", n], 15), re.M) or [None, ""])[1].strip()
            s.mode = "fastbootd" if g("is-userspace") == "yes" else "fastboot"
            s.product, s.slot, s.unlocked = g("product"), g("current-slot"), g("unlocked")
            info = run_quiet([metroid.FASTBOOT, "oem", "device-info"], 15)
            s.critical = "yes" if re.search(r"critical unlocked:\s*true", info, re.I) else "no"
            return s
        ad = run_quiet([metroid.ADB, "devices"], 10) if os.path.isfile(metroid.ADB) else ""
        if prev is not None and prev.mode in ("android", "recovery", "sideload", "unauthorized"):
            for line in ad.splitlines():
                if line.startswith(prev.serial + "\t") and line.split()[-1] in ("device", "recovery", "sideload", "unauthorized"):
                    mode = {"device": "android"}.get(line.split()[-1], line.split()[-1])
                    if mode == prev.mode:
                        return prev
        for line in ad.splitlines():
            if "\t" not in line or line.startswith("*"):   # skip "List of devices", "* daemon started" lines
                continue
            p = line.split()
            if len(p) >= 2:
                s.serial = p[0]
                s.mode = {"device": "android", "recovery": "recovery", "sideload": "sideload",
                          "unauthorized": "unauthorized"}.get(p[1], p[1])
                if s.mode == "android":
                    s.product = run_quiet([metroid.ADB, "-s", s.serial, "shell", "getprop", "ro.product.device"], 10).strip()
                    s.slot = run_quiet([metroid.ADB, "-s", s.serial, "shell", "getprop", "ro.boot.slot_suffix"], 10).strip().lstrip("_")
                return s
        return s

    def describe(self):
        """(headline, list of (ok, text) requirement lines)"""
        if self.mode == "none":
            return ("No phone found", [(False, "Plug the phone in. For flashing: hold Power + Volume Down until the "
                                               "fastboot screen shows. Use a rear USB port and a good cable, no hub.")])
        names = {"fastboot": "Bootloader (fastboot) - ready to flash", "fastbootd": "fastbootd (userspace fastboot)",
                 "android": "Android is running", "recovery": "Recovery", "sideload": "Recovery: Apply from ADB",
                 "unauthorized": "Android: USB debugging not allowed yet"}
        head = names.get(self.mode, self.mode)
        lines = []
        if self.mode == "fastboot":
            lines.append((self.product == metroid_flasher.EXPECTED_PRODUCT,
                          f"Phone: {self.product or '?'}" + ("" if self.product == metroid_flasher.EXPECTED_PRODUCT
                                                           else "  - this tool is only for the Nothing Phone (3)")))
            lines.append((True, f"Active slot: {self.slot or '?'}"))
            lines.append((self.unlocked == "yes", "Bootloader unlocked" if self.unlocked == "yes" else
                          "Bootloader LOCKED - unlock it first (fastboot flashing unlock; wipes the phone)"))
            lines.append((self.critical == "yes", "Critical partitions unlocked" if self.critical == "yes" else
                          "Critical partitions locked - needed for Unbrick to stock (fastboot flashing unlock_critical; wipes the phone)"))
        elif self.mode == "fastbootd":
            lines.append((False, "Flashing needs the plain bootloader. Use Tools > Reboot to bootloader."))
        elif self.mode == "unauthorized":
            lines.append((False, "Unlock the phone and tap Allow on the USB debugging prompt."))
        elif self.mode == "android":
            lines.append((True, f"Device: {self.product or '?'}  slot {self.slot or '?'}"))
            lines.append((False, "To flash, use Tools > Reboot to bootloader."))
        return head, lines


# ----------------------------------------------------------------------------- the window

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(TITLE + "  -  Nothing Phone (3)")
        try:
            self._icon = icon.photo(self)
            self.iconphoto(True, self._icon)
        except Exception:  # noqa: BLE001 - cosmetic only
            pass
        self.geometry("1040x720")
        self.minsize(860, 600)
        self.q = queue.Queue()
        self.busy = False
        self.usb = threading.Lock()
        self.phone = PhoneState()
        self.dry_ok = {}          # job -> True after a passing dry run (same inputs)
        self.job_buttons = []
        metroid.GUI = True
        metroid.ensure_layout()
        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._pump)
        self._poll()
        self.refresh_inputs()
        self.check_gapps_zips()
        self.say(BANNER, "good")
        self.say(f"{TITLE}  -  folder: {metroid.ROOT}\n")
        self.get_platform_tools()

    # ---------- look ----------
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("vista" if os.name == "nt" else "clam")
        except tk.TclError:
            pass
        st.configure("Big.TButton", font=("Segoe UI", 12, "bold"), padding=(10, 10))
        st.configure("Head.TLabel", font=("Segoe UI", 16, "bold"))
        st.configure("Sub.TLabel", font=("Segoe UI", 10), foreground="#555")
        st.configure("Ok.TLabel", foreground="#1a7f37", font=("Segoe UI", 10))
        st.configure("Bad.TLabel", foreground="#b42318", font=("Segoe UI", 10))
        st.configure("TNotebook.Tab", font=("Segoe UI", 11, "bold"), padding=(18, 6))
        st.configure("Box.TLabelframe.Label", font=("Segoe UI", 10, "bold"))

    def _build(self):
        top = ttk.Frame(self, padding=(12, 8, 12, 4))
        top.pack(fill="x")
        ttk.Label(top, text=f"MetroidFlasher  -  {metroid.APP_VERSION}", style="Head.TLabel").pack(anchor="center")
        ttk.Label(top, text="BETA  -  Nothing Phone (3) 'metroid' only. You flash at your own risk.",
                  style="Sub.TLabel").pack(anchor="center")

        # phone status
        sf = ttk.LabelFrame(self, text="Phone Status:", padding=(10, 4), style="Box.TLabelframe")
        sf.pack(fill="x", padx=12, pady=4)
        self.phone_head = ttk.Label(sf, text="Looking for the phone...", font=("Segoe UI", 12, "bold"))
        self.phone_head.pack(anchor="w")
        self.phone_lines = ttk.Frame(sf)
        self.phone_lines.pack(anchor="w", fill="x")

        mid = ttk.Frame(self, padding=(12, 4))
        mid.pack(fill="both", expand=True)
        nb = ttk.Notebook(mid)
        nb.pack(side="left", fill="y")
        self._flash_tab(nb)
        self._tools_tab(nb)

        right = ttk.Frame(mid)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.banner = tk.Label(right, text="", bg="#fff4ce", fg="#5c3c00", font=("Segoe UI", 12, "bold"),
                               wraplength=520, justify="left", padx=8, pady=6)
        self.logbox = scrolledtext.ScrolledText(right, wrap="word", font=("Consolas", 10),
                                                bg="#111", fg="#ddd", insertbackground="#ddd")
        self.logbox.pack(fill="both", expand=True)
        self.logbox.tag_configure("warn", foreground="#ffcc66")
        self.logbox.tag_configure("bad", foreground="#ff7b72")
        self.logbox.tag_configure("good", foreground="#7ee787")
        self.logbox.tag_configure("dim", foreground="#8b949e")
        bar = ttk.Frame(right)
        bar.pack(fill="x", pady=(6, 0))
        self.progress = ttk.Progressbar(bar, mode="determinate", length=260)
        self.progress.pack(side="left")
        self.busy_label = ttk.Label(bar, text="")
        self.busy_label.pack(side="left", padx=8)
        ttk.Button(bar, text="Save log...", command=self.save_log).pack(side="right")
        ttk.Button(bar, text="Copy log", command=self.copy_log).pack(side="right", padx=4)
        ttk.Button(bar, text="Clear", command=lambda: self.logbox.delete("1.0", "end")).pack(side="right")

        # built-in terminal: any fastboot/adb command, output in the log above
        con = ttk.Frame(right)
        con.pack(fill="x", pady=(6, 0))
        ttk.Label(con, text="Command:").pack(side="left")
        self.cmd_var = tk.StringVar()
        ce = ttk.Entry(con, textvariable=self.cmd_var, font=("Consolas", 10))
        ce.pack(side="left", fill="x", expand=True, padx=6)
        ce.bind("<Return>", lambda _e: self.run_console())
        cb = ttk.Button(con, text="Run", command=self.run_console)
        cb.pack(side="left")
        self.job_buttons.append(cb)
        ttk.Label(right, text="e.g.  fastboot getvar current-slot     adb devices     fastboot reboot bootloader",
                  style="Sub.TLabel").pack(anchor="w")

    def _flash_tab(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="  Flash  ")
        self.inputs = {}
        jobs = [
            ("unbrick", "Unbrick to stock Nothing OS", metroid.D_STOCK, "1-STOCK: the stock image folder (.img files)"),
            ("rom", "Install a custom ROM", metroid.D_ROM, "2-ROM: the ROM's .img files"),
            ("romgapps", "Install ROM + Google apps", metroid.D_GAPPS, "2-ROM + 3-GAPPS (NikGapps or MindTheGapps zips)"),
        ]
        self.gapps_var = tk.StringVar(value="Omni")
        self.customize = {}          # package -> [app packages left out]
        self.root_var = tk.StringVar(value="")
        self.gapps_have = None       # {package: bool} once the zips in 3-GAPPS are checked
        self.gapps_zips = {}
        for key, label, folder, hint in jobs:
            box = ttk.Frame(f)
            box.pack(fill="x", pady=3)
            b = ttk.Button(box, text=label, style="Big.TButton", width=30, command=lambda k=key: self.start_job(k))
            b.pack(anchor="w")
            self.job_buttons.append(b)
            row = ttk.Frame(box)
            row.pack(anchor="w", fill="x")
            lab = ttk.Label(row, text=hint, style="Sub.TLabel", wraplength=300, justify="left")
            lab.pack(side="left")
            ttk.Button(row, text="Open folder", width=11,
                       command=lambda d=folder if key != "romgapps" else metroid.D_GAPPS: self.open_folder(d)).pack(side="right")
            self.inputs[key] = lab
            if key == "romgapps":
                pick = ttk.Frame(box)
                pick.pack(anchor="w", fill="x", pady=(4, 0))
                ttk.Label(pick, text="Google apps:").pack(side="left")
                cb = ttk.Combobox(pick, textvariable=self.gapps_var, state="readonly", width=13,
                                  values=[gapps.NAMES[p] for p in gapps.ORDER])
                cb.pack(side="left", padx=4)
                cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_inputs())
                ttk.Button(pick, text="Customize...", command=self.customize_dialog).pack(side="left", padx=4)
                self.customize_label = ttk.Label(box, text="", style="Sub.TLabel", wraplength=320, justify="left")
                self.customize_label.pack(anchor="w")
        ttk.Separator(f).pack(fill="x", pady=8)
        rb = ttk.Frame(f)
        rb.pack(fill="x")
        ttk.Label(rb, text="Root (optional):").pack(side="left")
        ttk.Entry(rb, textvariable=self.root_var, width=24, state="readonly").pack(side="left", padx=4)
        ttk.Button(rb, text="Choose...", command=self.choose_root).pack(side="left")
        ttk.Button(rb, text="Clear", command=lambda: (self.root_var.set(""), self.refresh_inputs())).pack(side="left", padx=4)
        ttk.Label(f, text="Your rooted init_boot/boot, patched from THIS ROM (checked, never patched here).",
                  style="Sub.TLabel", wraplength=320, justify="left").pack(anchor="w", pady=(2, 0))
        ttk.Separator(f).pack(fill="x", pady=6)
        self.dry_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Dry run (check everything, flash nothing)", variable=self.dry_var).pack(anchor="w")
        ttk.Label(f, text="Every job needs a passing dry run before the real flash is allowed.",
                  style="Sub.TLabel", wraplength=320).pack(anchor="w", pady=(2, 8))
        ttk.Button(f, text="Refresh folders", command=lambda: (self.refresh_inputs(), self.check_gapps_zips())).pack(anchor="w")
        ttk.Button(f, text="Check my files (SHA-256)", command=self.hash_check).pack(anchor="w", pady=4)

    def _tools_tab(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="  Tools  ")

        def btn(text, cmd, tip=""):
            box = ttk.Frame(f)
            box.pack(anchor="w", pady=3)
            b = ttk.Button(box, text=text, command=cmd, width=34)
            b.pack(fill="x")
            self.job_buttons.append(b)
            if tip:
                # centered under its own button (same width as the button), same gap above and below
                ttk.Label(box, text=tip, style="Sub.TLabel", anchor="center", justify="center",
                          wraplength=0).pack(fill="x", pady=(4, 4))

        ttk.Label(f, text="adb + fastboot", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        btn("Use my own platform-tools folder...", self.pick_platform_tools,
            "Found by itself if it's on PATH or in the usual folders.")
        ttk.Label(f, text="Reboot to", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        btn("Android (system)", lambda: self.reboot(""))
        btn("Bootloader (fastboot)", lambda: self.reboot("bootloader"), "Where flashing happens.")
        btn("Recovery", lambda: self.reboot("recovery"))
        btn("fastbootd", lambda: self.reboot("fastboot"))
        ttk.Separator(f).pack(fill="x", pady=8)
        btn("Device info (getvar all)", self.device_info, "Read-only. Copy it for support.")
        btn("Switch active slot", self.switch_slot, "Changes which slot boots next.")
        btn("Wipe data (factory reset)", self.wipe_data, "ERASES everything on the phone.")
        btn("Open logs folder", lambda: self.open_folder(metroid.WORK))

    # ---------- log/queue ----------
    def say(self, s, tag=None):
        self.q.put(("log", s) if tag is None else ("tag", (s, tag)))

    def _pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind in ("log", "tag"):
                    text, tag = (val, None) if kind == "log" else val
                    if tag is None:
                        tag = ("bad" if re.search(r"STOPPED|FAILED|!!", text) else
                               "good" if re.search(r"\bDONE\b|Dry run finished|OKAY|: OK\b", text) else
                               "warn" if text.lstrip().startswith(("!", ">> ON THE PHONE")) else None)
                    self.logbox.insert("end", text, tag or ())
                    self.logbox.see("end")
                    if self.busy and "Building super.img" in text:
                        self.busy_label.config(text="Building super.img on the PC - this takes a few minutes")
                    elif self.busy and "Verifying super.img" in text:
                        self.busy_label.config(text="Checking super.img byte for byte against the ROM")
                    m = (re.search(r"Sending sparse 'super' (\d+)/(\d+)", text)
                         or re.search(r"\[super (\d+)/(\d+)\]", text))
                    if m and self.busy:
                        n, tot = int(m.group(1)), int(m.group(2))
                        if str(self.progress.cget("mode")) != "determinate":
                            self.progress.stop()
                            self.progress.config(mode="determinate", maximum=tot)
                        self.progress["value"] = n
                        self.busy_label.config(text=f"Sending system to the phone: part {n} of {tot} - keep the cable connected")
                    if ">> ON THE PHONE" in text:
                        self.show_banner(text.strip().lstrip(">").strip())
                    if self.busy and re.search(r"== Step (\d)/4", text):
                        step = re.search(r"== Step (\d)/4: ([^=]+)", text)
                        self.progress.config(mode="indeterminate", value=0)
                        self.progress.start(12)
                        self.busy_label.config(text=f"Step {step.group(1)} of 4: {step.group(2).strip()}"
                                                    "   (buttons unlock when it finishes)")
                    elif self.busy and "sending " in text and ".zip" in text:
                        self.busy_label.config(text=f"Installing {text.strip()[8:].rstrip(' .')} on the phone")
                    if "sending " in text or "waiting for the phone (bootloader)" in text:
                        self.show_banner("")
                elif kind == "phone":
                    self.show_phone(val)
                elif kind == "done":
                    self.set_busy(False)
                    if val:
                        val()
                elif kind == "call":   # background helper finished; doesn't touch the busy state
                    val()
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def show_banner(self, text):
        if text:
            self.banner.config(text="ON THE PHONE: " + text)
            self.banner.pack(fill="x", pady=(0, 6), before=self.logbox)
            self.bell()
        else:
            self.banner.pack_forget()

    def set_busy(self, on, label=""):
        self.busy = on
        self.busy_label.config(text=(label + "   (buttons unlock when it finishes)") if on else label)
        self.progress.stop()
        self.progress.config(mode="indeterminate" if on else "determinate", value=0)
        if on:
            self.progress.start(12)
        for b in self.job_buttons:
            b.state(["disabled"] if on else ["!disabled"])
        if on:
            # status polling pauses while a job talks to the phone (two programs on USB made it drop),
            # so say that instead of showing a state that goes stale
            self.phone_head.config(text="Working with the phone - status updates resume when the job ends")
            for w in self.phone_lines.winfo_children():
                w.destroy()
        else:
            self.show_banner("")

    # ---------- phone polling ----------
    def _poll(self):
        # Only one program may talk to the phone at a time: a status check and a flash (or a second
        # status check) fighting over USB made the bootloader drop off until replugged.
        if not self.busy and self.usb.acquire(blocking=False):
            def work():
                try:
                    self.q.put(("phone", PhoneState().read(self.phone)))
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    self.usb.release()
            threading.Thread(target=work, daemon=True).start()
        self.after(5000, self._poll)

    def show_phone(self, st):
        if self.busy:
            return
        self.phone = st
        head, lines = st.describe()
        self.phone_head.config(text=head + (f"   ({st.serial})" if st.serial else ""))
        for w in self.phone_lines.winfo_children():
            w.destroy()
        short = all(len(t) < 60 for _ok, t in lines)
        for i, (ok, text) in enumerate(lines):
            lab = ttk.Label(self.phone_lines, text=("OK   " if ok else "!!   ") + text, wraplength=900,
                            style="Ok.TLabel" if ok else "Bad.TLabel")
            if short:   # side by side, two per row
                lab.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 40))
            else:
                lab.grid(row=i, column=0, columnspan=2, sticky="w")

    # ---------- inputs ----------
    def get_platform_tools(self):
        """First run: download Google's platform-tools (pinned version, SHA-256 checked) in the background."""
        if os.path.isfile(metroid.FASTBOOT) and os.path.isfile(metroid.ADB):
            return

        def work():
            ok = metroid.ensure_platform_tools(log=lambda m: self.q.put(("log", m + "\n")))
            msg = "adb + fastboot ready.\n" if ok else ("!! Could not get platform-tools - see above. Or use "
                                                         "Tools > Use my own platform-tools folder.\n")
            self.q.put(("call", lambda: self.say(msg, "good" if ok else "bad")))
        threading.Thread(target=work, daemon=True).start()

    def pick_platform_tools(self):
        d = filedialog.askdirectory(title="Your platform-tools folder (the one with adb.exe and fastboot.exe)")
        if not d:
            return
        why = metroid.set_own_platform_tools(os.path.normpath(d))
        if why:
            messagebox.showerror(TITLE, f"Can't use that folder: {why}.")
        else:
            self.say(f"Using your own platform-tools: {metroid.FASTBOOT}\n", "good")

    def check_gapps_zips(self):
        """Hash the zips in 3-GAPPS in the background (first time can take a minute; cached after)."""
        def work():
            try:
                have, zips = metroid.available_gapps()
            except Exception as e:  # noqa: BLE001
                have, zips = {}, {}
                self.q.put(("log", f"Could not check 3-GAPPS: {e}\n"))
            def done():
                self.gapps_have, self.gapps_zips = have, zips
                self.refresh_inputs()
            self.q.put(("call", done))
        self.gapps_have = None
        threading.Thread(target=work, daemon=True).start()

    def choose_root(self):
        p = filedialog.askopenfilename(title="Your rooted init_boot.img or boot.img",
                                       filetypes=[("Boot images", "*.img"), ("All files", "*.*")])
        if p:
            try:
                kind, ver, patch = metroid_flasher.boot_header(p)
            except metroid_flasher.FlashError as e:
                messagebox.showerror(TITLE, str(e))
                return
            self.root_var.set(p)
            self.say(f"Root image: {os.path.basename(p)} ({kind}, Android {ver}, patch {patch}). "
                     f"The dry run checks it matches the ROM.\n")
            self.refresh_inputs()

    def customize_dialog(self):
        pkg = self.gapps_var.get().lower()
        if not self.gapps_have or not self.gapps_have.get(pkg):
            messagebox.showinfo(TITLE, f"3-GAPPS doesn't hold the zips for {gapps.NAMES[pkg]} yet.")
            return
        try:
            apps = metroid.package_apps(pkg, self.gapps_zips)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(TITLE, f"Could not read the GApps zip: {e}")
            return
        d = tk.Toplevel(self)
        d.title(f"Customize {gapps.NAMES[pkg]}")
        d.transient(self)
        d.grab_set()
        body = ttk.Frame(d, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"Untick apps to leave OUT of {gapps.NAMES[pkg]}.", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(body, text="The ROM's own app stays when you untick its Google version. Greyed-out ones are "
                             "required. Anything else can be added later from the Play Store.",
                  style="Sub.TLabel", wraplength=380, justify="left").pack(anchor="w", pady=(2, 8))
        frame = ttk.Frame(body)
        frame.pack(fill="both", expand=True)
        cols = 2
        left_out = set(self.customize.get(pkg, []))
        vars_ = {}
        for i, (name, locked) in enumerate(apps):
            v = tk.BooleanVar(value=locked or name not in left_out)
            vars_[name] = v
            c = ttk.Checkbutton(frame, text=name, variable=v)
            if locked:
                c.state(["disabled"])
            c.grid(row=i // cols, column=i % cols, sticky="w", padx=4, pady=1)

        def ok():
            self.customize[pkg] = sorted(n for n, v in vars_.items() if not v.get())
            d.destroy()
            self.refresh_inputs()
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="OK", command=ok).pack(side="right")
        ttk.Button(row, text="Cancel", command=d.destroy).pack(side="right", padx=6)
        ttk.Button(row, text="Tick all", command=lambda: [v.set(True) for v in vars_.values()]).pack(side="left")

    def refresh_inputs(self):
        stock = metroid.find_images(metroid.D_STOCK)
        rom = metroid.find_images(metroid.D_ROM)
        pkg = self.gapps_var.get().lower()
        ok_g = [pkg] if self.gapps_have and self.gapps_have.get(pkg) else []

        def n_img(d):
            return len([x for x in os.listdir(d) if x.lower().endswith(".img")]) if d else 0

        self.inputs["unbrick"].config(
            text=f"1-STOCK: {n_img(stock)} images found" if stock else "1-STOCK is empty: put the stock image folder (.img files) there.",
            style="Sub.TLabel" if stock else "Bad.TLabel")
        self.inputs["rom"].config(
            text=f"2-ROM: {n_img(rom)} images found" if rom else "2-ROM is empty: put the ROM's .img files there.",
            style="Sub.TLabel" if rom else "Bad.TLabel")
        if not rom:
            g = "2-ROM is empty."
        elif self.gapps_have is None:
            g = "Checking the GApps zips in 3-GAPPS..."
        elif not ok_g:
            ready = [gapps.NAMES[p] for p in gapps.ORDER if self.gapps_have.get(p)]
            g = (f"3-GAPPS doesn't hold the zip(s) for {gapps.NAMES[pkg]}."
                 + (f" Ready: {', '.join(ready)}." if ready else " Put the NikGapps or MindTheGapps zips there."))
        else:
            ready = [gapps.NAMES[p] for p in gapps.ORDER if self.gapps_have.get(p)]
            g = f"3-GAPPS ready for: {', '.join(ready)}"
        self.inputs["romgapps"].config(text=g, style="Sub.TLabel" if rom and ok_g else "Bad.TLabel")
        left = self.customize.get(pkg, [])
        if hasattr(self, "customize_label"):
            what = gapps.WHAT[pkg]
            self.customize_label.config(text=f"{gapps.NAMES[pkg]}: {what}. " + (f"Leaving out: {', '.join(left)}" if left
                                        else "All its apps."))
        return stock, rom, ok_g

    def signature(self, job):
        """What a dry run approved: job + files in its folders. A real flash needs a dry run of the same thing."""
        pkg = self.gapps_var.get().lower()
        parts = [job, pkg, ",".join(self.customize.get(pkg, [])), self.root_var.get()]
        for d in (metroid.D_STOCK, metroid.D_ROM, metroid.D_GAPPS):
            for dp, _dn, fn in os.walk(d):
                parts += [f"{f}:{os.path.getsize(os.path.join(dp, f))}" for f in sorted(fn)]
        return "|".join(parts)

    # ---------- jobs ----------
    def start_job(self, key):
        if self.busy:
            return
        stock, rom, ok_g = self.refresh_inputs()
        need = {"unbrick": stock, "rom": rom, "romgapps": rom and len(ok_g) == 1}[key]
        if key == "unbrick" and self.root_var.get():
            messagebox.showinfo(TITLE, "The root image is only used for custom ROMs. Unbrick installs stock as-is.")
        if not need:
            messagebox.showwarning(TITLE, "The folder for this job isn't ready yet - see the red text under the button.")
            return
        if not metroid.check_tool_integrity():
            messagebox.showerror(TITLE, "The tool folder has been changed or is incomplete.\n"
                                        "Download a fresh copy from the official release page.")
            return
        dry = self.dry_var.get()
        if key == "rom" and ok_g and not dry and not messagebox.askyesno(
                TITLE, "3-GAPPS has a GApps zip, but 'Install a custom ROM' installs the ROM WITHOUT Google apps.\n\n"
                       "Continue with the ROM only?"):
            return
        sig = self.signature(key)
        if not dry:
            if self.dry_ok.get(key) != sig:
                messagebox.showinfo(TITLE, "Run a dry run of this job first (tick 'Dry run'). "
                                           "The real flash unlocks after a dry run passes with these same files.")
                return
            if not self.confirm_flash(key):
                return
        metroid.GUI_DRY = dry
        pkg = self.gapps_var.get().lower()
        metroid.GUI_GAPPS = pkg
        metroid.GUI_CUSTOMIZE = list(self.customize.get(pkg, []))
        metroid.GUI_ROOT_IMAGE = self.root_var.get() if key != "unbrick" else ""
        job = {"unbrick": metroid.job_unbrick, "rom": metroid.job_custom_rom, "romgapps": metroid.job_rom_gapps}[key]
        label = {"unbrick": "Unbrick to stock", "rom": "Custom ROM", "romgapps": "ROM + GApps"}[key]
        self.say(f"\n=== {label}: {'DRY RUN' if dry else 'FLASHING'} ===\n", "warn")
        self.set_busy(True, f"{label} {'(dry run)' if dry else '- keep the cable connected'}...")
        start_log = self.logbox.index("end-1c")

        def work():
            with self.usb:   # waits for any status check still talking to the phone
                with contextlib.redirect_stdout(QueueWriter(self.q)):
                    try:
                        job()
                    except SystemExit as e:
                        print(f"\nSTOPPED: {e}")
                    except Exception as e:  # noqa: BLE001
                        print(f"\nSTOPPED: unexpected error: {e!r}")
            self.q.put(("done", lambda: self.after_job(key, sig, dry, start_log)))

        threading.Thread(target=work, daemon=True).start()

    def after_job(self, key, sig, dry, start_log):
        text = self.logbox.get(start_log, "end")
        ok = "STOPPED" not in text and ("Dry run finished" in text if dry else "DONE" in text)
        if dry:
            if ok:
                self.dry_ok[key] = sig
                self.dry_var.set(False)
                self.say("\nDry run passed. Untick nothing more: 'Dry run' is now off - press the same button to flash for real.\n", "good")
            else:
                self.say("\nThe dry run did not pass. Nothing was flashed. Read the STOPPED line above.\n", "bad")
        else:
            self.dry_ok.pop(key, None)
            self.dry_var.set(True)
            if ok:
                messagebox.showinfo(TITLE, "DONE. The phone is rebooting.\nFirst boot can sit on the logo for a while - give it some time.")

    def confirm_flash(self, key):
        what = {"unbrick": "stock Nothing OS from 1-STOCK", "rom": "the custom ROM in 2-ROM",
                "romgapps": f"the custom ROM in 2-ROM with Google apps ({self.gapps_var.get()}) built in"}[key]
        d = tk.Toplevel(self)
        d.title("Flash for real?")
        d.transient(self)
        d.grab_set()
        d.resizable(False, False)
        body = ttk.Frame(d, padding=14)
        body.pack()
        ttk.Label(body, text=f"This installs {what}.", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(body, text="It ERASES everything on the phone: apps, photos, accounts, settings.",
                  style="Bad.TLabel").pack(anchor="w", pady=(4, 8))
        checks = ["I backed up what I need from the phone.",
                  "The phone is on the fastboot screen and the cable is in a rear USB port (no hub).",
                  "I will not unplug the phone until it says DONE."]
        if key == "romgapps":
            checks.append("I will tap 'Apply update -> Apply from ADB' on the phone when this window asks (twice).")
        vars_ = []
        for c in checks:
            v = tk.BooleanVar()
            ttk.Checkbutton(body, text=c, variable=v).pack(anchor="w")
            vars_.append(v)
        ttk.Label(body, text="Type FLASH to continue:").pack(anchor="w", pady=(10, 2))
        e = ttk.Entry(body, width=20)
        e.pack(anchor="w")
        res = {"ok": False}

        def go():
            if not all(v.get() for v in vars_):
                messagebox.showwarning(TITLE, "Tick every box first.", parent=d)
                return
            if e.get().strip() != "FLASH":
                messagebox.showwarning(TITLE, "Type FLASH (capital letters) to continue.", parent=d)
                return
            res["ok"] = True
            d.destroy()

        row = ttk.Frame(body)
        row.pack(fill="x", pady=(12, 0))
        ttk.Button(row, text="Flash", command=go).pack(side="right")
        ttk.Button(row, text="Cancel", command=d.destroy).pack(side="right", padx=6)
        e.focus_set()
        self.wait_window(d)
        return res["ok"]

    def hash_check(self):
        if self.busy:
            return
        self.set_busy(True, "Checking files...")

        def work():
            with contextlib.redirect_stdout(QueueWriter(self.q)):
                metroid.hash_check_menu()
            self.q.put(("done", None))

        threading.Thread(target=work, daemon=True).start()

    # ---------- tools ----------
    def fb_cmd(self, args, label, timeout=60, after=None):
        if self.busy:
            return
        self.set_busy(True, label + "...")
        self.say(f"\n=== {label} ===\n", "warn")

        def work():
            with self.usb:
                for a in args:
                    exe = metroid.ADB if a[0] == "adb" else metroid.FASTBOOT
                    cmd = [exe, *a[1:]]
                    self.q.put(("log", "  > " + " ".join(["adb" if a[0] == "adb" else "fastboot", *a[1:]]) + "\n"))
                    self.q.put(("log", run_quiet(cmd, timeout) or "  (no output)\n"))
            self.q.put(("done", after))

        threading.Thread(target=work, daemon=True).start()

    def run_console(self):
        line = self.cmd_var.get().strip()
        if not line or self.busy:
            return
        import shlex
        try:
            parts = shlex.split(line, posix=False)
        except ValueError:
            parts = line.split()
        parts = [p.strip('"') for p in parts]
        tool = parts[0].lower().removesuffix(".exe")
        if tool not in ("fastboot", "adb"):
            messagebox.showinfo(TITLE, "The command box runs fastboot and adb commands only.\n"
                                       "Start the line with fastboot or adb.")
            return
        low = [p.lower() for p in parts[1:]]
        # same never-touch rule as the engine: device-unique partitions (IMEI, calibration, keys)
        for p in low:
            base = re.sub(r"_(a|b)$", "", p)
            if base in metroid_flasher.NEVER_TOUCH and any(v in low for v in ("flash", "erase", "format", "wipe")):
                messagebox.showerror(TITLE, f"Refused: '{p}' holds data unique to this phone (IMEI, calibration, keys).\n"
                                            "MetroidFlasher never writes or erases it.")
                return
        risky = [p for p in low if re.sub(r"_(a|b)$", "", p) in ("xbl", "xbl_config", "abl", "super")]
        if risky and any(v in low for v in ("erase", "format")):
            name = risky[0]
            ans = simpledialog.askstring(TITLE, f"Erasing '{name}' can stop the phone booting.\n"
                                                   f"Type {name} to confirm:", parent=self)
            if (ans or "").strip().lower() != name:
                return
        self.cmd_var.set("")
        self.fb_cmd([(tool, *parts[1:])], line, timeout=600)

    def reboot(self, target):
        """Send the reboot, then judge by where the phone ends up. The bootloader often reboots before it
        answers, so fastboot prints 'FAILED (Device sent unknown status code)' even when it worked."""
        m = self.phone.mode
        names = {"": "Android", "bootloader": "the bootloader (fastboot)", "recovery": "recovery", "fastboot": "fastbootd"}
        want = {"": ("android", "unauthorized"), "bootloader": ("fastboot",), "recovery": ("recovery", "sideload"),
                "fastboot": ("fastbootd",)}[target]
        if m in ("fastboot", "fastbootd"):
            cmd = ("fastboot", "reboot", *([target] if target else []))
        elif m in ("android", "recovery", "sideload"):
            cmd = ("adb", "reboot", *([target] if target else []))
        else:
            messagebox.showinfo(TITLE, "No phone found.\n\nAndroid: turn on USB debugging and allow this PC.\n"
                                       "Recovery: turn on ADB in the recovery's menu if it has that option, "
                                       "or use its 'Reboot to bootloader'.\n"
                                       "Fastboot screen: power off, then hold Power + Volume Down.")
            return
        if self.busy:
            return
        self.set_busy(True, f"Rebooting to {names[target]}...")
        self.say(f"\n=== Reboot to {names[target]} ===\n", "warn")

        def work():
            with self.usb:
                exe = metroid.ADB if cmd[0] == "adb" else metroid.FASTBOOT
                out = run_quiet([exe, *cmd[1:]], 30).strip()
                self.q.put(("tag", (f"  > {' '.join(cmd)}\n" + (out + "\n" if out else ""), "dim")))
                end, mode = time.time() + 90, None
                time.sleep(4)
                while time.time() < end:
                    mode = PhoneState().read().mode
                    if mode in want:
                        break
                    time.sleep(3)
                if mode in want:
                    self.q.put(("tag", (f"OK: the phone is in {names[target]}.\n", "good")))
                elif target == "":
                    self.q.put(("log", "The phone is rebooting to Android. The GUI can only see it there with USB "
                                       "debugging turned on, so check the phone's screen.\n"))
                elif target == "recovery":
                    self.q.put(("log", "The phone should be in recovery now. If the GUI doesn't see it, turn on ADB in "
                                       "the recovery's menu (if it has that option) - or just check the phone's screen.\n"))
                else:
                    self.q.put(("tag", (f"!! The phone didn't show up in {names[target]} within 90 seconds. "
                                        "Check the phone's screen and the cable.\n", "bad")))
            self.q.put(("done", None))

        threading.Thread(target=work, daemon=True).start()

    def device_info(self):
        if self.phone.mode in ("fastboot", "fastbootd"):
            self.fb_cmd([("fastboot", "getvar", "all"), ("fastboot", "oem", "device-info")], "Device info")
        elif self.phone.mode == "android":
            self.fb_cmd([("adb", "shell", "getprop")], "Device info", timeout=30)
        else:
            messagebox.showinfo(TITLE, "Connect the phone in fastboot or Android first.")

    def switch_slot(self):
        """System, vendor, product... are logical partitions in super, and super only has room for ONE slot's
        set (virtual A/B). After a MetroidFlasher flash the other slot has none, so switching bootloops.
        fastbootd can see logical partitions and checks; the plain bootloader can't, so it warns hard."""
        m = self.phone.mode
        if m not in ("fastboot", "fastbootd"):
            messagebox.showinfo(TITLE, "Put the phone on the fastboot screen (or fastbootd) first.")
            return
        other = {"a": "b", "b": "a"}.get(self.phone.slot)
        if not other:
            messagebox.showwarning(TITLE, "Could not read the current slot.")
            return
        if m == "fastbootd":
            with self.usb:
                out = run_quiet([metroid.FASTBOOT, "getvar", f"partition-size:system_{other}"], 15)
            size = out.split(f"partition-size:system_{other}:")[-1].split()[0] if f"system_{other}:" in out else ""
            try:
                empty = int(size, 16) == 0
            except ValueError:
                empty = True
            if empty:
                messagebox.showerror(TITLE, f"Slot {other} has no system installed (system_{other} doesn't exist "
                                            f"in super).\n\nSwitching would bootloop, so nothing was changed.")
                return
            if messagebox.askyesno(TITLE, f"Slot {other} has a system installed.\n\nSwitch the active slot from "
                                          f"{self.phone.slot} to {other}?"):
                self.fb_cmd([("fastboot", "set_active", other)], f"Switch slot to {other}")
            return
        if messagebox.askyesno(TITLE, f"Switch the active slot from {self.phone.slot} to {other}?\n\n"
                                      f"WARNING: after a MetroidFlasher flash, slot {other} has NO system (super only "
                                      f"holds one slot's system, vendor, product...). Switching will BOOTLOOP unless an "
                                      f"OTA has installed slot {other} since.\n\nTo check first: reboot to fastbootd and "
                                      f"press this button there.\n\nSwitch anyway?", icon="warning", default="no"):
            self.fb_cmd([("fastboot", "set_active", other)], f"Switch slot to {other}")

    def wipe_data(self):
        """fastboot -w only works from the bootloader. From Android (USB debugging on) or a recovery with adb,
        send the phone there first; otherwise explain how to get there and wait for it."""
        m = self.phone.mode
        warn = ("Wipe data (factory reset)?\n\nThis ERASES everything on the phone: apps, photos, accounts, "
                "settings. It cannot be undone.")
        if m == "fastboot":
            if messagebox.askyesno(TITLE, warn, icon="warning"):
                self.fb_cmd([("fastboot", "-w")], "Wipe data", timeout=300)
            return
        if m in ("android", "recovery", "sideload"):
            if not messagebox.askyesno(TITLE, warn + "\n\nThe phone will be sent to the bootloader (fastboot screen) "
                                                     "first, then wiped there.", icon="warning"):
                return
            self.wipe_via_bootloader(["adb", "reboot", "bootloader"])
            return
        if m == "fastbootd":
            if not messagebox.askyesno(TITLE, warn + "\n\nThe phone is in fastbootd: it will be sent to the plain "
                                                     "bootloader first, then wiped there.", icon="warning"):
                return
            self.wipe_via_bootloader(["fastboot", "reboot", "bootloader"])
            return
        if not messagebox.askyesno(TITLE, "The phone isn't reachable right now (not plugged in, a recovery with ADB "
                                          "off, or Android with USB debugging off).\n\nPut it on the fastboot screen: "
                                          "power off, then hold Power + Volume Down (or choose 'Reboot to bootloader' "
                                          "in recovery).\n\n"
                                          "Wait for it, then wipe?", icon="warning"):
            return
        self.wipe_via_bootloader(None)

    def wipe_via_bootloader(self, send):
        if self.busy:
            return
        self.set_busy(True, "Waiting for the fastboot screen...")
        self.say("\n=== Wipe data ===\n", "warn")

        def work():
            with self.usb:
                if send:
                    exe = metroid.ADB if send[0] == "adb" else metroid.FASTBOOT
                    self.q.put(("log", f"  > {' '.join(send)}\n" + run_quiet([exe, *send[1:]], 30)))
                end = time.time() + 300
                found = False
                while time.time() < end:
                    out = run_quiet([metroid.FASTBOOT, "devices"], 10)
                    if metroid_flasher.has_fastboot_device(out) and \
                            "yes" not in run_quiet([metroid.FASTBOOT, "getvar", "is-userspace"], 15).split("is-userspace:")[-1][:6]:
                        found = True
                        break
                    time.sleep(3)
                if not found:
                    self.q.put(("log", "STOPPED: the phone never reached the fastboot screen. Nothing was wiped.\n"))
                else:
                    self.q.put(("log", "  > fastboot -w\n" + run_quiet([metroid.FASTBOOT, "-w"], 300)))
                    self.q.put(("log", "Wipe finished. Use Tools > Reboot to Android when you're ready.\n"))
            self.q.put(("done", None))

        threading.Thread(target=work, daemon=True).start()

    # ---------- misc ----------
    def open_folder(self, d):
        os.makedirs(d, exist_ok=True)
        if os.name == "nt":
            os.startfile(d)
        else:
            subprocess.Popen(["xdg-open", d])

    def save_log(self):
        p = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=f"MetroidFlasher-log-{time.strftime('%Y%m%d-%H%M')}.txt")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.logbox.get("1.0", "end"))

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.logbox.get("1.0", "end"))

    def on_close(self):
        if self.busy and not messagebox.askyesno(TITLE, "Something is still running. If it is flashing, closing now "
                                                        "can leave the phone half-flashed.\n\nClose anyway?", icon="warning"):
            return
        self.destroy()


def _crash_log():
    """Windowed exe: there is no console, so errors would vanish (and printing to a missing stderr can
    kill the app). Send them to tool/work/gui-errors.log instead."""
    import traceback
    path = os.path.join(metroid.WORK, "gui-errors.log")
    os.makedirs(metroid.WORK, exist_ok=True)
    f = open(path, "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = f
    if sys.stdout is None:
        sys.stdout = f

    def hook(*exc):
        f.write("\n--- " + time.strftime("%Y-%m-%d %H:%M:%S") + " ---\n")
        traceback.print_exception(*exc, file=f)
    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
    return hook


def main():
    hook = _crash_log()
    if "--run-command" in sys.argv:          # test switch: type a command into the box and run it
        cmd = sys.argv[sys.argv.index("--run-command") + 1]
        a = App()
        a.report_callback_exception = hook
        a.cmd_var.set(cmd)
        a.after(1500, a.run_console)
        a.after(12000, lambda: (open(os.path.join(metroid.WORK, "gui-command-test.txt"), "w", encoding="utf-8")
                                .write(a.logbox.get("1.0", "end")), a.destroy()))
        a.mainloop()
        return 0
    if "--selftest" in sys.argv:
        a = App()
        a.update()
        st = PhoneState().read()
        print("phone:", st.mode, st.product, st.slot)
        print("inputs:", [w.cget("text") for w in a.inputs.values()])
        a.destroy()
        print("GUI OK")
        return 0
    a = App()
    a.report_callback_exception = hook
    a.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
