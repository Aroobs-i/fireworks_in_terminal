"""
Terminal Fireworks 

Run:        python fireworks5.py
Quit:       Ctrl+C

Options (add after the file name):
    --trio-only    only the three main fireworks, no extra rockets in between
    --no-rockets   skip the rockets: fireworks just burst in place (like v3)
    --blocks       chunkier but works in any font (use this if you see boxes or odd symbols)
    --256          for terminals with only 256 colors (e.g. macOS Terminal.app)

How the picture is drawn
------------------------
Normal characters are big, so the picture would look pixelated. Instead we use
BRAILLE characters like "⣿". Each one is a tiny 2 x 4 grid of dots, so every
character cell gives us 8 dots to switch on or off. That is 4 times finer than
using whole blocks, which is what makes the thin spikes look sharp.
"""

import math
import os
import random
import shutil
import sys
import time

# This empty call switches on color support in the Windows terminal.
# On Mac/Linux it does nothing, so it is safe to leave in.
os.system("")

# ---------- Settings you can play with ----------
FPS = 30               # frames per second
BURST_FRAMES = 80      # how long one firework burst lasts (80 frames = about 2.7 seconds)
CORE_FRAMES = 9        # how long the bright starting ball lasts
SPIKE_DENSITY = 2.2    # spikes per dot of radius: more = denser burst
ROCKET_BASE_FRAMES = 20  # even a short climb takes at least this long (a higher burst takes longer)
EXTRA_GAP = (18, 40)   # an extra rocket launches on its own every 18 to 40 frames
MAX_FIREWORKS = 8      # never more than this many fireworks in the sky at once
# frames until the next round of three starts (shorter than one round lasts, so rounds overlap)
SALVO_GAP = (75, 100) if "--trio-only" not in sys.argv else (120, 140)
GLOW = True            # faint warm light around rockets and explosions
GLOW_FRAMES = 26       # how long the light lasts after an explosion

ROCKETS = "--no-rockets" not in sys.argv
EXTRAS = "--trio-only" not in sys.argv
TRUECOLOR = "--256" not in sys.argv
BLOCKS = "--blocks" in sys.argv
USE_GLOW = GLOW and TRUECOLOR and not BLOCKS

# The three fireworks: (position across the screen, size compared to the big one,
# delay in frames before it launches). The big one in the middle goes last.
LAYOUT = [
    (0.17, 0.62, 0),     # small, left
    (0.83, 0.62, 8),     # small, right
    (0.50, 1.00, 18),    # BIG, middle
]

# ---------- Colors sampled from the gif (R, G, B) and how common each is ----------
PALETTE = [
    ((224, 217, 204), 30),   # cream white
    ((198, 191, 182), 6),    # soft grey-cream
    ((211, 195, 132), 22),   # pale gold
    ((169, 153, 114), 20),   # tan gold
    ((118, 89, 52), 12),     # brown amber
    ((192, 104, 72), 10),    # orange-brown accent
]
PALETTE_COLORS = [c for c, _ in PALETTE]
PALETTE_WEIGHTS = [w for _, w in PALETTE]
EMBER = (104, 56, 32)        # every spark slowly cools down to this brown
SPARKLE = (250, 246, 235)    # the white-hot flash on a spark
HOT_WHITE = (255, 250, 240)  # the middle of the starting ball and the rocket head
BLACK = (0, 0, 0)
PALE_GOLD = (211, 195, 132)
SPARK_TONES = [PALE_GOLD, (169, 153, 114), (192, 104, 72)]   # colors of the rocket's trail

# Which Braille dot belongs to which spot in the 2 x 4 grid of a character
BRAILLE_BITS = [0x01, 0x02, 0x04, 0x40,    # left column, top to bottom
                0x08, 0x10, 0x20, 0x80]    # right column, top to bottom


# ---------- Small helper functions ----------
def mix(a, b, t):
    """Blend color a into color b. t=0 gives a, t=1 gives b."""
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def smoothstep(lo, hi, x):
    """Goes smoothly from 0 (when x<=lo) to 1 (when x>=hi)."""
    t = max(0.0, min(1.0, (x - lo) / (hi - lo)))
    return t * t * (3 - 2 * t)


def shade(color, brightness):
    """Make a color brighter/darker. Rounded to steps of 8 so the terminal
    gets fewer different color codes to print (that keeps it fast)."""
    return tuple(max(0, min(255, int(c * brightness))) // 8 * 8 for c in color)


def pick_palette_color():
    return random.choices(PALETTE_COLORS, PALETTE_WEIGHTS)[0]


# ---------- The drawing surface ----------
class Canvas:
    """A grid of tiny dots. We only store the dots that are lit."""

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.pix = {}   # (x, y) -> (r, g, b)

    def plot(self, x, y, color):
        x = int(round(x))
        y = int(round(y))
        if 0 <= x < self.w and 0 <= y < self.h:
            old = self.pix.get((x, y))
            if old is None:
                self.pix[(x, y)] = color
            else:  # two sparks on the same dot: keep the brighter value of each channel
                self.pix[(x, y)] = (max(old[0], color[0]),
                                    max(old[1], color[1]),
                                    max(old[2], color[2]))


# ---------- The rocket that climbs before the explosion ----------
class Rocket:
    """A glowing shell that climbs from the bottom of the screen, leaving a trail
    of sparks, slows down near the top and then explodes."""

    def __init__(self, cx, cy, size, w, h):
        self.cx = cx                    # where it will explode
        self.cy = cy
        self.size = size
        self.sx = cx + random.uniform(-0.03, 0.03) * w   # where it launches from
        self.sy = h - 2
        distance = self.sy - cy
        self.frames = int(ROCKET_BASE_FRAMES + distance * 0.2)   # higher burst = longer climb
        self.age = 0
        self.phase = random.uniform(0, 2 * math.pi)
        self.pos = (self.sx, self.sy)
        self.sparks = []   # each trail spark: [x, y, vx, vy, life, max_life, color]

    def position(self, age):
        k = min(age / self.frames, 1.0)
        e = 1 - (1 - k) ** 2            # fast at first, then slowing down near the top
        wobble = math.sin(age * 0.8 + self.phase) * 1.2 * (1 - e)   # tiny shimmer sideways
        return (self.sx + (self.cx - self.sx) * e + wobble,
                self.sy + (self.cy - self.sy) * e)

    def draw(self, canvas):
        plot = canvas.plot

        if self.age < self.frames:
            x, y = self.position(self.age)
            self.pos = (x, y)
            # which way are we moving? (needed to draw the streak behind the head)
            px, py = self.position(self.age - 1) if self.age > 0 else (x, y + 2)
            dx, dy = x - px, y - py
            n = math.hypot(dx, dy) or 1.0
            dx, dy = dx / n, dy / n

            # leave a few fresh sparks behind us
            for _ in range(2 + int(self.size / 20)):
                life = random.randint(10, 20)
                self.sparks.append([x + random.uniform(-0.8, 0.8),
                                    y + random.uniform(0.0, 1.5),
                                    random.uniform(-0.22, 0.22),    # drifts sideways a little
                                    random.uniform(0.0, 0.3),       # and starts to fall
                                    life, life, random.choice(SPARK_TONES)])

            # a short glowing streak right behind the head
            for i in range(1, 11):
                f = i / 11
                plot(x - dx * i, y - dy * i, shade(mix(HOT_WHITE, PALE_GOLD, f), 1 - f * 0.8))
            # the bright head: a little 2 x 2 square of white-hot dots
            head = shade(HOT_WHITE, 1.0)
            for ox in (0, 1):
                for oy in (0, 1):
                    plot(x + ox, y + oy, head)

        # move the trail sparks (they keep falling and fading even after the rocket is gone)
        alive = []
        for s in self.sparks:
            s[0] += s[2]
            s[1] += s[3]
            s[3] += 0.03                 # gravity
            s[4] -= 1
            if s[4] > 0:
                alive.append(s)
                f = 1 - s[4] / s[5]      # 0 = fresh, 1 = burnt out
                if random.random() < 0.85:   # random flicker
                    color = mix(mix(HOT_WHITE, s[6], min(1.0, f * 3)), EMBER, f)
                    plot(s[0], s[1], shade(color, (1 - f) ** 1.0 * random.uniform(0.8, 1.0) * 1.25))
        self.sparks = alive
        self.age += 1

    def make_burst(self):
        return Burst(self.cx, self.cy, self.size)

    def finished(self):
        return self.age > self.frames and not self.sparks


# ---------- One thin spike of the firework ----------
class Spike:
    def __init__(self, size, angle, is_long):
        self.dx = math.cos(angle)                       # direction it flies in
        self.dy = math.sin(angle)
        # long and short spikes take turns, which gives a neat flower-like shape
        if is_long:
            self.reach = size * random.uniform(0.90, 1.0)
            self.length = self.reach * random.uniform(0.45, 0.70)   # how long its tail is
        else:
            self.reach = size * random.uniform(0.66, 0.84)
            self.length = self.reach * random.uniform(0.32, 0.50)
        self.life = BURST_FRAMES * random.uniform(0.82, 1.0)
        self.color = pick_palette_color()
        self.gravity = size * 0.00009                   # sparks sag a little near the end

    def draw(self, canvas, cx, cy, age):
        p = age / self.life            # progress: 0 = just born, 1 = burnt out
        if p >= 1:
            return

        # Fast at first, then slowing down (like a real shell). This curve does that.
        head = self.reach * (1 - (1 - p) ** 2.4)
        # The tail shrinks as the spark slows, so long spikes turn into dots.
        # "inner" slowly opens a hollow middle so the spikes stay separate and clear.
        inner = self.reach * 0.10 * smoothstep(0.05, 0.3, p)
        tail = min(head, max(inner, head - self.length * (1 - p) ** 0.55))
        drift = self.gravity * max(0, age - 25) ** 2    # falls a bit in the second half

        cool = smoothstep(0.6, 1.0, p)                  # turns brown as it cools
        fade = 1 - smoothstep(0.82, 1.0, p)             # then fades away
        base = mix(self.color, EMBER, cool)
        tip = mix(base, SPARKLE, 0.55)                  # the glowing tip of the spike

        steps = int(head - tail) + 1
        plot = canvas.plot
        for i in range(steps + 1):
            u = i / steps               # 0 = tail end, 1 = bright head
            # near the end the spike breaks up into dots
            if p > 0.6 and random.random() < (p - 0.6) * 1.9:
                continue
            d = tail + (head - tail) * u
            x = cx + self.dx * d
            y = cy + self.dy * d + drift
            if i >= steps - 1:          # the last two dots are the bright tip
                color, b = tip, fade
                if p > 0.35 and random.random() < 0.35:
                    color = SPARKLE     # random white-hot twinkle
            else:
                color = base
                b = (0.62 + 0.38 * u) * 1.12 * fade * random.uniform(0.88, 1.0)
            plot(x, y, shade(color, b))


# ---------- One whole firework burst ----------
class Burst:
    def __init__(self, cx, cy, size):
        self.cx = cx
        self.cy = cy
        self.size = size
        self.age = 0
        n = max(8, int(size * SPIKE_DENSITY))
        self.spikes = []
        for i in range(n):
            # evenly spaced around the circle, with a little wobble so it looks natural
            angle = (i + random.uniform(-0.35, 0.35)) / n * 2 * math.pi
            self.spikes.append(Spike(size, angle, i % 2 == 0))

    def draw_core(self, canvas):
        """The bright grainy ball at the very start that dissolves into spikes."""
        if self.age >= CORE_FRAMES:
            return
        k = self.age / CORE_FRAMES
        radius = self.size * (0.12 + 0.30 * k)
        density = 0.85 * (1 - k)
        for _ in range(int(radius * radius * 2) + 4):
            a = random.uniform(0, 2 * math.pi)
            d = radius * math.sqrt(random.random())
            if random.random() < density:
                # white-hot in the middle, palette colors toward the edge
                color = mix(HOT_WHITE, pick_palette_color(), d / radius)
                canvas.plot(self.cx + math.cos(a) * d, self.cy + math.sin(a) * d,
                            shade(color, random.uniform(0.85, 1.0)))

    def draw(self, canvas):
        self.draw_core(canvas)
        for s in self.spikes:
            s.draw(canvas, self.cx, self.cy, self.age)
        self.age += 1

    def finished(self):
        return self.age > BURST_FRAMES


def big_size(w, h):
    """Radius (in dots) of the big firework. Everything else is sized from this."""
    return min(w * 0.185, h * 0.44)


# ---------- The show: three main fireworks plus extras, overlapping ----------
class Scene:
    def __init__(self):
        self.t = 0
        self.rockets = []       # rockets that are climbing right now
        self.bursts = []        # fireworks that are exploding right now
        self.queue = []         # fireworks waiting for their launch frame
        self.next_salvo = 0     # when the next group of three starts
        self.next_extra = 30    # when the next extra rocket launches

    def new_rocket(self, fx, rel, w, h):
        big = big_size(w, h)
        size = max(big * rel * random.uniform(0.92, 1.0), 6)
        cx = w * fx + random.uniform(-0.02, 0.02) * w
        # the big one explodes a little higher than the small ones
        cy = h * (0.46 if rel >= 1 else 0.54) + random.uniform(-0.03, 0.03) * h
        cy = min(max(cy, size + 2), h - size - 6)
        return Rocket(cx, cy, size, w, h)

    def new_extra_rocket(self, w, h):
        """A rocket that goes off on its own, at a random spot that isn't crowded."""
        size = max(big_size(w, h) * random.uniform(0.30, 0.65), 6)   # smaller, like farther away
        # everything already in the sky (or about to launch), as (x, y, radius)
        others = [(r.cx, r.cy, r.size) for r in self.rockets]
        others += [(b.cx, b.cy, b.size) for b in self.bursts if b.age < BURST_FRAMES * 0.75]
        others += [(w * fx, h * 0.5, big_size(w, h) * rel) for _, fx, rel in self.queue]
        best, best_gap = None, None
        for _ in range(8):                        # try 8 random spots, keep the emptiest one
            cx = random.uniform(size + 2, max(size + 3, w - size - 2))
            cy = random.uniform(0.22, 0.55) * h
            cy = min(max(cy, size + 2), h - size - 6)
            # how much free space is there between this spot and the nearest firework?
            gap = min((math.hypot(cx - ox, cy - oy) - size - orad for ox, oy, orad in others),
                      default=1e9)
            if best_gap is None or gap > best_gap:
                best, best_gap = (cx, cy), gap
        return Rocket(best[0], best[1], size, w, h)

    def in_the_sky(self):
        """How many fireworks are climbing or properly bursting (not just faint embers)."""
        return len(self.rockets) + sum(1 for b in self.bursts if b.age < BURST_FRAMES * 0.75)

    def launch(self, rocket):
        if ROCKETS:
            self.rockets.append(rocket)
        else:
            self.bursts.append(rocket.make_burst())   # no rocket: explode right away

    def reset(self):
        self.rockets.clear()
        self.bursts.clear()
        self.queue.clear()

    def frame(self, w, h):
        # 1) the main round of three
        if self.t >= self.next_salvo:
            delays = [d for _, _, d in LAYOUT]
            if random.random() < 0.5:          # sometimes the right small one goes first
                delays[0], delays[1] = delays[1], delays[0]
            for (fx, rel, _), delay in zip(LAYOUT, delays):
                self.queue.append((self.t + delay + random.randint(0, 3), fx, rel))
            # the next round starts BEFORE this one has finished, so the sky never empties
            self.next_salvo = self.t + random.randint(*SALVO_GAP)

        for item in self.queue[:]:
            when, fx, rel = item
            if self.t >= when:
                self.launch(self.new_rocket(fx, rel, w, h))
                self.queue.remove(item)

        # 2) extra rockets that go off on their own, anywhere in the sky
        if EXTRAS and self.t >= self.next_extra:
            if self.in_the_sky() < MAX_FIREWORKS:
                self.launch(self.new_extra_rocket(w, h))
                self.next_extra = self.t + random.randint(*EXTRA_GAP)
            else:
                self.next_extra = self.t + 5   # the sky is full: try again in a moment

        canvas = Canvas(w, h)
        for r in self.rockets:
            r.draw(canvas)
            if r.age == r.frames:              # it reached the top of its climb: explode!
                self.bursts.append(r.make_burst())
        self.rockets = [r for r in self.rockets if not r.finished()]
        for b in self.bursts:
            b.draw(canvas)
        self.bursts = [b for b in self.bursts if not b.finished()]
        self.t += 1
        return canvas


# ---------- Turning dots into terminal text ----------
_codes = {}


def color_code(rgb, is_background):
    key = (rgb, is_background)
    code = _codes.get(key)
    if code is None:
        r, g, b = rgb
        kind = 48 if is_background else 38
        if TRUECOLOR:
            code = f"\033[{kind};2;{r};{g};{b}m"
        else:  # fall back to the 256-color palette
            n = 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)
            code = f"\033[{kind};5;{n}m"
        _codes[key] = code
    return code


def build_cells(canvas):
    """Group the lit dots into character cells: cell -> [dot bits, brightness, color]."""
    cells = {}
    for (x, y), color in canvas.pix.items():
        key = (x >> 1, y >> 2)                      # 2 dots across, 4 dots down per cell
        bit = BRAILLE_BITS[(x & 1) * 4 + (y & 3)]
        brightness = color[0] + color[1] + color[2]
        cell = cells.get(key)
        if cell is None:
            cells[key] = [bit, brightness, color]
        else:
            cell[0] |= bit
            if brightness > cell[1]:                # a cell can only have one color,
                cell[1] = brightness                # so it takes the brightest dot's color
                cell[2] = color
    return cells


def glow_map(scene, cols, rows):
    """A faint warm light around each rocket and around each explosion."""
    lights = []   # (x, y, radius, strength), all measured in dots
    for b in scene.bursts:
        if b.age < GLOW_FRAMES:
            lights.append((b.cx, b.cy, b.size * 1.35, (1 - b.age / GLOW_FRAMES) ** 2 * 0.20))
    for r in scene.rockets:
        if r.age < r.frames:
            lights.append((r.pos[0], r.pos[1], 9, 0.13))

    glow = {}
    for lx, ly, radius, strength in lights:
        rx, ry = radius / 2, radius / 4              # radius in character cells
        cx, cy = lx / 2, ly / 4
        for row in range(max(0, int(cy - ry)), min(rows, int(cy + ry) + 1)):
            for col in range(max(0, int(cx - rx)), min(cols, int(cx + rx) + 1)):
                dx = (col + 0.5 - cx) / rx
                dy = (row + 0.5 - cy) / ry
                d2 = dx * dx + dy * dy
                if d2 < 1:
                    glow[(col, row)] = glow.get((col, row), 0.0) + (1 - d2) ** 2 * strength
    return glow


def glow_color(v):
    v = min(v, 0.35)
    return (int(v * 250) // 6 * 6, int(v * 185) // 6 * 6, int(v * 100) // 6 * 6)


def render_braille(canvas, cols, rows, glow):
    cells = build_cells(canvas)
    out = []
    cur_fg = cur_bg = None
    for row in range(rows):
        out.append(f"\033[{row + 1};1H")          # jump to the start of this row
        for col in range(cols):
            g = glow.get((col, row)) if glow else None
            bg = glow_color(g) if g else BLACK
            if bg != cur_bg:                       # only print a color code when it changes
                out.append(color_code(bg, True))
                cur_bg = bg
            cell = cells.get((col, row))
            if cell is None:
                out.append(" ")
            else:
                if cell[2] != cur_fg:
                    out.append(color_code(cell[2], False))
                    cur_fg = cell[2]
                out.append(chr(0x2800 + cell[0]))  # 0x2800 is the empty Braille character
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def render_blocks(canvas):
    """Chunkier fallback: each character shows two stacked square pixels."""
    out = []
    cur_fg = cur_bg = None
    pix = canvas.pix
    for row in range(canvas.h // 2):
        out.append(f"\033[{row + 1};1H")
        for x in range(canvas.w):
            top = pix.get((x, row * 2))
            bottom = pix.get((x, row * 2 + 1))
            if top is None and bottom is None:
                ch, fg, bg = " ", None, BLACK
            elif bottom is None:
                ch, fg, bg = "▀", top, BLACK
            elif top is None:
                ch, fg, bg = "▄", bottom, BLACK
            else:
                ch, fg, bg = "▀", top, bottom
            if fg is not None and fg != cur_fg:
                out.append(color_code(fg, False))
                cur_fg = fg
            if bg != cur_bg:
                out.append(color_code(bg, True))
                cur_bg = bg
            out.append(ch)
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def main():
    scene = Scene()
    size = None
    # switch to a clean screen, hide the cursor
    sys.stdout.write("\033[?1049h\033[?25l\033[2J")
    try:
        while True:
            started = time.time()
            cols, rows = shutil.get_terminal_size((100, 30))
            rows -= 1                              # keep the last line free
            if (cols, rows) != size:               # window was resized: start fresh
                size = (cols, rows)
                scene.reset()
                sys.stdout.write("\033[2J")
            if BLOCKS:
                canvas = scene.frame(cols, rows * 2)
                render_blocks(canvas)
            else:
                canvas = scene.frame(cols * 2, rows * 4)
                glow = glow_map(scene, cols, rows) if USE_GLOW else None
                render_braille(canvas, cols, rows, glow)
            time.sleep(max(0.0, 1 / FPS - (time.time() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        # put the terminal back the way we found it
        sys.stdout.write("\033[0m\033[?25h\033[?1049l")
        sys.stdout.flush()


if __name__ == "__main__":
    main()