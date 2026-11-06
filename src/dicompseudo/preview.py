"""`dicompseudo preview`: private before/after contact sheet for drawing mask templates.

For every device signature in input_dir (by default only those that may carry burned-in
text, or that a template matches), a few sample images are rendered:
left = original with the template's rectangles outlined in red, right = masked result.

The PNGs SHOW THE ORIGINAL PIXELS, including any burned-in names, so they are written to
<audit_dir>/preview/, which is private like the mapping file. Nothing is printed except
technical signatures, counts and the folder path.

It also writes pixel_masks_skeleton.json: one template per signature without a template,
ready to copy into environment.json once the rectangles are filled in.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydicom import dcmread

from .config import Config
from .engine import Pseudonymiser
from .io import is_dicom, iter_files
from .pixels import _axes, find_template, masked_array, signature


@dataclass
class Group:
    signature: dict
    burned_in: set = field(default_factory=set)
    files: list = field(default_factory=list)
    count: int = 0


def _key(sig: dict) -> str:
    return json.dumps(sig, sort_keys=True)


def _to_image(ds, arr, bounds=None):
    """First frame as an 8-bit PIL image (grey or RGB), contrast-stretched.

    Pass the original's `bounds` when rendering the masked image, so both halves of the
    preview use the same brightness scale.
    """
    import numpy as np
    from PIL import Image
    colour = int(ds.get("SamplesPerPixel", 1) or 1) > 1
    target_ndim = 3 if colour else 2                      # (R, C, 3) or (R, C)
    while arr.ndim > target_ndim:                         # drop frame axis -> first frame
        arr = arr[0]
    a = arr.astype("float64")
    lo, hi = bounds if bounds else (float(a.min()), float(a.max()))
    a = (a - lo) / (hi - lo) * 255 if hi > lo else a * 0
    a = np.clip(a, 0, 255).astype(np.uint8)
    if str(ds.get("PhotometricInterpretation", "")).upper() == "MONOCHROME1":
        a = 255 - a
    img = Image.fromarray(a)
    return img.convert("RGB"), (lo, hi)


def _outline(img, rects):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    width = max(1, img.width // 300)
    for x, y, w, h in rects:
        d.rectangle([x, y, x + w - 1, y + h - 1], outline=(230, 75, 53), width=width)
    return img


def _side_by_side(left, right):
    from PIL import Image
    gap = 8
    out = Image.new("RGB", (left.width + right.width + gap, max(left.height, right.height)),
                    (255, 255, 255))
    out.paste(left, (0, 0))
    out.paste(right, (left.width + gap, 0))
    return out


def build(cfg: Config, n_per_signature: int = 3, include_all: bool = False) -> tuple[Path, list[dict]]:
    groups: dict[str, Group] = {}
    for path in iter_files(cfg.input_dir):
        if not is_dicom(path):
            continue
        try:
            ds = dcmread(path, stop_before_pixels=True)
        except Exception:
            continue
        if "Rows" not in ds:
            continue
        sig = signature(ds)
        g = groups.setdefault(_key(sig), Group(sig))
        g.count += 1
        b = Pseudonymiser.burned_in_status(ds)
        g.burned_in.add(b)
        if len(g.files) < n_per_signature:
            g.files.append(path)

    out = cfg.audit_dir / "preview"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("sig*.png"):
        old.unlink()
    summary, skeleton, rows_html = [], [], []
    for i, (k, g) in enumerate(sorted(groups.items()), 1):
        ds0 = dcmread(g.files[0], stop_before_pixels=True)
        tpl = find_template(ds0, cfg.pixel_masks)
        risky = g.burned_in != {"no"}
        if not (include_all or risky or tpl):
            continue
        imgs = []
        for j, f in enumerate(g.files, 1):
            try:
                ds = dcmread(f)
                arr = ds.pixel_array
                before, bounds = _to_image(ds, arr)
                if tpl:
                    _outline(before, tpl.rectangles)
                    after, _ = _to_image(ds, masked_array(ds, tpl), bounds)
                    img = _side_by_side(before, after)
                else:
                    img = before
                name = f"sig{i:02d}_{j}.png"
                img.save(out / name)
                imgs.append(name)
            except Exception as e:
                imgs.append(f"(could not render: {type(e).__name__})")
        entry = {"index": i, "signature": g.signature, "files": g.count,
                 "burned_in": sorted(g.burned_in), "template": tpl.name if tpl else None,
                 "images": [x for x in imgs if x.endswith(".png")]}
        summary.append(entry)
        if tpl is None:
            skeleton.append({"name": f"device_{i:02d}", "match": g.signature,
                             "rectangles": [], "clears_burned_in": True})
        cells = "".join(f'<img src="{html.escape(x)}">' if x.endswith(".png")
                        else f"<p>{html.escape(x)}</p>" for x in imgs)
        rows_html.append(
            f"<h2>sig{i:02d}: {html.escape(json.dumps(g.signature))}</h2>"
            f"<p>{g.count} files · burned-in: {', '.join(sorted(g.burned_in))} · template: "
            f"<b>{html.escape(tpl.name) if tpl else 'NONE'}</b>"
            f"{' (left: original with rectangles in red, right: masked)' if tpl else ''}</p>"
            f"<div>{cells}</div>")
    (out / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>dicompseudo preview (PRIVATE)</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}img{max-width:100%;"
        "margin:4px 0;border:1px solid #ccc}h2{font-size:16px;margin-top:32px}</style>"
        "<h1>dicompseudo preview: PRIVATE, shows original pixels</h1>"
        "<p>Coordinates: x = column, y = row, from the top-left corner, in pixels "
        "(the image size is in the signature). Rectangles are [x, y, width, height].</p>"
        + "".join(rows_html), encoding="utf8")
    (out / "pixel_masks_skeleton.json").write_text(json.dumps(skeleton, indent=2) + "\n",
                                                   encoding="utf8")
    return out, summary
