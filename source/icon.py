"""MetroidFlasher icon, drawn in code (no image files): a red disc with a white down-arrow.

pixels(n)        -> n x n list of (r, g, b, a)
photo(tk_root)   -> tkinter PhotoImage for the window/taskbar
write_ico(path)  -> .ico file for the exe (used by the build)
"""
import struct

RED = (215, 25, 33)
WHITE = (255, 255, 255)


def pixels(n):
    rows = []
    c = (n - 1) / 2
    r_out = n * 0.48
    for y in range(n):
        row = []
        for x in range(n):
            # anti-aliased disc edge
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            a = max(0.0, min(1.0, r_out - d + 0.5))
            if a <= 0:
                row.append((0, 0, 0, 0))
                continue
            u, v = x / n, y / n
            shaft = 0.42 <= u <= 0.58 and 0.20 <= v <= 0.56
            head = v >= 0.50 and v <= 0.78 and abs(u - 0.5) <= (0.78 - v) * 0.95
            base = 0.26 <= u <= 0.74 and 0.80 <= v <= 0.86
            col = WHITE if (shaft or head or base) else RED
            row.append((*col, int(255 * a)))
        rows.append(row)
    return rows


def photo(root, n=64):
    import tkinter as tk
    img = tk.PhotoImage(master=root, width=n, height=n)
    px = pixels(n)
    img.put(" ".join("{" + " ".join("#%02x%02x%02x" % p[:3] for p in row) + "}" for row in px))
    for y, row in enumerate(px):
        for x, p in enumerate(row):
            if p[3] < 128:
                img.transparency_set(x, y, True)
    return img


def write_ico(path, sizes=(16, 32, 48, 64, 128, 256)):
    images = []
    for n in sizes:
        px = pixels(n)
        xor = b"".join(bytes((b, g, r, a)) for row in reversed(px) for (r, g, b, a) in row)
        and_row = ((n + 31) // 32) * 4
        bmp = struct.pack("<IiiHHIIiiII", 40, n, n * 2, 1, 32, 0, len(xor), 0, 0, 0, 0) + xor + b"\0" * (and_row * n)
        images.append((n, bmp))
    head = struct.pack("<HHH", 0, 1, len(images))
    off = 6 + 16 * len(images)
    entries = b""
    for n, bmp in images:
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(bmp), off)
        off += len(bmp)
    with open(path, "wb") as f:
        f.write(head + entries + b"".join(b for _n, b in images))


if __name__ == "__main__":
    import sys
    write_ico(sys.argv[1] if len(sys.argv) > 1 else "metroidflasher.ico")
