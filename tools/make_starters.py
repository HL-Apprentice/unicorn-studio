# Generates the starter displays in ~/Documents/Unicorn Displays (palette+rows format)
import json, math, os, random
OUT = os.path.expanduser("~/Documents/Unicorn Displays")
CH = ".#abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@$%&*+=?!~^<>/|"

def blank(c=(0, 0, 0)):
    return [[c] * 32 for _ in range(32)]

def encode(name, desc, frames):
    pal = {}
    out = []
    for px, dur in frames:
        rows = []
        for y in range(32):
            r = ""
            for x in range(32):
                c = "#%02x%02x%02x" % px[y][x]
                if c not in pal:
                    pal[c] = "." if c == "#000000" else CH[1 + sum(1 for v in pal.values() if v != ".")]
                r += pal[c]
            rows.append(r)
        out.append({"duration_ms": dur, "rows": rows})
    doc = {"format": "unicorn-display", "version": 1, "name": name, "description": desc,
           "palette": {v: k for k, v in pal.items()}, "frames": out}
    with open(os.path.join(OUT, name + ".json"), "w") as f:
        json.dump(doc, f, indent=1)
    print(name, len(frames), "frames", len(pal), "colors")

def pumpkin(face):
    p = blank()
    for y in range(32):
        for x in range(32):
            dx, dy = (x - 15.5) / 14.5, (y - 18.5) / 11.5
            if dx * dx + dy * dy <= 1:
                rib = abs(((x - 15.5) / 14.5) * 3) % 1
                p[y][x] = (200, 80, 0) if rib < 0.12 else (255, 120, 0)
    for y in range(3, 8):
        for x in range(15, 18):
            p[y][x] = (40, 140, 30)
    p[4][18] = p[3][19] = (40, 140, 30)
    for i in range(4):  # triangle eyes
        for x in range(9 - i, 10 + i):
            p[12 + i][x] = face
        for x in range(22 - i, 23 + i):
            p[12 + i][x] = face
    p[17][15] = p[17][16] = p[18][15] = p[18][16] = face  # nose
    for x in range(8, 24):  # mouth with teeth
        top = 21 if (x // 2) % 2 == 0 else 22
        for y in range(top, 25):
            p[y][x] = face
    return p

encode("pumpkin", "Jack-o'-lantern with a flickering candle face (Halloween)",
       [(pumpkin((255, 230, 60)), 300), (pumpkin((255, 190, 30)), 120),
        (pumpkin((255, 240, 120)), 250), (pumpkin((230, 150, 20)), 90)])

def heart(scale, col):
    p = blank()
    for y in range(32):
        for x in range(32):
            X = (x - 15.5) / (13 * scale)
            Y = -(y - 15) / (13 * scale)
            if (X * X + Y * Y - 1) ** 3 - X * X * Y ** 3 <= 0:
                p[y][x] = col
    # shine
    if scale > 0.8:
        p[10][9] = p[9][10] = p[10][10] = (255, 200, 210)
    return p

encode("heart", "Beating heart",
       [(heart(0.85, (230, 0, 40)), 450), (heart(1.0, (255, 20, 60)), 140),
        (heart(0.88, (230, 0, 40)), 120), (heart(1.0, (255, 20, 60)), 160)])

random.seed(7)
STARS = [(random.randrange(32), random.randrange(20)) for _ in range(18)]

def ufo(cx, cy, k, beam):
    p = blank((0, 0, 12))
    for i, (sx, sy) in enumerate(STARS):
        b = 90 + (60 if (i + k) % 5 == 0 else 0)
        p[sy][sx] = (b, b, b)
    for y in range(27, 32):  # ground
        for x in range(32):
            p[y][x] = (10, 60, 10) if y > 27 else (20, 90, 20)
    def put(x, y, c):
        if 0 <= x < 32 and 0 <= y < 32:
            p[y][x] = c
    if beam:
        for y in range(cy + 3, 28):
            w = 2 + (y - cy) // 3
            for x in range(cx - w, cx + w + 1):
                if 0 <= x < 32:
                    r, g, b = p[y][x]
                    p[y][x] = (min(255, r + 40), min(255, g + 140), min(255, b + 60))
    for x in range(cx - 2, cx + 3):  # dome
        put(x, cy - 2, (120, 220, 255))
    for x in range(cx - 1, cx + 2):
        put(x, cy - 3, (160, 240, 255))
    for x in range(cx - 6, cx + 7):  # saucer
        put(x, cy - 1, (170, 170, 190))
        put(x, cy, (200, 200, 215))
    for x in range(cx - 4, cx + 5):
        put(x, cy + 1, (110, 110, 130))
    lights = [(255, 0, 0), (255, 220, 0), (0, 255, 120), (0, 140, 255)]
    for j, x in enumerate(range(cx - 5, cx + 6, 2)):
        put(x, cy, lights[(j + k) % 4])
    return p

frames = []
for k, cx in enumerate(range(-7, 40, 2)):
    cy = 9 + int(round(2 * math.sin(k * 0.7)))
    frames.append((ufo(cx, cy, k, 12 <= cx <= 20), 110))
encode("ufo", "Animated UFO crossing the night sky, beams down mid-screen", frames)
