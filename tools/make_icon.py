"""Genera assets/renova.ico (cuadrado redondeado rosa con flecha de descarga)."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets"
TOP, BOTTOM, INK = (255, 100, 118), (207, 63, 80), (22, 10, 11)


def render(size: int) -> Image.Image:
    ss = 8
    S = size * ss
    grad = Image.new("RGB", (1, S))
    for y in range(S):
        t = y / (S - 1)
        grad.putpixel((0, y), tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)))
    grad = grad.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    pad = int(S * 0.04)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, S - pad, S - pad], radius=int(S * 0.24), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    cx = S / 2
    w = S * 0.11                     # grosor del trazo
    top, mid = S * 0.22, S * 0.60    # flecha
    d.rounded_rectangle([cx - w / 2, top, cx + w / 2, mid - w * 0.2], radius=w / 2, fill=INK)
    head = S * 0.20
    d.polygon([(cx - head, mid - head * 0.95), (cx + head, mid - head * 0.95), (cx, mid + head * 0.25)], fill=INK)
    d.rounded_rectangle([S * 0.25, S * 0.72, S * 0.75, S * 0.72 + w], radius=w / 2, fill=INK)
    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(exist_ok=True)
    big = render(256)
    big.save(OUT / "renova.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)],
             append_images=[render(s) for s in (16, 20, 24, 32, 40, 48, 64, 128)])
    big.save(OUT / "renova.png")
    print("ok", OUT / "renova.ico")


if __name__ == "__main__":
    main()
