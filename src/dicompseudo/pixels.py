"""Phase 2: remove text burned into the pixels, using mask templates per device.

A template (in environment.json -> "pixel_masks") matches files by technical header
values (Modality, Manufacturer, ManufacturerModelName, Rows, Columns, ...) and lists
rectangles [x, y, width, height] in pixels (x = column, y = row, origin top-left) that
are filled with black in every frame. Masked files are rewritten uncompressed.

Burned-in text sits at the same place for one scanner model and screen layout, which is
why templates are defined per device signature. Templates are drawn by a person who has
looked at the images (see `dicompseudo preview`), never guessed by the tool.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

MATCH_KEYS = ("Modality", "Manufacturer", "ManufacturerModelName", "Rows", "Columns",
              "SoftwareVersions", "SOPClassUID", "ImageType")
INT_KEYS = {"Rows", "Columns"}
CLEAN_PIXEL_CODE = ("113101", "Clean Pixel Data Option")


class MaskError(ValueError):
    pass


@dataclass
class MaskTemplate:
    name: str
    match: dict
    rectangles: list[tuple[int, int, int, int]]
    clears_burned_in: bool = True

    def matches(self, ds: Dataset) -> bool:
        for k, want in self.match.items():
            have = ds.get(k)
            if have is None:
                return False
            if k in INT_KEYS:
                if int(have) != int(want):
                    return False
            elif k == "ImageType":
                if str(want).upper() not in [str(v).upper() for v in (have if not isinstance(have, str) else [have])]:
                    return False
            elif str(have).strip().upper() != str(want).strip().upper():
                return False
        return True


def parse_templates(raw) -> list[MaskTemplate]:
    """Validate environment.json -> pixel_masks (messages name the template, not data)."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise MaskError("'pixel_masks' must be a list of templates")
    out, names = [], set()
    for i, t in enumerate(raw, 1):
        where = f"pixel_masks[{i}]"
        if not isinstance(t, dict):
            raise MaskError(f"{where} must be an object")
        unknown = set(t) - {"name", "match", "rectangles", "clears_burned_in"}
        if unknown:
            raise MaskError(f"{where}: unknown key(s) {sorted(unknown)}")
        name = str(t.get("name") or f"template_{i}")
        if name in names:
            raise MaskError(f"{where}: duplicate name '{name}'")
        names.add(name)
        match = t.get("match")
        if not isinstance(match, dict) or not match:
            raise MaskError(f"{where} ('{name}'): 'match' needs at least one of {list(MATCH_KEYS)}")
        bad = set(match) - set(MATCH_KEYS)
        if bad:
            raise MaskError(f"{where} ('{name}'): cannot match on {sorted(bad)}; use {list(MATCH_KEYS)}")
        for k in INT_KEYS & set(match):
            if not isinstance(match[k], int) or match[k] <= 0:
                raise MaskError(f"{where} ('{name}'): match.{k} must be a positive integer")
        rects = t.get("rectangles")
        if not isinstance(rects, list) or not rects:
            raise MaskError(f"{where} ('{name}'): 'rectangles' must be a non-empty list of [x, y, width, height]")
        parsed = []
        for j, r in enumerate(rects, 1):
            if (not isinstance(r, list) or len(r) != 4
                    or not all(isinstance(v, int) and not isinstance(v, bool) for v in r)):
                raise MaskError(f"{where} ('{name}') rectangle {j}: must be [x, y, width, height] integers")
            x, y, w, h = r
            if x < 0 or y < 0 or w <= 0 or h <= 0:
                raise MaskError(f"{where} ('{name}') rectangle {j}: x, y >= 0 and width, height > 0")
            if "Columns" in match and x + w > match["Columns"]:
                raise MaskError(f"{where} ('{name}') rectangle {j}: extends past Columns={match['Columns']}")
            if "Rows" in match and y + h > match["Rows"]:
                raise MaskError(f"{where} ('{name}') rectangle {j}: extends past Rows={match['Rows']}")
            parsed.append((x, y, w, h))
        cb = t.get("clears_burned_in", True)
        if not isinstance(cb, bool):
            raise MaskError(f"{where} ('{name}'): 'clears_burned_in' must be true or false")
        out.append(MaskTemplate(name, dict(match), parsed, cb))
    return out


def find_template(ds: Dataset, templates: list[MaskTemplate]) -> MaskTemplate | None:
    for t in templates:
        if t.matches(ds):
            return t
    return None


def signature(ds: Dataset) -> dict:
    """Technical device signature used for templates (no personal data)."""
    sig = {}
    for k in ("Modality", "Manufacturer", "ManufacturerModelName", "Rows", "Columns"):
        v = ds.get(k)
        if v not in (None, ""):
            sig[k] = int(v) if k in INT_KEYS else str(v)
    return sig


# ------------------------------------------------------------------ pixel work
def _axes(ds: Dataset, arr) -> tuple[int, int]:
    """Index of the row and column axes in pydicom's array layout."""
    spp = int(ds.get("SamplesPerPixel", 1) or 1)
    return (arr.ndim - 3, arr.ndim - 2) if spp > 1 else (arr.ndim - 2, arr.ndim - 1)


def fill_value(ds: Dataset, arr):
    """Black: 0, except MONOCHROME1 where black is the maximum stored value."""
    if str(ds.get("PhotometricInterpretation", "")).upper() == "MONOCHROME1":
        bits = int(ds.get("BitsStored", arr.dtype.itemsize * 8))
        return (1 << bits) - 1 if arr.dtype.kind == "u" else (1 << (bits - 1)) - 1
    return 0


def masked_array(ds: Dataset, template: MaskTemplate):
    """Decoded pixel array with the template's rectangles filled with black."""
    import numpy as np  # noqa: F401  (pixel support is optional)
    arr = ds.pixel_array.copy()
    ra, ca = _axes(ds, arr)
    rows, cols = arr.shape[ra], arr.shape[ca]
    fv = fill_value(ds, arr)
    for x, y, w, h in template.rectangles:
        y0, y1 = min(y, rows), min(y + h, rows)
        x0, x1 = min(x, cols), min(x + w, cols)
        if y0 >= y1 or x0 >= x1:
            continue
        idx = [slice(None)] * arr.ndim
        idx[ra] = slice(y0, y1)
        idx[ca] = slice(x0, x1)
        arr[tuple(idx)] = fv
    return arr


def apply_mask(ds: Dataset, template: MaskTemplate) -> None:
    """Mask in place and rewrite PixelData uncompressed (Explicit VR Little Endian)."""
    try:
        arr = masked_array(ds, template)
    except ImportError as e:
        raise MaskError("pixel masking needs numpy (and pylibjpeg for compressed images): "
                        "conda env update -f environment.yml") from e
    pi = str(ds.PhotometricInterpretation)
    if pi.startswith("YBR"):
        pi = "RGB"                       # pydicom decodes YBR colour to RGB
    bits = int(ds.get("BitsStored", arr.dtype.itemsize * 8))
    ds.set_pixel_data(arr, pi, bits, generate_instance_uid=False)
    if template.clears_burned_in:
        ds.BurnedInAnnotation = "NO"
    seq = ds.get("DeidentificationMethodCodeSequence") or Sequence()
    if CLEAN_PIXEL_CODE[0] not in [str(i.get("CodeValue", "")) for i in seq]:
        item = Dataset()
        item.CodeValue, item.CodingSchemeDesignator, item.CodeMeaning = (
            CLEAN_PIXEL_CODE[0], "DCM", CLEAN_PIXEL_CODE[1])
        seq.append(item)
        ds.DeidentificationMethodCodeSequence = seq


def rectangles_are_black(ds: Dataset, template: MaskTemplate) -> bool:
    """Used by `check`: True if every rectangle is black in every frame."""
    arr = ds.pixel_array
    ra, ca = _axes(ds, arr)
    fv = fill_value(ds, arr)
    for x, y, w, h in template.rectangles:
        idx = [slice(None)] * arr.ndim
        idx[ra] = slice(y, min(y + h, arr.shape[ra]))
        idx[ca] = slice(x, min(x + w, arr.shape[ca]))
        region = arr[tuple(idx)]
        if region.size and not (region == fv).all():
            return False
    return True
