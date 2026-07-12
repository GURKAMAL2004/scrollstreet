"""Generate assets/scrollstreet.ico - a map-dots skyline rising like a chart."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "scrollstreet.ico"

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# dark rounded tile
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=52, fill=(11, 13, 16, 255),
                    outline=(36, 43, 51, 255), width=4)

# scattered "map" dots in the validated palette, faint
import random

rng = random.Random(7)
palette = [(57, 135, 229), (25, 158, 112), (201, 133, 0), (144, 133, 233),
           (230, 103, 103), (213, 81, 129)]
for _ in range(90):
    x, y = rng.randint(28, S - 28), rng.randint(28, S - 28)
    c = rng.choice(palette)
    r = rng.randint(3, 6)
    d.ellipse([x - r, y - r, x + r, y + r], fill=(*c, 70))

# the rising line: a scroll through the universe
pts = [(44, 196), (104, 148), (140, 168), (212, 72)]
d.line(pts, fill=(57, 135, 229, 255), width=14, joint="curve")
# bright nodes on the line
node_colors = [(201, 133, 0), (25, 158, 112), (144, 133, 233), (230, 103, 103)]
for (x, y), c in zip(pts, node_colors):
    d.ellipse([x - 13, y - 13, x + 13, y + 13], fill=(11, 13, 16, 255))
    d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=(*c, 255))

img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"icon -> {OUT}")
