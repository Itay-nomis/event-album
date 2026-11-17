"""Generate fake "phone photos" for the load test.

Runs inside a throwaway python container (see run-loadtest.ps1), so nothing is installed
on your PC. Most images are sharp; about 1 in 8 is blurred on purpose, so you can watch the
worker's blur filter drop them.

Usage: python generate_images.py <output-dir> [count]
"""

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def make_photo(rng: random.Random, width: int, height: int) -> Image.Image:
    img = Image.new("RGB", (width, height), tuple(rng.randint(0, 255) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(rng.randint(40, 90)):  # random shapes = plenty of edges, like a real scene
        x0, y0 = rng.randint(0, width), rng.randint(0, height)
        x1, y1 = x0 + rng.randint(40, width // 3), y0 + rng.randint(40, height // 3)
        color = tuple(rng.randint(0, 255) for _ in range(3))
        if rng.random() < 0.5:
            draw.rectangle([x0, y0, x1, y1], fill=color)
        else:
            draw.ellipse([x0, y0, x1, y1], fill=color, outline=(0, 0, 0), width=4)
    # A bit of sensor-like noise makes the JPEG size realistic (1-3 MB)
    noise = Image.effect_noise((width, height), 40).convert("RGB")
    return Image.blend(img, noise, 0.15)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "images")
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    names = []
    for i in range(count):
        portrait = rng.random() < 0.4
        w, h = (3000, 4000) if portrait else (4000, 3000)
        img = make_photo(rng, w, h)
        blurry = i % 8 == 7
        if blurry:
            img = img.filter(ImageFilter.GaussianBlur(30))
        name = f"photo_{i:02d}{'_blurry' if blurry else ''}.jpg"
        img.save(out / name, "JPEG", quality=88)
        names.append(name)
        print(f"  {name}  {(out / name).stat().st_size // 1024} KB")
    (out / "index.json").write_text(json.dumps(names))
    print(f"{count} images written to {out}")


if __name__ == "__main__":
    main()
