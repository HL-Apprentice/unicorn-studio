# Unicorn Studio firmware for Pimoroni Cosmic (32x32), Galactic (53x11) and Stellar (16x16) Unicorn.
# Wi-Fi + time + MQTT (Home Assistant discovery) + rotation + modes + custom displays + playlists.
# Configuration lives in secrets.py (see secrets.example.py). No credentials in this file.
import time, machine, network, gc, json, os, math, random
import micropython

VERSION = "us-2.1"

# ---------- board auto-detect (each Pimoroni MicroPython build ships one board module) ----------
try:
    from cosmic import CosmicUnicorn as U
    from picographics import PicoGraphics, DISPLAY_COSMIC_UNICORN as DISPLAY
    BOARD = "cosmic"
except ImportError:
    try:
        from galactic import GalacticUnicorn as U
        from picographics import PicoGraphics, DISPLAY_GALACTIC_UNICORN as DISPLAY
        BOARD = "galactic"
    except ImportError:
        from stellar import StellarUnicorn as U
        from picographics import PicoGraphics, DISPLAY_STELLAR_UNICORN as DISPLAY
        BOARD = "stellar"

try:
    machine.freq(200000000)
except Exception:
    pass
cu = U()
g = PicoGraphics(DISPLAY)
W, H = g.get_bounds()
NPX = W * H
SQUARE = W == H
FB = memoryview(g)          # W x H, 4 bytes/pixel (RGB888), row-major
SCR = bytearray(NPX * 4)

try:
    import secrets as S
except ImportError:
    S = None

def sec(k, d=None):
    v = getattr(S, k, None) if S else None
    return d if v in (None, "") else v

TZ_CFG = int(float(sec("TZ_OFFSET_HOURS", 0)) * 3600)   # fixed offset; HA can send the live offset (DST) in the agenda

BUILTIN = ["clock", "today", "agenda", "scroll_text", "starfield", "matrix", "plasma", "life"]
EXT = [m for m in ("fire", "rainbow", "supercomputer") if (m + ".py") in os.listdir()]   # Pimoroni example effects, if on flash
MODES = BUILTIN[:3] + ["scroll_text"] + EXT + BUILTIN[4:]
ALIAS = {"date": "today"}
ROTS = ["0", "90", "180", "270"] if SQUARE else ["0", "180"]
SFILE = "settings.json"
st = {"on": True, "brightness": 0.5, "mode": "clock", "rotation": 0, "text": "Hello!",
      "speed": 10, "color": [255, 180, 90], "custom": None, "playlist": None}
try:
    with open(SFILE) as f:
        st.update(json.load(f))
except Exception:
    pass
if st["mode"] not in MODES and st["mode"] not in ("custom", "playlist"):
    st["mode"] = "clock"
if str(st["rotation"]) not in ROTS:
    st["rotation"] = 0

dirty_t = None
pub_due = True

def mark():
    global dirty_t, pub_due
    dirty_t = time.ticks_ms()
    pub_due = True

def save_if_due(force=False):
    global dirty_t
    if dirty_t is not None and (force or time.ticks_diff(time.ticks_ms(), dirty_t) > 2000):
        try:
            with open(SFILE + ".tmp", "w") as f:
                json.dump(st, f)
            os.rename(SFILE + ".tmp", SFILE)
        except Exception as e:
            print("save err", e)
        dirty_t = None

def note_reset(why):
    # remembered across the reset so the next boot can report why it restarted
    try:
        with open("reset.txt", "w") as f:
            f.write(why[:120])
    except Exception:
        pass

RESET_WHY = ""
try:
    with open("reset.txt") as f:
        RESET_WHY = f.read()
    os.remove("reset.txt")
except OSError:
    pass

# ---------- rotation layer (applied to every mode) ----------
@micropython.viper
def rot_sq(src: ptr32, dst: ptr32, r: int, n: int):
    m = n - 1
    py = 0
    while py < n:
        px = 0
        while px < n:
            if r == 1:
                v = src[(m - px) * n + py]
            elif r == 2:
                v = src[(m - py) * n + m - px]
            else:
                v = src[px * n + m - py]
            dst[py * n + px] = v
            px += 1
        py += 1
    i = 0
    while i < n * n:
        src[i] = dst[i]
        i += 1

@micropython.viper
def rot_180(src: ptr32, n: int):
    i = 0
    j = n - 1
    while i < j:
        v = src[i]
        src[i] = src[j]
        src[j] = v
        i += 1
        j -= 1

def show():
    r = int(st["rotation"]) // 90
    if r:
        if SQUARE:
            rot_sq(FB, SCR, r, W)
        elif r == 2:
            rot_180(FB, NPX)
    cu.update(g)

def pen(r, gg, b):
    return g.create_pen(max(0, min(255, int(r))), max(0, min(255, int(gg))), max(0, min(255, int(b))))

def clear():
    g.set_pen(0)
    g.clear()

def tzoff():
    a = AG[0]
    if a and isinstance(a.get("tz"), int) and -50400 <= a["tz"] <= 50400:
        return a["tz"]
    return TZ_CFG

def lt(ep=None):
    return time.localtime((time.time() if ep is None else ep) + tzoff())

def centered(txt, y, scale=1):
    w = g.measure_text(txt, scale)
    g.text(txt, (W - w) // 2, y, -1, scale)

# ---------- 3x5 pixel font (deterministic widths; used on every board) ----------
_F = {
 "A": ".#.#.#####.##.#", "B": "##.#.###.#.###.", "C": ".###..#..#...##", "D": "##.#.##.##.###.",
 "E": "####..##.#..###", "F": "####..##.#..#..", "G": ".###..#.##.#.##", "H": "#.##.#####.##.#",
 "I": "###.#..#..#.###", "J": "..#..#..##.#.#.", "K": "#.##.###.#.##.#", "L": "#..#..#..#..###",
 "M": "#.########.##.#", "N": "##.#.##.##.##.#", "O": ".#.#.##.##.#.#.", "P": "##.#.###.#..#..",
 "Q": ".#.#.##.###..##", "R": "##.#.###.#.##.#", "S": ".###...#...###.", "T": "###.#..#..#..#.",
 "U": "#.##.##.##.####", "V": "#.##.##.##.#.#.", "W": "#.##.########.#", "X": "#.##.#.#.#.##.#",
 "Y": "#.##.#.#..#..#.", "Z": "###..#.#.#..###", "0": "####.##.##.####", "1": ".#.##..#..#.###",
 "2": "##...#.#.#..###", "3": "##...#.#...###.", "4": "#.##.####..#..#", "5": "####..##...###.",
 "6": ".###..####.####", "7": "###..#.#..#..#.", "8": "####.#####.####", "9": "####.####..###.",
 "/": "..#..#.#.#..#..", "-": "......###......", "&": ".#.#.#.#.#.#.##", "?": "##...#.#.....#.",
 "+": "....#.###.#....", "(": ".#.#..#..#...#.", ")": ".#...#..#..#.#.", "#": "#.####.#.####.#",
}
_N = {":": ".#.#.", ".": "....#", "'": "##...", "!": "###.#", ",": "...##", " ": None}

def cw(ch):
    if ch in _N:
        return 1 if _N[ch] else 2
    return 3

def tw(t):
    return sum(cw(c) + 1 for c in t) - 1 if t else 0

def ttext(t, x, y, p, x0=0, x1=None):
    if x1 is None:
        x1 = W
    g.set_pen(p)
    for ch in t:
        if x >= x1:
            break
        if ch in _N:
            b = _N[ch]
            if b:
                for r in range(5):
                    if b[r] == "#" and x0 <= x < x1:
                        g.pixel(x, y + r)
            x += cw(ch) + 1
            continue
        b = _F.get(ch) or _F["?"]
        for r in range(5):
            for c in range(3):
                if b[r * 3 + c] == "#" and x0 <= x + c < x1:
                    g.pixel(x + c, y + r)
        x += 4

def tcenter(t, y, p):
    ttext(t, (W - tw(t)) // 2, y, p)

def marquee(t, x, y, p, tick):
    w = tw(t)
    if w <= W - x:
        ttext(t, x, y, p)
    else:
        span = w + 8
        off = (tick // 2) % span
        ttext(t, x - off, y, p, x, W)
        ttext(t, x - off + span, y, p, x, W)

def clean(t):
    t = str(t).upper()[:64]
    return "".join(c for c in t if c in _F or c in _N)

# ---------- modes ----------
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
MONS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
ntp_ok = False
tick = 0

def hhmm(t):
    return "%d:%02d" % (t[3] % 12 or 12, t[4])

def draw_clock():
    t = lt()
    c = st["color"]
    clear()
    sbar = pen(c[0] // 4, c[1] // 4, c[2] // 4)
    if BOARD == "cosmic":
        g.set_font("bitmap6")
        g.set_pen(pen(80, 160, 255))
        centered(DAYS[t[6]] if ntp_ok else "SYNC", 0)
        g.set_font("bitmap8")
        g.set_pen(pen(c[0], c[1], c[2]))
        centered(hhmm(t), 8)
        g.set_font("bitmap6")
        g.set_pen(pen(c[0] // 3, c[1] // 3, c[2] // 3))
        centered("PM" if t[3] >= 12 else "AM", 17)
        g.set_pen(pen(120, 255, 140))
        d = "%s%d" % (MONS[t[1] - 1], t[2])
        if g.measure_text(d, 1) > W:
            d = "%d/%d" % (t[1], t[2])
        centered(d, 24)
    elif BOARD == "galactic":
        g.set_font("bitmap8")
        g.set_pen(pen(c[0], c[1], c[2]))
        g.text(hhmm(t), 1, 1, -1, 1)
        ttext(DAYS[t[6]] if ntp_ok else "SYNC", W - 12, 0, pen(80, 160, 255))
        ds = "%d/%d" % (t[1], t[2])
        ttext(ds, W - tw(ds), 6, pen(120, 255, 140))
    else:  # stellar 16x16
        ts = hhmm(t)
        if tw(ts) > W:
            ts = ts.replace(":", "")
        tcenter(ts if ntp_ok else "SYNC", 2, pen(c[0], c[1], c[2]))
        tcenter(DAYS[t[6]], 9, pen(80, 160, 255))
    g.set_pen(sbar)
    g.rectangle(0, H - 1, (t[5] * W) // 60, 1)

def draw_today():
    t = lt()
    Wp = pen(255, 255, 255)
    Rp = pen(150, 0, 0)
    g.set_pen(Wp)
    g.clear()
    if BOARD == "cosmic":
        g.set_pen(Rp)
        g.rectangle(0, 0, W, 8)
        g.set_font("bitmap6")
        g.set_pen(Wp)
        centered(DAYS[t[6]], 1)
        g.set_font("bitmap8")
        g.set_pen(Rp)
        centered(str(t[2]), 10, 2)
        g.set_font("bitmap6")
        g.set_pen(pen(60, 60, 60))
        centered(MONS[t[1] - 1], 26)
    elif BOARD == "galactic":
        g.set_pen(Rp)
        g.rectangle(0, 0, 14, H)
        ttext(DAYS[t[6]], 1, 3, Wp)
        g.set_font("bitmap8")
        g.set_pen(Rp)
        g.text(str(t[2]), 17, 2, -1, 1)
        ttext(MONS[t[1] - 1], W - 12, 3, pen(60, 60, 60))
    else:
        g.set_pen(Rp)
        g.rectangle(0, 0, W, 6)
        tcenter(DAYS[t[6]], 0, Wp)
        tcenter(str(t[2]), 7, Rp)
        g.set_pen(pen(60, 60, 60))
        g.rectangle(0, 14, W, 1)

scroll_x = 0.0

def draw_scroll():
    global scroll_x
    clear()
    g.set_font("bitmap8")
    txt = st["text"]
    w = g.measure_text(txt, 1)
    c = st["color"]
    g.set_pen(pen(c[0], c[1], c[2]))
    g.text(txt, int(scroll_x), (H - 8) // 2 + (1 if H > 16 else 0), -1, 1)
    scroll_x -= spd() / 10
    if scroll_x < -w:
        scroll_x = float(W)

stars = []
R2 = max(W, H) / 2

def init_stars():
    global stars
    n = max(12, NPX // 15)
    stars = [[random.uniform(-R2, R2), random.uniform(-R2, R2), random.uniform(1, 32)] for _ in range(n)]

def draw_starfield():
    clear()
    sp = spd() / 20
    for s in stars:
        s[2] -= sp
        if s[2] <= 0.5:
            s[0] = random.uniform(-R2, R2); s[1] = random.uniform(-R2, R2); s[2] = 32.0
            continue
        k = 16 / s[2]
        x = int(W / 2 + s[0] * k)
        y = int(H / 2 + s[1] * k)
        if 0 <= x < W and 0 <= y < H:
            b = max(40, int(255 * (1 - s[2] / 32)))
            g.set_pen(pen(b, b, b + 30))
            g.pixel(x, y)
        else:
            s[2] = 0.0

drops = []

def new_drop(d=None):
    v = [random.uniform(-H, 0), random.uniform(0.3, 1.0), random.randint(max(3, H // 8), max(4, H // 2))]
    if d is None:
        return v
    d[0], d[1], d[2] = v

def init_matrix():
    global drops
    drops = [new_drop() for _ in range(W)]

def draw_matrix():
    clear()
    sp = spd() / 10
    for x in range(W):
        d = drops[x]
        d[0] += d[1] * sp
        head = int(d[0])
        n = d[2]
        for i in range(n):
            y = head - i
            if 0 <= y < H:
                if i == 0:
                    g.set_pen(pen(190, 255, 190))
                else:
                    g.set_pen(pen(0, 255 * (n - i) // n, 0))
                g.pixel(x, y)
        if head - n > H:
            new_drop(d)

PAL = []
SIN = [math.sin(i * 2 * math.pi / 256) for i in range(256)]
DIST = bytearray(NPX)
pt = 0.0

def hsv(h, s, v):
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r, gg, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    return pen(r * 255, gg * 255, b * 255)

def init_plasma():
    global PAL
    PAL = [hsv(i / 64, 1.0, 1.0) for i in range(64)]
    for y in range(H):
        for x in range(W):
            DIST[y * W + x] = int(math.sqrt((x - W / 2) ** 2 + (y - H / 2) ** 2) * 8) & 255

@micropython.native
def draw_plasma():
    global pt
    pt += spd() * 0.4
    t = int(pt)
    sx = [SIN[(x * 8 + t) & 255] for x in range(W)]
    sy = [SIN[(y * 10 - t * 2) & 255] for y in range(H)]
    sxy = [SIN[(i * 5 + t) & 255] for i in range(W + H)]
    for y in range(H):
        for x in range(W):
            v = sx[x] + sy[y] + sxy[x + y] + SIN[(DIST[y * W + x] * 2 - t * 3) & 255]
            g.set_pen(PAL[int((v + 4) * 7.9) & 63])
            g.pixel(x, y)

grid = bytearray(NPX)
nxt = bytearray(NPX)
life_gen = 0
life_hist = []

def init_life():
    global life_gen, life_hist
    for i in range(NPX):
        grid[i] = 1 if random.random() < 0.3 else 0
    life_gen = 0
    life_hist = []

@micropython.native
def life_step():
    for y in range(H):
        ym = ((y + H - 1) % H) * W
        y0 = y * W
        yp = ((y + 1) % H) * W
        for x in range(W):
            xm = (x + W - 1) % W
            xp = (x + 1) % W
            n = ((grid[ym + xm] > 0) + (grid[ym + x] > 0) + (grid[ym + xp] > 0) +
                 (grid[y0 + xm] > 0) + (grid[y0 + xp] > 0) +
                 (grid[yp + xm] > 0) + (grid[yp + x] > 0) + (grid[yp + xp] > 0))
            a = grid[y0 + x]
            if a:
                nxt[y0 + x] = min(a + 1, 250) if n == 2 or n == 3 else 0
            else:
                nxt[y0 + x] = 1 if n == 3 else 0
    grid[:] = nxt

def draw_life():
    global life_gen, life_hist
    life_step()
    life_gen += 1
    pop = sum(1 for v in grid if v)
    life_hist.append(pop)
    if len(life_hist) > 30:
        life_hist.pop(0)
    if life_gen > 600 or pop < NPX // 128 + 2 or (len(life_hist) == 30 and max(life_hist) - min(life_hist) < 3):
        init_life()
    clear()
    for i in range(NPX):
        a = grid[i]
        if a:
            if a < 3:
                g.set_pen(pen(120, 255, 255))
            elif a < 20:
                g.set_pen(pen(60, 120, 255))
            else:
                g.set_pen(pen(160, 40, 200))
            g.pixel(i % W, i // W)

# ---------- agenda (calendar data comes ONLY from Home Assistant over local MQTT) ----------
# Topic unicorn/agenda (retained): {"now":epoch,"tz":utc_offset_s,"ev":[{"t":title,"s":start,"e":end,"c":cal}, ...]}
# c = calendar index 0..2 (colour). The board picks current/next itself so it rolls over between updates.
AG = [None, 0]
CALC = ((255, 70, 200), (0, 190, 255), (70, 255, 90))
MAXEV = 16

def agenda_msg(m):
    try:
        d = json.loads(m)
        ev = []
        for e in (d.get("ev") or [])[:MAXEV]:
            if isinstance(e, dict) and isinstance(e.get("s"), int) and isinstance(e.get("e"), int):
                ev.append({"t": clean(e.get("t", "")), "s": e["s"], "e": e["e"], "c": int(e.get("c", 0)) % 3})
        d = {"ev": ev, "tz": d.get("tz")}
        AG[0] = d
        AG[1] = time.ticks_ms()
    except Exception as e:
        print("agenda bad json", e)

def dur(sec_):
    m = (sec_ + 59) // 60
    if m < 60:
        return "%dM" % m
    if m < 600:
        return "%dH%02d" % (m // 60, m % 60)
    return "%dH" % (m // 60)

def cur_next(now):
    cur = nx = None
    for e in (AG[0] or {}).get("ev") or []:
        if e["s"] <= now < e["e"]:
            if cur is None:
                cur = e
        elif e["s"] > now and nx is None:
            nx = e
    return cur, nx

def title_row(e, y, tk, dim):
    c = CALC[e.get("c", 0) % 3]
    g.set_pen(pen(c[0], c[1], c[2]))
    g.rectangle(0, y + 1, 1, 3)            # calendar colour dot
    t = e.get("t") or "BUSY"
    marquee(t, 2, y, pen(255 // dim, 255 // dim, 255 // dim), tk)

def progress(cur, now, y):
    cc = CALC[cur.get("c", 0) % 3]
    span = max(1, cur["e"] - cur["s"])
    n = min(W, (now - cur["s"]) * W // span)
    g.set_pen(pen(cc[0] // 6, cc[1] // 6, cc[2] // 6))
    g.rectangle(0, y, W, 1)
    g.set_pen(pen(cc[0], cc[1], cc[2]))
    if n:
        g.rectangle(0, y, n, 1)

def draw_agenda():
    clear()
    now = time.time()
    t = lt(now)
    c = st["color"]
    ts = hhmm(t) if ntp_ok else "SYNC"
    cur, nx = cur_next(now)
    if BOARD == "cosmic":
        ds = "%d/%d" % (t[1], t[2])
        if tw(ts) + 2 + tw(ds) > W:
            ds = "%d" % t[2]
        ttext(ts, 0, 0, pen(c[0], c[1], c[2]))
        ttext(ds, W - tw(ds), 0, pen(120, 255, 140))
        if AG[0] is None:
            ttext("NO CAL", 0, 12, pen(110, 110, 110))
            ttext("DATA", 0, 18, pen(110, 110, 110))
            return
        if cur:
            title_row(cur, 7, tick, 1)
            ttext("LEFT " + dur(cur["e"] - now), 0, 13, pen(255, 200, 60))
        else:
            ttext("FREE", 0, 7, pen(90, 200, 90))
            if nx:
                ttext("IN " + dur(nx["s"] - now), 0, 13, pen(255, 200, 60))
        g.set_pen(pen(25, 25, 25))
        g.rectangle(0, 19, W, 1)
        if nx:
            title_row(nx, 21, tick + 40, 2)
            ttext(hm_s(nx["s"]), 2, 26, pen(140, 140, 255))
        else:
            ttext("NO NEXT", 0, 21, pen(80, 80, 80))
        if cur:
            progress(cur, now, H - 1)
        return
    # compact layouts: one event (current, else next)
    e = cur or nx
    if BOARD == "galactic":
        ttext(ts, 0, 0, pen(c[0], c[1], c[2]))
        if AG[0] is None:
            ttext("NO CAL", W - tw("NO CAL"), 0, pen(110, 110, 110))
        elif cur:
            s = "LEFT " + dur(cur["e"] - now)
            ttext(s, W - tw(s), 0, pen(255, 200, 60))
        elif nx:
            s = "IN " + dur(nx["s"] - now)
            ttext(s, W - tw(s), 0, pen(255, 200, 60))
        else:
            ttext("FREE", W - tw("FREE"), 0, pen(90, 200, 90))
        if e:
            title_row(e, 5, tick, 1 if cur else 2)
    else:  # stellar
        if tw(ts) > W:
            ts = ts.replace(":", "")
        tcenter(ts, 0, pen(c[0], c[1], c[2]))
        if AG[0] is None:
            tcenter("CAL?", 6, pen(110, 110, 110))
        elif e:
            title_row(e, 5, tick, 1 if cur else 2)
            s = dur((cur["e"] if cur else nx["s"]) - now)
            tcenter(s, 10, pen(255, 200, 60) if cur else pen(140, 140, 255))
        else:
            tcenter("FREE", 8, pen(90, 200, 90))
    if cur:
        progress(cur, now, H - 1)

def hm_s(ep):
    return hhmm(lt(ep))

# ---------- custom displays (uploaded from Unicorn Studio / CLI) ----------
# Stored on flash: custom/<name>.bin = frames of W x H RGB (row-major, upright),
# custom/<name>.json = {"durations": [ms, ...]}. Only one frame is held in RAM at a time.
CDIR = "custom"
PDIR = "playlists"
FRAME = NPX * 3
CHUNK = 1024
NCH = (FRAME + CHUNK - 1) // CHUNK
MAXF = 64
MAXPL = 32
for _d in (CDIR, PDIR):
    try:
        os.mkdir(_d)
    except OSError:
        pass
CBUF = bytearray(FRAME)
LBUF = bytearray(FRAME)
UBUF = bytearray(FRAME)
cust = None      # [file, durations, idx, t_ms, name]
live = False
up = None        # upload in progress: dict

def clen(c):
    return min(CHUNK, FRAME - c * CHUNK)

def _ls(d, ext):
    try:
        return sorted(f[:-len(ext)] for f in os.listdir(d) if f.endswith(ext) and not f.startswith("_"))
    except OSError:
        return []

def customs():
    return _ls(CDIR, ".bin")

def playlists():
    return _ls(PDIR, ".json")

def all_modes():
    return MODES + ["custom:" + n for n in customs()] + ["playlist:" + n for n in playlists()]

def good_name(n):
    return isinstance(n, str) and 0 < len(n) <= 24 and all(c in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in n)

@micropython.viper
def blit_rgb(src: ptr8, dst: ptr32, n: int):
    i = 0
    while i < n:
        j = i * 3
        dst[i] = (src[j] << 16) | (src[j + 1] << 8) | src[j + 2]
        i += 1

def close_custom():
    global cust
    if cust:
        try:
            cust[0].close()
        except Exception:
            pass
    cust = None

def open_custom(n):
    global cust
    close_custom()
    if not n or n not in customs():
        return False
    try:
        with open(CDIR + "/" + n + ".json") as f:
            durs = json.load(f)["durations"]
        f = open(CDIR + "/" + n + ".bin", "rb")
        if f.readinto(CBUF) != FRAME:
            f.close()
            raise ValueError("size mismatch (made for another board?)")
        cust = [f, durs, 0, time.ticks_ms(), n]
        print("custom: playing", n, len(durs), "frames")
        return True
    except Exception as e:
        print("custom load err", n, e)
        close_custom()
        return False

def draw_custom():
    if live:
        blit_rgb(LBUF, FB, NPX)
        return
    if cust is None:
        clear()
        tcenter("NO", H // 2 - 6 if H > 11 else 0, pen(120, 120, 120))
        tcenter("DISP", H // 2 if H > 11 else 6, pen(120, 120, 120))
        return
    f, durs, i, t0, _n = cust
    now = time.ticks_ms()
    if len(durs) > 1 and time.ticks_diff(now, t0) >= durs[i] * 10 // spd():
        i = (i + 1) % len(durs)
        f.seek(i * FRAME)
        f.readinto(CBUF)
        cust[2] = i
        cust[3] = now
    blit_rgb(CBUF, FB, NPX)

# ---------- playlists ----------
# playlists/<name>.json = {"items": [{"mode": "custom:alien_head" | "clock" | ..., "seconds": 10}, ...]}
pl = None        # {"name", "items", "idx", "t", "cur"}

def norm_item(it):
    if not isinstance(it, dict):
        raise ValueError("item not an object")
    m = it.get("mode") or it.get("name") or ""
    if it.get("type") == "custom" and not str(m).startswith("custom:"):
        m = "custom:" + str(m)
    m = ALIAS.get(m, m)
    if not (m in MODES or m in ("fire", "rainbow", "supercomputer") or (m.startswith("custom:") and good_name(m[7:]))):
        raise ValueError("bad item mode %s" % m)
    s = int(it.get("seconds", 10))
    out = {"mode": m, "seconds": max(2, min(3600, s))}
    if it.get("speed") is not None:      # optional per-item speed 1..20 (10 = the display's own timing)
        out["speed"] = max(1, min(20, int(it["speed"])))
    return out

def spd():
    """Animation speed now: the playing playlist item's own speed if it has one, else the global speed.
    st["speed"] itself is never changed by a playlist, so leaving the playlist restores it."""
    if pl and st["mode"] == "playlist" and not live and pl["cur"] is not None:
        s = pl["items"][pl["idx"]].get("speed")
        if s:
            return s
    return st["speed"]

def pl_save(m):
    j = json.loads(m)
    name = j.get("name")
    items = j.get("items") or []
    if not good_name(name) or not (0 < len(items) <= MAXPL):
        raise ValueError("bad playlist name or item count (1..%d)" % MAXPL)
    items = [norm_item(i) for i in items]
    with open(PDIR + "/_tmp.json", "w") as f:
        json.dump({"items": items}, f)
    os.rename(PDIR + "/_tmp.json", PDIR + "/" + name + ".json")
    return name, len(items)

def pl_valid(it):
    m = it["mode"]
    return m in MODES or (m.startswith("custom:") and m[7:] in customs())

def pl_start(name):
    global pl
    try:
        with open(PDIR + "/" + name + ".json") as f:
            items = [norm_item(i) for i in json.load(f)["items"]]
    except Exception as e:
        print("playlist load err", name, e)
        pl = None
        return False
    pl = {"name": name, "items": items, "idx": -1, "t": 0, "cur": None}
    pl_next()
    return True

def pl_next():
    global pub_due
    n = len(pl["items"])
    for _ in range(n):
        pl["idx"] = (pl["idx"] + 1) % n
        it = pl["items"][pl["idx"]]
        if pl_valid(it):
            pl["t"] = time.ticks_ms()
            pl["cur"] = it["mode"]
            render(it["mode"])
            pub_due = True
            return
    pl["cur"] = None        # nothing playable: show the empty screen
    pl["t"] = time.ticks_ms()
    render(None)

def pl_tick(now):
    if pl and pl["cur"] is not None and time.ticks_diff(now, pl["t"]) >= pl["items"][pl["idx"]]["seconds"] * 1000:
        pl_next()
    elif pl and pl["cur"] is None and time.ticks_diff(now, pl["t"]) > 5000:
        pl["t"] = now
        pl_next()

# ---------- render engine ----------
RM = "clock"     # what is being drawn right now (a builtin, "custom" or None)
ext = None       # Pimoroni effect module (fire / rainbow / supercomputer)

def render(m):
    """Switch what is drawn. m = builtin mode, 'custom:<name>' or None (blank)."""
    global RM, ext, scroll_x
    ext = None
    close_custom()
    gc.collect()
    if m is None:
        RM = None
        return
    if m.startswith("custom:"):
        RM = "custom" if open_custom(m[7:]) else "custom"
        return
    RM = m
    try:
        if m in EXT:
            ext = __import__(m)
            ext.graphics = g
            if m == "supercomputer":
                ext.colour = tuple(st["color"])
            ext.init()
        elif m == "starfield":
            init_stars()
        elif m == "matrix":
            init_matrix()
        elif m == "plasma":
            init_plasma()
        elif m == "life":
            init_life()
        elif m == "scroll_text":
            scroll_x = float(W)
    except Exception as e:
        print("mode init err", m, e)
        ext = None
        RM = "clock"

def init_mode():
    global pl
    m = st["mode"]
    if m != "playlist":
        pl = None
    if m == "custom":
        render("custom:" + str(st.get("custom")))
    elif m == "playlist":
        if not pl_start(str(st.get("playlist"))):
            st["mode"] = "clock"
            render("clock")
    else:
        render(m)

def draw_mode():
    if live or RM == "custom":
        draw_custom()
    elif RM is None:
        clear()
        tcenter("EMPTY", (H - 5) // 2, pen(90, 90, 90))
    elif ext is not None:
        if RM == "rainbow":
            ext.speed = spd() / 2
        ext.draw()
    elif RM == "clock":
        draw_clock()
    elif RM == "today":
        draw_today()
    elif RM == "agenda":
        draw_agenda()
    elif RM == "scroll_text":
        draw_scroll()
    elif RM == "starfield":
        draw_starfield()
    elif RM == "matrix":
        draw_matrix()
    elif RM == "plasma":
        draw_plasma()
    elif RM == "life":
        draw_life()

def frame_ms():
    if live or RM == "custom":
        return 20
    if RM in ("clock", "today"):
        return 250
    if RM == "agenda":
        return 120
    if RM in ("fire", "supercomputer", "life"):
        return max(5, 105 - spd() * 5)
    return 20

def mode_label():
    if live:
        return "live"
    if st["mode"] == "custom":
        return "custom:" + str(st.get("custom"))
    if st["mode"] == "playlist":
        return "playlist:" + str(st.get("playlist"))
    return st["mode"]

def set_mode(m):
    global live
    live = False
    m = ALIAS.get(m, m)
    if m.startswith("custom:"):
        n = m[7:]
        if n in customs():
            st["custom"] = n
            st["mode"] = "custom"
            init_mode()
    elif m.startswith("playlist:"):
        n = m[9:]
        if n in playlists():
            st["playlist"] = n
            st["mode"] = "playlist"
            init_mode()
    elif m == "custom":
        st["mode"] = "custom" if st.get("custom") in customs() else "clock"
        init_mode()
    elif m in MODES:
        st["mode"] = m
        init_mode()
    mark()

# ---------- upload protocol ----------
# begin  : JSON {"name", "durations": [...], "id": "<token 1..16 chars>"}       (w/h optional, must match)
# frame  : [frame hi, frame lo, chunk] + bytes, or v2: [0xA5, id_len, id..., frame hi, frame lo, chunk] + bytes
# end    : name (v1) or JSON {"name", "id"}
# acks   : BASE/custom/ack  "<frames written>" (v1)   or  "<id>:<frames written>" (v2)
# result : BASE/custom/result  text (v1)  and  BASE/custom/result2 JSON {"id","ok","msg"} (v2)
# A frame can be re-sent from chunk 0 (retry after a lost packet); stray chunks are ignored.
UP_IDLE_MS = 15000

def up_reset():
    global up
    if up:
        try:
            up["f"].close()
        except Exception:
            pass
    up = None

def custom_msg(k, m):
    global up, live, need_disc
    if k == "custom/live":
        # payload = [chunk] + RGB bytes for that chunk
        if len(m) >= 1 and m[0] < NCH and len(m) == clen(m[0]) + 1:
            c = m[0] * CHUNK
            LBUF[c:c + len(m) - 1] = memoryview(m)[1:]
            if m[0] == NCH - 1 and not live:
                live = True
                st["on"] = True
                mark()
        elif len(m) == 0 and live:
            live = False
            mark()
        return
    if k == "custom/begin":
        j = json.loads(m)
        uid = str(j.get("id", ""))[:16]
        name = j.get("name")
        if up and up["id"] != uid and time.ticks_diff(time.ticks_ms(), up["t"]) < UP_IDLE_MS:
            res("busy: another upload (%s) is in progress" % up["name"], uid, False)
            return
        if ("width" in j and int(j["width"]) != W) or ("height" in j and int(j["height"]) != H):
            raise ValueError("display is %sx%s, this board is %dx%d" % (j.get("width"), j.get("height"), W, H))
        durs = [max(20, min(60000, int(d))) for d in j["durations"]]
        if not good_name(name) or not (0 < len(durs) <= MAXF):
            raise ValueError("bad name or frame count (1..%d)" % MAXF)
        fs = os.statvfs("/")
        if fs[0] * fs[3] < len(durs) * FRAME + 150000:
            raise ValueError("flash full")
        up_reset()
        up = {"f": open(CDIR + "/_up.bin", "wb"), "name": name, "durs": durs, "seq": 0, "id": uid,
              "t": time.ticks_ms()}
        return
    if k == "custom/frame":
        if not up:
            return                       # stray chunk (e.g. after an abort): ignore
        o = 0
        if len(m) > 2 and m[0] == 0xA5:
            il = m[1]
            if bytes(m[2:2 + il]).decode() != up["id"]:
                return                   # chunk from someone else's upload
            o = 2 + il
        fr = (m[o] << 8) | m[o + 1]
        c = m[o + 2]
        seq = fr * NCH + c
        if c == 0 and fr == up["seq"] // NCH:
            up["seq"] = seq              # (re)start of the frame we are waiting for
        elif c == 0 and fr == up["seq"] // NCH - 1 and up["seq"] % NCH == 0:
            n = str(up["seq"] // NCH)    # sender missed our ack for a frame we already have: re-ack
            pub(BASE + "/custom/ack", (up["id"] + ":" + n) if up["id"] else n)
            return
        if seq != up["seq"] or c >= NCH or len(m) != clen(c) + o + 3:
            return                       # ignore; the sender retries the frame after a timeout
        b = c * CHUNK
        UBUF[b:b + clen(c)] = memoryview(m)[o + 3:]
        up["seq"] += 1
        up["t"] = time.ticks_ms()
        if c == NCH - 1:
            # write a whole frame only once the sender has paused (it waits for the ack) -
            # flash writes while Wi-Fi data is arriving stall the cyw43 TCP stream.
            up["f"].seek(fr * FRAME)
            up["f"].write(UBUF)
            n = str(up["seq"] // NCH)
            pub(BASE + "/custom/ack", (up["id"] + ":" + n) if up["id"] else n)
        return
    if k == "custom/end":
        s = bytes(m).decode()
        uid = ""
        if s.startswith("{"):
            j = json.loads(s)
            uid = str(j.get("id", ""))[:16]
        if not up or up["id"] != uid:
            raise ValueError("end without begin")
        name, durs, got = up["name"], up["durs"], up["seq"]
        up["f"].close()
        up = None
        if got != len(durs) * NCH:
            raise ValueError("got %d of %d chunks" % (got, len(durs) * NCH))
        if cust and cust[4] == name:
            close_custom()
        try:
            os.remove(CDIR + "/" + name + ".bin")
        except OSError:
            pass
        os.rename(CDIR + "/_up.bin", CDIR + "/" + name + ".bin")
        with open(CDIR + "/" + name + ".json", "w") as f:
            json.dump({"durations": durs}, f)
        need_disc = True
        live = False
        if not j_noplay(s):
            set_mode("custom:" + name)
            st["on"] = True
        elif pl and pl["cur"] == "custom:" + name:
            render(pl["cur"])            # playlist is showing the display that was just replaced
        elif st["mode"] == "custom" and st.get("custom") == name:
            init_mode()
        mark()
        res("ok %s %d frames" % (name, len(durs)), uid, True)
        return
    if k == "custom/delete":
        name = bytes(m).decode().strip()
        if not good_name(name):
            raise ValueError("bad name")
        if cust and cust[4] == name:
            close_custom()
        for e in (".bin", ".json"):
            try:
                os.remove(CDIR + "/" + name + e)
            except OSError:
                pass
        if st["mode"] == "custom" and st.get("custom") == name:
            st["custom"] = None
            set_mode("clock")
        need_disc = True
        mark()
        res("deleted " + name)

def j_noplay(s):
    return '"play": false' in s or '"play":false' in s

def res(msg, uid="", ok=None, topic="custom"):
    print(topic.upper(), msg)
    if ok is None:
        ok = not msg.startswith("error") and not msg.startswith("busy")
    try:
        pub(BASE + "/" + topic + "/result", msg)
        pub(BASE + "/" + topic + "/result2", json.dumps({"id": uid, "ok": ok, "msg": msg}))
    except Exception:
        pass

def playlist_msg(k, m):
    global need_disc, pub_due
    if k == "playlist/save":
        try:
            uid = str(json.loads(m).get("id", ""))[:16]
        except Exception:
            uid = ""
        name, n = pl_save(m)
        if st["mode"] == "playlist" and st.get("playlist") == name:
            init_mode()
        need_disc = True
        mark()
        pub_due = True
        res("ok playlist %s %d items" % (name, n), uid, True, "playlist")
    elif k == "playlist/get":
        # answer with the stored items so the app can import a playlist that only exists on the board
        name = bytes(m).decode().strip()
        if not good_name(name):
            raise ValueError("bad name")
        try:
            with open(PDIR + "/" + name + ".json") as f:
                items = json.load(f)["items"]
            out = {"name": name, "items": items}
        except Exception:
            out = {"name": name, "error": "no such playlist"}
        pub(BASE + "/playlist/items", json.dumps(out))
    elif k == "playlist/delete":
        name = bytes(m).decode().strip()
        if not good_name(name):
            raise ValueError("bad name")
        try:
            os.remove(PDIR + "/" + name + ".json")
        except OSError:
            pass
        if st["mode"] == "playlist" and st.get("playlist") == name:
            set_mode("clock")
        need_disc = True
        mark()
        res("deleted playlist " + name, "", True, "playlist")

# ---------- network / MQTT ----------
try:
    import rp2
    rp2.country(sec("WIFI_COUNTRY", "US"))
except Exception:
    pass
network.hostname(sec("HOSTNAME", BOARD + "-unicorn"))
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
try:
    wlan.config(pm=0xa11140)    # Wi-Fi power-save off: steadier MQTT
except Exception:
    pass
MAC = wlan.config("mac").hex()
DEV = "unicorn_" + MAC[-6:]
BASE = "unicorn/" + MAC[-6:]
SSID = sec("WIFI_SSID")
PSK = sec("WIFI_PASSWORD")
BROKER = sec("MQTT_BROKER")
PORT = int(sec("MQTT_PORT", 1883))
MUSER = sec("MQTT_USER")
MPASS = sec("MQTT_PASSWORD")
TIME_HOST = sec("TIME_HTTP_HOST", BROKER)
TIME_PORT = int(sec("TIME_HTTP_PORT", 8123))
NTP_HOST = sec("NTP_HOST")          # optional; e.g. your router. Unset = no internet NTP
OTA_KEY = sec("OTA_KEY")            # unset = Wi-Fi firmware updates are disabled (USB only)
MAXPKT = 65536 if OTA_KEY else 16384

mq = None
ip = ""
wifi_t = None
mq_t = None
ntp_t = None
ping_t = 0
statepub_t = 0
need_disc = False
cmsg_t = 0
reboot_at = None
uptime_ms = 0

def state_json():
    c = st["color"]
    return json.dumps({
        "state": "ON" if st["on"] else "OFF",
        "brightness": int(st["brightness"] * 255 + 0.5),
        "color_mode": "rgb", "color": {"r": c[0], "g": c[1], "b": c[2]},
        "effect": mode_label(), "mode": mode_label(), "customs": customs(), "playlists": playlists(), "modes": MODES,
        "playing": (pl["cur"] if pl else None), "rotation": str(st["rotation"]),
        "text": st["text"], "speed": st["speed"], "speed_now": spd(), "ip": ip, "board": BOARD, "width": W, "height": H,
        "uptime": uptime_ms // 1000, "mem_free": gc.mem_free(), "time_synced": ntp_ok, "version": VERSION,
        "ota": bool(OTA_KEY), "reset_reason": RESET_WHY})

def pub(topic, msg, retain=False):
    mq.sock.settimeout(5)
    mq.publish(topic, msg, retain)

def discovery():
    dev = {"identifiers": [DEV], "name": (BOARD[0].upper() + BOARD[1:]) + " Unicorn", "manufacturer": "Pimoroni",
           "model": "%s Unicorn %dx%d" % ((BOARD[0].upper() + BOARD[1:]), W, H), "sw_version": VERSION}
    av = BASE + "/status"
    S_ = BASE + "/state"
    am = all_modes()
    ents = [
        ("light", "", {"name": "Display", "schema": "json", "command_topic": BASE + "/light/set",
                       "state_topic": S_, "brightness": True, "brightness_scale": 255,
                       "supported_color_modes": ["rgb"], "effect": True, "effect_list": am}),
        ("select", "mode", {"name": "Mode", "command_topic": BASE + "/mode/set", "state_topic": S_,
                            "value_template": "{{ value_json.mode }}", "options": am + ["live"], "icon": "mdi:animation"}),
        ("select", "rotation", {"name": "Rotation", "command_topic": BASE + "/rotation/set", "state_topic": S_,
                                "value_template": "{{ value_json.rotation }}", "options": ROTS,
                                "icon": "mdi:screen-rotation", "entity_category": "config"}),
        ("text", "text", {"name": "Scroll text", "command_topic": BASE + "/text/set", "state_topic": S_,
                          "value_template": "{{ value_json.text }}", "max": 255, "icon": "mdi:message-text"}),
        ("number", "speed", {"name": "Speed", "command_topic": BASE + "/speed/set", "state_topic": S_,
                             "value_template": "{{ value_json.speed }}", "min": 1, "max": 20, "step": 1,
                             "mode": "slider", "icon": "mdi:speedometer"}),
        ("button", "reboot", {"name": "Reboot", "command_topic": BASE + "/reboot/set", "payload_press": "PRESS",
                              "device_class": "restart", "entity_category": "config"}),
        ("sensor", "ip", {"name": "IP address", "state_topic": S_, "value_template": "{{ value_json.ip }}",
                          "icon": "mdi:ip-network", "entity_category": "diagnostic"}),
        ("sensor", "uptime", {"name": "Uptime", "state_topic": S_, "value_template": "{{ value_json.uptime }}",
                              "device_class": "duration", "unit_of_measurement": "s",
                              "entity_category": "diagnostic"}),
        ("sensor", "playing", {"name": "Playlist item", "state_topic": S_,
                               "value_template": "{{ value_json.playing or 'none' }}", "icon": "mdi:playlist-play"}),
    ]
    for comp, key, cfg in ents:
        oid = DEV + ("_" + key if key else "")
        cfg["unique_id"] = oid
        cfg["default_entity_id"] = comp + "." + oid
        cfg["availability_topic"] = av
        cfg["device"] = dev
        pub("homeassistant/%s/%s/config" % (comp, oid), json.dumps(cfg), True)

# ---- signed Wi-Fi file update (disabled unless OTA_KEY is set in secrets.py) ----
# payload: name \n seq \n hex(HMAC-SHA256(OTA_KEY, name + "\n" + seq + "\n" + body)) \n body
# seq must be larger than the last accepted one (stops replays of old files). secrets.py can't be written.
def _hmac(key, msg):
    import hashlib
    key = key.encode() if isinstance(key, str) else key
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key = key + b"\0" * (64 - len(key))
    ik = bytes(b ^ 0x36 for b in key)
    ok_ = bytes(b ^ 0x5C for b in key)
    h = hashlib.sha256(ik)
    h.update(msg)
    o = hashlib.sha256(ok_)
    o.update(h.digest())
    return o.digest()

def _hex(b):
    return "".join("%02x" % x for x in b)

def ota(m):
    try:
        if not OTA_KEY:
            raise ValueError("Wi-Fi updates disabled (set OTA_KEY in secrets.py)")
        parts = m.split(b"\n", 3)
        if len(parts) != 4:
            raise ValueError("bad format")
        name, seq, sig, body = parts[0].decode().strip(), parts[1].decode().strip(), parts[2].decode().strip(), parts[3]
        want = _hex(_hmac(OTA_KEY, parts[0] + b"\n" + parts[1] + b"\n" + body))
        diff = len(want) ^ len(sig)
        for a, b in zip(want, sig):
            diff |= ord(a) ^ ord(b)
        if diff:
            raise ValueError("bad signature")
        last = 0
        try:
            with open("ota_seq.txt") as f:
                last = int(f.read())
        except Exception:
            pass
        if int(seq) <= last:
            raise ValueError("replayed or old update")
        ok = len(name) < 32 and all(ch in "abcdefghijklmnopqrstuvwxyz0123456789_." for ch in name) \
            and (name.endswith(".py") or name.endswith(".json")) and name not in ("secrets.py", "boot.py")
        if not ok:
            raise ValueError("bad name")
        if name.endswith(".py"):
            try:
                compile(body.decode(), name, "exec")
            except NameError:
                pass
        if name == "main.py":
            try:
                os.rename("main.py", "main_prev.py")
            except OSError:
                pass
        with open(name + ".tmp", "wb") as f:
            f.write(body)
        os.rename(name + ".tmp", name)
        with open("ota_seq.txt", "w") as f:
            f.write(seq)
        r = "ok %s %d" % (name, len(body))
    except Exception as e:
        r = "error %s" % (e,)
    print("OTA", r)
    pub(BASE + "/ota/result", r)

def on_msg(t, m):
    global need_disc, reboot_at, cmsg_t, pub_due
    t = t.decode()
    if t == "homeassistant/status":
        if m == b"online":
            need_disc = True
        return
    if t == BASE + "/ota/put":
        ota(m)
        return
    if t == "unicorn/agenda":
        agenda_msg(m)
        return
    k = t[len(BASE) + 1:]
    if k.startswith("custom/") or k.startswith("playlist/"):
        cmsg_t = time.ticks_ms()
        if k.endswith("/result") or k.endswith("/result2") or k == "custom/ack" or k == "playlist/items":
            return
        try:
            if k.startswith("custom/"):
                custom_msg(k, m)
            else:
                playlist_msg(k, m)
        except Exception as e:
            uid = ""
            if k in ("custom/begin", "custom/end", "playlist/save"):
                try:
                    uid = str(json.loads(m).get("id", ""))[:16]
                except Exception:
                    pass
            if k == "custom/begin":
                up_reset()
            res("error %s: %s" % (k, e), uid, False, k.split("/")[0])
        return
    try:
        s = m.decode()
    except Exception:
        return
    if k == "refresh/set":
        pub_due = True      # just republish the state: no discovery, no settings write
        return
    print("CMD", k, s[:60])
    try:
        if k == "light/set":
            j = json.loads(s)
            if "state" in j:
                st["on"] = j["state"] == "ON"
            if "brightness" in j:
                st["brightness"] = max(0, min(255, int(j["brightness"]))) / 255
            if "color" in j:
                c = j["color"]
                st["color"] = [max(0, min(255, int(c.get(x, 0)))) for x in ("r", "g", "b")]
                if RM == "supercomputer" and ext:
                    ext.colour = tuple(st["color"])
            if "effect" in j:
                set_mode(str(j["effect"]))
        elif k == "mode/set":
            set_mode(s.strip())
        elif k == "rotation/set":
            r = str(int(float(s)))
            if r in ROTS:
                st["rotation"] = int(r)
        elif k == "text/set":
            st["text"] = s[:255]
            set_mode("scroll_text")
        elif k == "speed/set":
            st["speed"] = max(1, min(20, int(float(s))))
        elif k == "reboot/set":
            reboot_at = time.ticks_add(time.ticks_ms(), 500)
        mark()
    except Exception as e:
        print("cmd err", k, e)

def rx_exact(n):
    b = bytearray(n)
    mv = memoryview(b)
    i = 0
    while i < n:
        k = mq.sock.readinto(mv[i:])
        if not k:
            raise OSError("rx stall")
        i += k
    return b

def rx_skip(n):
    tmp = bytearray(512)
    while n > 0:
        k = mq.sock.readinto(memoryview(tmp)[:min(512, n)])
        if not k:
            raise OSError("rx stall")
        n -= k

def mq_read():
    # Own MQTT reader: never drops back to a fully blocking socket, so a stalled TCP stream
    # raises (-> reconnect) instead of freezing the board. Oversized packets are skipped.
    mq.sock.settimeout(5)
    op = rx_exact(1)[0]
    n = 0
    sh = 0
    for _ in range(4):
        x = rx_exact(1)[0]
        n |= (x & 0x7F) << sh
        sh += 7
        if not x & 0x80:
            break
    else:
        raise OSError("bad MQTT length")
    if n > MAXPKT:
        print("MQTT packet too big, skipped", n)
        rx_skip(n)
        return
    body = rx_exact(n) if n else b""
    if op & 0xF0 == 0x30 and n >= 2:
        tl = (body[0] << 8) | body[1]
        p = 2 + tl + (2 if op & 6 else 0)
        on_msg(bytes(body[2:2 + tl]), bytes(body[p:]))

def mq_drop():
    global mq
    try:
        mq.sock.close()
    except Exception:
        pass
    mq = None
    print("MQTT down")

POLL = None

class _TSock:
    # Stand-in for the socket module inside umqtt.simple: the blocking connect / CONNACK read
    # used to hang with no timeout when the network was wedged, which tripped the watchdog.
    def __init__(self, real):
        self.r = real
        self.getaddrinfo = real.getaddrinfo
    def socket(self, *a):
        so = self.r.socket(*a)
        so.settimeout(6)
        return so
    def __getattr__(self, k):
        return getattr(self.r, k)

mq_fail = 0

def mq_connect():
    global mq, need_disc, POLL, mq_fail
    from umqtt.simple import MQTTClient
    import umqtt.simple as _us
    if not isinstance(_us.socket, _TSock):
        _us.socket = _TSock(_us.socket)      # every socket umqtt opens gets a timeout
    c = MQTTClient(DEV, BROKER, port=PORT, user=MUSER, password=MPASS, keepalive=60)
    c.set_last_will(BASE + "/status", "offline", retain=True)
    c.set_callback(on_msg)
    alive[0] = time.ticks_ms()
    try:
        c.connect(timeout=6)        # newer umqtt: settimeout(timeout) (None would block forever)
    except TypeError:
        c.connect()                 # older umqtt: the _TSock shim already set a timeout
    mq = c
    import select
    POLL = select.poll()
    POLL.register(c.sock, select.POLLIN)
    c.sock.settimeout(10)
    c.subscribe(BASE + "/+/set")
    if OTA_KEY:
        c.subscribe(BASE + "/ota/put")
    for k in ("begin", "frame", "end", "delete", "live"):
        c.subscribe(BASE + "/custom/" + k)
    c.subscribe(BASE + "/playlist/save")
    c.subscribe(BASE + "/playlist/delete")
    c.subscribe(BASE + "/playlist/get")
    c.subscribe("unicorn/agenda")
    c.subscribe("homeassistant/status")
    discovery()
    pub(BASE + "/status", "online", True)
    pub(BASE + "/state", state_json(), True)
    need_disc = False
    mq_fail = 0
    print("MQTT up", DEV)

def http_time():
    # HA's HTTP Date header is UTC to the second (works where NTP is blocked).
    import socket
    so = socket.socket()
    so.settimeout(3)
    try:
        so.connect(socket.getaddrinfo(TIME_HOST, TIME_PORT)[0][-1])
        so.send(b"HEAD / HTTP/1.0\r\nHost: ha\r\n\r\n")
        d = so.recv(800)
    finally:
        so.close()
    i = d.find(b"Date: ")
    if i < 0:
        raise ValueError("no Date header")
    p = d[i + 6:d.find(b"\r\n", i)].decode().split()
    yr, mon, day = int(p[3]), MONS.index(p[2].upper()) + 1, int(p[1])
    hh, mm, ss = [int(x) for x in p[4].split(":")]
    if yr < 2024:
        raise ValueError("bad date")
    wd = time.localtime(time.mktime((yr, mon, day, 0, 0, 0, 0, 0)))[6]
    machine.RTC().datetime((yr, mon, day, wd, hh, mm, ss, 0))

def net_tick(now):
    global wifi_t, mq_t, ntp_t, ntp_ok, ip, mq_fail
    if not SSID:
        return
    if not wlan.isconnected():
        if mq is not None:
            mq_drop()
        ip = ""
        ws = wlan.status()
        el = time.ticks_diff(now, wifi_t) if wifi_t is not None else 0
        if wifi_t is None or (ws < 0 and el > 5000) or (ws not in (1, 2) and el > 45000) or el > 100000:
            wifi_t = now
            print("WIFI connecting, last status", ws)
            try:
                wlan.disconnect()
                wlan.connect(SSID, PSK)
            except Exception as e:
                print("wifi err", e)
        return
    if not ip:
        ip = wlan.ifconfig()[0]
        print("WIFI up")
    if TIME_HOST and (ntp_t is None or time.ticks_diff(now, ntp_t) > (6 * 3600000 if ntp_ok else 60000)):
        ntp_t = now
        try:
            http_time()
            ntp_ok = True
            print("TIME ok (http)")
        except Exception as e:
            print("HTTP time err", e)
            if NTP_HOST:
                try:
                    import ntptime
                    ntptime.host = NTP_HOST
                    ntptime.timeout = 1
                    ntptime.settime()
                    ntp_ok = True
                    print("TIME ok (ntp)")
                except Exception as e:
                    print("NTP err", e)
    if mq is None and BROKER and MUSER and (mq_t is None or time.ticks_diff(now, mq_t) > 10000):
        mq_t = now
        try:
            mq_connect()
        except Exception as e:
            mq_fail += 1
            print("MQTT err", e, "fails", mq_fail)
            mq_drop()
            if mq_fail >= 4:            # stuck TCP stack: restart Wi-Fi instead of waiting for a watchdog reset
                mq_fail = 0
                print("WIFI restart after MQTT failures")
                try:
                    wlan.disconnect()
                except Exception:
                    pass
                wifi_t = None

# ---------- buttons ----------
BTNS = (U.SWITCH_A, U.SWITCH_B, U.SWITCH_C, U.SWITCH_D, U.SWITCH_SLEEP)
held = set()
D_CYCLE = ["clock", "today", "agenda", "scroll_text", "starfield", "matrix", "plasma", "life", "custom"]

def ext_or(m):
    return m if m in MODES else "plasma"

def buttons():
    for b in BTNS:
        p = cu.is_pressed(b)
        if p and b not in held:
            held.add(b)
            if b == U.SWITCH_SLEEP:
                st["on"] = not st["on"]
                mark()
            else:
                st["on"] = True
                if b == U.SWITCH_A:
                    set_mode(ext_or("fire"))
                elif b == U.SWITCH_B:
                    set_mode(ext_or("supercomputer"))
                elif b == U.SWITCH_C:
                    set_mode(ext_or("rainbow"))
                else:
                    m = st["mode"]
                    set_mode(D_CYCLE[(D_CYCLE.index(m) + 1) % len(D_CYCLE)] if m in D_CYCLE else "clock")
        elif not p and b in held:
            held.discard(b)
    if st["on"]:
        d = 0
        if cu.is_pressed(U.SWITCH_BRIGHTNESS_UP):
            d = 0.01
        if cu.is_pressed(U.SWITCH_BRIGHTNESS_DOWN):
            d = -0.01
        if d:
            st["brightness"] = max(0.0, min(1.0, st["brightness"] + d))
            mark()

# ---- soft watchdog: if the main loop stalls >45 s (e.g. a wedged Wi-Fi socket), reboot.
# Settings (incl. the custom display / playlist) are on flash, so the board comes back showing the same thing.
alive = [time.ticks_ms()]
def _wd(_t):
    if time.ticks_diff(time.ticks_ms(), alive[0]) > 45000:
        note_reset("watchdog: main loop stalled")
        machine.reset()
WD = machine.Timer(period=5000, mode=machine.Timer.PERIODIC, callback=_wd)

# ---- MAIN LOOP ----
def run():
    global pub_due, ping_t, statepub_t, need_disc, uptime_ms, tick
    print("Unicorn", BOARD, W, H, VERSION, DEV, "mode", st["mode"], "last reset:", RESET_WHY or "-")
    init_mode()
    last = time.ticks_ms()
    frame_t = 0
    gc_t = 0
    blank = False
    errs = 0
    last_err = ""
    while True:
        try:
            now = time.ticks_ms()
            alive[0] = now
            uptime_ms += time.ticks_diff(now, last)
            last = now
            net_tick(now)
            if mq is not None:
                try:
                    for _ in range(8):
                        if not POLL.poll(0):
                            break
                        if gc.mem_free() < 80000:
                            gc.collect()
                        mq_read()
                        alive[0] = time.ticks_ms()       # progress made; still bounded by the socket timeouts
                        if time.ticks_diff(alive[0], now) > 250:
                            break                         # keep drawing during upload bursts
                    if up is not None and time.ticks_diff(now, up["t"]) > 60000:
                        up_reset()       # abandoned upload
                    if need_disc and up is None and time.ticks_diff(now, cmsg_t) > 3000:
                        discovery()
                        need_disc = False
                        pub_due = True
                    if time.ticks_diff(now, ping_t) > 30000:
                        ping_t = now
                        mq.sock.settimeout(5)
                        mq.ping()
                    if pub_due or time.ticks_diff(now, statepub_t) > 60000:
                        pub(BASE + "/state", state_json(), True)
                        pub_due = False
                        statepub_t = now
                except Exception as e:
                    print("MQTT io", e)
                    mq_drop()
            buttons()
            if st["mode"] == "playlist" and not live:
                pl_tick(now)
            if st["on"]:
                blank = False
                if time.ticks_diff(now, frame_t) >= frame_ms():
                    frame_t = now
                    tick += 1
                    cu.set_brightness(st["brightness"])
                    draw_mode()
                    show()
            elif not blank:
                clear()
                cu.update(g)
                blank = True
            save_if_due()
            if time.ticks_diff(now, gc_t) > 3000:
                gc_t = now
                gc.collect()
            if reboot_at is not None and time.ticks_diff(now, reboot_at) > 0:
                save_if_due(True)
                note_reset("reboot requested")
                machine.reset()
            errs = 0
        except KeyboardInterrupt:
            raise
        except Exception as e:
            errs += 1
            last_err = repr(e)
            print("loop err", e)
            time.sleep_ms(200)
            if errs > 100:
                note_reset("loop errors: " + last_err)
                machine.reset()
        time.sleep_ms(2)

try:
    run()
except KeyboardInterrupt:
    WD.deinit()   # stopped from USB (mpremote): don't reboot under the REPL
    raise
