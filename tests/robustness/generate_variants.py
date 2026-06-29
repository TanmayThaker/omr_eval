"""Generate a battery of degraded variants of the reference sheet to simulate
real-world scan/photo conditions, plus capture the clean decode as ground truth.

Output: data/variants/*.png (+ a few .jpg) and ground_truth.json
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import numpy as np
import cv2
from PIL import Image

from omr import process_document, OMRConfig
from omr.pdf import load_grayscale

PDF = r"D:\omr_eval\OMR_sheet_2467.pdf"
OUT = r"D:\omr_eval\data\variants"
GT = os.path.join(os.path.dirname(__file__), "ground_truth.json")


def save(name, img):
    path = os.path.join(OUT, name)
    if name.lower().endswith((".jpg", ".jpeg")):
        cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 100])
    else:
        cv2.imwrite(path, img)
    return name


def rotate(img, deg, border=255):
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def scale(img, f):
    h, w = img.shape[:2]
    return cv2.resize(img, (int(w * f), int(h * f)),
                      interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)


def gamma(img, g):
    lut = np.array([((i / 255.0) ** g) * 255 for i in range(256)]).astype(np.uint8)
    return cv2.LUT(img, lut)


def uneven_illumination(img, strength=0.6):
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    grad = (xx / w) * strength + (yy / h) * strength * 0.5
    out = img.astype(np.float32) * (1 - grad) + 255 * grad * 0.4
    return np.clip(out, 0, 255).astype(np.uint8)


def add_gauss_noise(img, sigma):
    n = np.random.normal(0, sigma, img.shape)
    return np.clip(img.astype(np.float32) + n, 0, 255).astype(np.uint8)


def salt_pepper(img, amount=0.02):
    out = img.copy()
    n = int(amount * img.size)
    ys = np.random.randint(0, img.shape[0], n); xs = np.random.randint(0, img.shape[1], n)
    out[ys[:n // 2], xs[:n // 2]] = 0
    out[ys[n // 2:], xs[n // 2:]] = 255
    return out


def perspective(img, k, border=255):
    h, w = img.shape[:2]
    d = int(min(h, w) * k)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[d, d // 2], [w - d // 2, 0], [w - d, h - d // 2], [d // 2, h]])
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, M, (w, h), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def translate(img, dx, dy, border=255):
    h, w = img.shape[:2]
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=border)


def crop_margins(img, px):
    return img[px:-px, px:-px]


def faint_marks(gray, factor=0.45):
    """Lighten dark (filled) regions to simulate light pencil marks."""
    out = gray.astype(np.float32)
    dark = out < 128
    out[dark] = 255 - (255 - out[dark]) * factor
    return out.astype(np.uint8)


def stray_marks(gray, n=40):
    out = gray.copy()
    H, W = out.shape
    for _ in range(n):
        x1, y1 = np.random.randint(0, W), np.random.randint(0, H)
        if np.random.rand() < 0.5:
            cv2.line(out, (x1, y1), (x1 + np.random.randint(-60, 60),
                     y1 + np.random.randint(-60, 60)), 0, np.random.randint(1, 3))
        else:
            cv2.circle(out, (x1, y1), np.random.randint(2, 7), 0, -1)
    return out


def to_color_blue_ink(gray):
    """Simulate a color scan where marks were made with blue ink on white."""
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR).astype(np.float32)
    dark = gray < 100
    # turn dark ink bluish: keep blue high, drop red/green
    bgr[dark] = bgr[dark] * [1.0, 0.35, 0.2] + [120, 20, 10]
    # warm paper tint
    bgr[~dark] = np.clip(bgr[~dark] * [1.0, 0.98, 0.92], 0, 255)
    return np.clip(bgr, 0, 255).astype(np.uint8)


def jpeg(img, q):
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return cv2.imdecode(enc, cv2.IMREAD_GRAYSCALE if img.ndim == 2 else cv2.IMREAD_COLOR)


def main():
    np.random.seed(42)
    os.makedirs(OUT, exist_ok=True)
    cfg = OMRConfig()

    # ground truth from the clean PDF (verified 100% correct)
    clean = process_document(PDF, cfg)
    gt = {"roll": clean.roll_number,
          "answers": {a.question: a.answer for a in clean.answers}}
    with open(GT, "w") as f:
        json.dump(gt, f)
    print("ground truth roll:", gt["roll"], "answers:", len(gt["answers"]))

    base = load_grayscale(PDF, dpi=cfg.render_dpi)
    print("base size:", base.shape)

    variants = []
    variants.append(save("clean.png", base))

    # --- skew (scanner feed) ---
    for d in (0.7, -1.5, 3.0, -5.0, 7.0):
        variants.append(save(f"skew_{d:+.1f}.png", rotate(base, d)))

    # --- orientation ---
    variants.append(save("rot90.png", np.rot90(base, 1)))
    variants.append(save("rot180.png", np.rot90(base, 2)))
    variants.append(save("rot270.png", np.rot90(base, 3)))

    # --- large free rotations (hand-held photo): pad first so corners survive ---
    def pad_rotate(img, deg, pad=0.30):
        p = int(min(img.shape[:2]) * pad)
        padded = cv2.copyMakeBorder(img, p, p, p, p, cv2.BORDER_CONSTANT, value=255)
        return rotate(padded, deg)
    for d in (12, 25, 40):
        variants.append(save(f"rotpad_{d}.png", pad_rotate(base, d)))

    # --- scale / resolution ---
    for f in (0.5, 0.65, 0.8, 1.4):
        variants.append(save(f"scale_{f}.png", scale(base, f)))

    # --- brightness / contrast / illumination ---
    variants.append(save("dark.png", gamma(base, 1.8)))
    variants.append(save("bright.png", gamma(base, 0.5)))
    variants.append(save("lowcontrast.png",
                         np.clip(base.astype(np.float32) * 0.5 + 90, 0, 255).astype(np.uint8)))
    variants.append(save("uneven_light.png", uneven_illumination(base)))

    # --- blur ---
    variants.append(save("blur3.png", cv2.GaussianBlur(base, (3, 3), 0)))
    variants.append(save("blur7.png", cv2.GaussianBlur(base, (7, 7), 0)))

    # --- noise ---
    variants.append(save("noise15.png", add_gauss_noise(base, 15)))
    variants.append(save("noise30.png", add_gauss_noise(base, 30)))
    variants.append(save("saltpepper.png", salt_pepper(base, 0.03)))

    # --- compression ---
    variants.append(save("jpeg20.png", jpeg(base, 20)))
    variants.append(save("jpeg40.png", jpeg(base, 40)))

    # --- geometry ---
    variants.append(save("perspective_mild.png", perspective(base, 0.04)))
    variants.append(save("perspective_strong.png", perspective(base, 0.10)))
    variants.append(save("translate.png", translate(base, 120, -80)))
    variants.append(save("crop20.png", crop_margins(base, 20)))
    variants.append(save("crop60.png", crop_margins(base, 60)))

    # --- marking quality ---
    variants.append(save("faint.png", faint_marks(base, 0.45)))
    variants.append(save("veryfaint.png", faint_marks(base, 0.25)))
    variants.append(save("stray.png", stray_marks(base, 60)))

    # --- color ---
    variants.append(save("blue_ink.png", to_color_blue_ink(base)))

    # --- inverted ---
    variants.append(save("inverted.png", 255 - base))

    # --- realistic phone photo: rotate + perspective + illumination + noise + jpeg ---
    combo = rotate(base, 2.2)
    combo = perspective(combo, 0.05)
    combo = uneven_illumination(combo, 0.5)
    combo = add_gauss_noise(combo, 12)
    combo = jpeg(combo, 45)
    variants.append(save("phone_photo.png", combo))

    print(f"generated {len(variants)} variants -> {OUT}")


if __name__ == "__main__":
    main()
