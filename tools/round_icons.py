#!/usr/bin/env python3
"""Builds the circular CrownOS app icons, light and dark, from src/apps/scalable.

    round_icons.py build [--shard N] [--names a,b] [--no-sheets]
    round_icons.py list --shard N

Per-icon exceptions live in tools/overrides/*.json as {"icon-name": {...}}:
    mode       "crop" (plate fills the circle) | "fit" (whole artwork on a plate)
    zoom       multiplier on the automatic scale
    dx, dy     offset in 64-unit canvas pixels
    plate      "#rrggbb" plate colour for fit mode in the light variant
    dark       "glyph" (dark plate, glyph kept) | "dim" (darkened light icon) | "keep" (same as light)
    tint       glyph colour in dark glyph mode: "original" | "plate" | "light"
    threshold  [low, high] RGB distance from the plate that separates the glyph
"""
import argparse
import base64
import fcntl
import io
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

THEME_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = THEME_ROOT / "src/apps/scalable"
OUTPUT_DIR = THEME_ROOT / "rounded"
OVERRIDES_DIR = THEME_ROOT / "tools/overrides"
SHARD_COUNT = 8
PREVIEW_FONT = "Inter Variable"

CANVAS = 64.0
CENTER = CANVAS / 2
RADIUS = CANVAS * 824 / 1024 / 2
ANALYSIS_PX = 256
CROP_OVERZOOM = 1.03
FIT_EXTENT = 0.72
DIAMETER_FIT = 2 * RADIUS * FIT_EXTENT
PLATE_ALPHA = 0.9
CONTENT_ALPHA = 0.35
DEFAULT_THRESHOLD = (18.0, 48.0)
NOISY_PLATE_RESIDUAL = 12.0
MIN_PLATE_INLIERS = 0.55
PLATE_FIT_ROUNDS = 3
LOST_DETAIL_LIMIT = 0.006
AUTO_ZOOMS = (0.95, 0.9, 0.86)
DARK_PLATE_LUMINANCE = 0.22
LIGHT_FIT_PLATE = "#f4f5f8"
DARK_PLATE_TOP, DARK_PLATE_BOTTOM = "#3a3b42", "#18191d"
LIGHT_GLYPH = (236, 237, 242)
TINT_MIN_LUMINANCE = 0.42
NEUTRAL_GLYPH_COLOURFUL = 0.15

LIGHT_STYLE = {"gloss": 0.22, "shade": 0.06, "rim_top": 0.55, "rim_mid": 0.10, "rim_bottom": 0.24, "shadow": 0.30, "saturate": 1.18}
DARK_STYLE = {"gloss": 0.12, "shade": 0.18, "rim_top": 0.34, "rim_mid": 0.06, "rim_bottom": 0.16, "shadow": 0.45, "saturate": 1.22}

SVG_HEAD = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" {ns} width="64" height="64" viewBox="0 0 64 64">'


def source_names():
    return sorted(path.stem for path in SOURCE_DIR.glob("*.svg"))


def shard_names(names, shard):
    size = -(-len(names) // SHARD_COUNT)
    return names[shard * size:(shard + 1) * size]


def load_overrides():
    merged = {}
    for path in sorted(OVERRIDES_DIR.glob("*.json")):
        merged.update(json.loads(path.read_text()))
    return merged


ROOT_STRUCTURAL_ATTRIBUTES = {"width", "height", "viewBox", "version", "id", "x", "y", "preserveAspectRatio", "enable-background", "xml:space", "baseProfile"}


def parse_source(text):
    root = re.search(r"<svg\b([^>]*)>", text)
    attributes = root.group(1)
    inner = text[root.end():text.rfind("</svg>")]
    namespaces = " ".join(ns for ns in re.findall(r'xmlns:[\w-]+="[^"]*"', attributes) if not ns.startswith("xmlns:xlink"))
    view_box = re.search(r'viewBox="([^"]+)"', attributes)
    if view_box:
        box = [float(v) for v in re.split(r"[\s,]+", view_box.group(1).strip())]
    else:
        size = [re.search(rf'\b{axis}="([\d.]+)', attributes) for axis in ("width", "height")]
        box = [0, 0, *(float(m.group(1)) if m else CANVAS for m in size)]
    presentation = " ".join(
        f'{key}="{value}"' for key, value in re.findall(r'([\w:.-]+)="([^"]*)"', attributes)
        if key not in ROOT_STRUCTURAL_ATTRIBUTES and not key.startswith(("xmlns", "sodipodi:", "inkscape:"))
    )
    if presentation:
        inner = f"<g {presentation}>{inner}</g>"
    return namespaces, box, inner


def nested(source, x, y, size):
    _, box, inner = source
    view_box = " ".join(f"{v:g}" for v in box)
    return f'<svg x="{x:.4f}" y="{y:.4f}" width="{size:.4f}" height="{size:.4f}" viewBox="{view_box}" preserveAspectRatio="xMidYMid meet" overflow="visible">{inner}</svg>'


def render(svg, px):
    with tempfile.TemporaryDirectory() as scratch:
        svg_path, png_path = Path(scratch, "icon.svg"), Path(scratch, "icon.png")
        svg_path.write_text(svg)
        subprocess.run(["resvg", "--sans-serif-family", PREVIEW_FONT, "--font-family", PREVIEW_FONT, "-w", str(px), "-h", str(px), svg_path, png_path], check=True, capture_output=True)
        return np.asarray(Image.open(png_path).convert("RGBA"), dtype=np.float64)


def alpha_box(alpha, threshold):
    ys, xs = np.nonzero(alpha >= threshold)
    if xs.size == 0:
        return None
    scale = CANVAS / alpha.shape[0]
    return xs.min() * scale, ys.min() * scale, (xs.max() + 1) * scale, (ys.max() + 1) * scale


def placement(source, settings):
    identity = render(SVG_HEAD.format(ns=source[0]) + nested(source, 0, 0, CANVAS) + "</svg>", ANALYSIS_PX)
    alpha = identity[..., 3] / 255
    plate = alpha_box(alpha, PLATE_ALPHA) or alpha_box(alpha, 0.5) or (0, 0, CANVAS, CANVAS)
    content = alpha_box(alpha, CONTENT_ALPHA) or plate
    if settings["mode"] == "fit":
        box, extent = content, DIAMETER_FIT
    else:
        box, extent = plate, 2 * RADIUS * CROP_OVERZOOM
    side = max(box[2] - box[0], box[3] - box[1]) if settings["mode"] == "fit" else min(box[2] - box[0], box[3] - box[1])
    scale = extent * settings.get("zoom", 1.0) / max(side, 1e-3)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    x = CENTER - cx * scale + settings.get("dx", 0.0)
    y = CENTER - cy * scale + settings.get("dy", 0.0)
    return x, y, CANVAS * scale



def pixel_grid(px):
    coords = (np.arange(px) + 0.5) * CANVAS / px
    xs, ys = np.meshgrid(coords, coords)
    return xs, ys, np.hypot(xs - CENTER, ys - CENTER)


def fit_plate_model(rgb, xs, ys, ring):
    basis = np.stack([np.ones(xs.size), xs.ravel(), ys.ravel()], axis=1)
    samples = ring.ravel().copy()
    flat_rgb = rgb.reshape(-1, 3)
    for _ in range(PLATE_FIT_ROUNDS):
        coefficients, *_ = np.linalg.lstsq(basis[samples], flat_rgb[samples], rcond=None)
        error = np.linalg.norm(flat_rgb - basis @ coefficients, axis=-1)
        cutoff = max(float(np.percentile(error[samples], 70)) * 1.5, 6.0)
        samples = ring.ravel() & (error <= cutoff)
    model = (basis @ coefficients).reshape(rgb.shape)
    residual = float(np.sqrt(np.mean(error[samples] ** 2)))
    inliers = float(samples.sum() / max(ring.sum(), 1))
    return model, residual, inliers


def luminance(rgb):
    return (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]) / 255


def chroma(rgb):
    return (rgb.max(axis=-1) - rgb.min(axis=-1)) / 255


def hex_color(rgb):
    return "#" + "".join(f"{int(round(min(max(v, 0), 255))):02x}" for v in rgb)


def readable_tint(rgb):
    rgb = np.asarray(rgb, dtype=np.float64)
    gray = rgb.mean()
    vivid = np.clip(gray + (rgb - gray) * 1.25, 0, 255)
    lift = max(0.0, TINT_MIN_LUMINANCE - float(luminance(vivid)))
    return vivid + (255 - vivid) * min(1.0, lift / 0.6)


def analyse(name, source, settings):
    x, y, size = placement(source, settings)
    placed = render(SVG_HEAD.format(ns=source[0]) + nested(source, x, y, size) + "</svg>", ANALYSIS_PX)
    rgb, alpha = placed[..., :3], placed[..., 3] / 255
    xs, ys, distance = pixel_grid(ANALYSIS_PX)
    inside = distance <= RADIUS
    ring = (distance >= 0.86 * RADIUS) & (distance <= 0.97 * RADIUS) & (alpha > 0.95)
    coverage = float((alpha[inside] > 0.98).mean())
    metrics = {"coverage": round(coverage, 4)}
    flags = []
    if settings["mode"] == "crop" and ring.sum() > 50:
        model, residual, inliers = fit_plate_model(rgb, xs, ys, ring)
        metrics["plate_inliers"] = round(inliers, 3)
    else:
        model, residual = None, 0.0
    if model is not None:
        low, high = settings.get("threshold", DEFAULT_THRESHOLD)
        difference = np.linalg.norm(rgb - model, axis=-1)
        lost = (~inside) & (alpha > 0.5) & (difference > high) & (distance <= RADIUS * 1.6)
        metrics["lost_detail"] = round(float(lost.sum() / inside.sum()), 4)
        mask = np.clip((difference - low) / max(high - low, 1e-3), 0, 1) * inside * (alpha > 0.5)
        plate_rgb = model[inside].mean(axis=0)
        plate_top = model[np.argmin(np.where(inside, ys, 1e9).ravel()) // ANALYSIS_PX, ANALYSIS_PX // 2]
        plate_bottom = model[np.argmax(np.where(inside, ys, -1e9).ravel()) // ANALYSIS_PX, ANALYSIS_PX // 2]
    else:
        mask = np.clip(alpha, 0, 1) * inside
        plate_rgb = np.array([int(LIGHT_FIT_PLATE[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float64)
        plate_top = plate_bottom = plate_rgb
    glyph_pixels = mask > 0.6
    glyph_rgb = np.median(rgb[glyph_pixels], axis=0) if glyph_pixels.sum() > 20 else plate_rgb
    metrics.update(
        residual=round(residual, 2),
        plate_luminance=round(float(luminance(plate_rgb)), 3),
        plate_chroma=round(float(chroma(plate_rgb)), 3),
        glyph_luminance=round(float(luminance(glyph_rgb)), 3),
        glyph_coverage=round(float(glyph_pixels[inside].mean()), 4),
        glyph_colourful=round(float((chroma(rgb[glyph_pixels]) > 0.25).mean()) if glyph_pixels.sum() > 20 else 0.0, 3),
    )
    if settings["mode"] == "crop" and settings.get("zoom", 1.0) >= 1.0 and coverage < 0.97:
        flags.append("not-a-plate")
    if metrics.get("lost_detail", 0) > LOST_DETAIL_LIMIT:
        flags.append("lost-detail")
    if residual > NOISY_PLATE_RESIDUAL or metrics.get("plate_inliers", 1.0) < MIN_PLATE_INLIERS:
        flags.append("noisy-plate")
    if not 0.01 <= metrics["glyph_coverage"] <= 0.6:
        flags.append("odd-glyph-mask")

    dark = settings.get("dark")
    if dark is None:
        if metrics["plate_luminance"] < DARK_PLATE_LUMINANCE:
            dark = "keep"
        elif "noisy-plate" in flags or "odd-glyph-mask" in flags:
            dark = "dim"
        else:
            dark = "glyph"
    tint = settings.get("tint")
    if tint is None:
        glyph_is_neutral = metrics["glyph_colourful"] < NEUTRAL_GLYPH_COLOURFUL
        if glyph_is_neutral and metrics["glyph_luminance"] > 0.75 and metrics["plate_chroma"] > 0.18:
            tint = "plate"
        elif glyph_is_neutral and metrics["glyph_luminance"] < 0.35:
            tint = "light"
        else:
            tint = "original"
    return {
        "placement": (x, y, size),
        "mask": mask,
        "plate_top": plate_top,
        "plate_bottom": plate_bottom,
        "dark": dark,
        "tint": tint,
        "metrics": metrics,
        "flags": flags,
    }


def png_data_uri(gray):
    buffer = io.BytesIO()
    Image.fromarray((np.clip(gray, 0, 1) * 255).astype(np.uint8), "L").save(buffer, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def lighting_defs(style):
    top, bottom = CENTER - RADIUS, CENTER + RADIUS
    return (
        f'<clipPath id="crwn-clip"><circle cx="{CENTER}" cy="{CENTER}" r="{RADIUS}"/></clipPath>'
        f'<filter id="crwn-shadow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="1.1"/></filter>'
        f'<filter id="crwn-vivid" color-interpolation-filters="sRGB"><feColorMatrix type="saturate" values="{style["saturate"]}"/></filter>'
        f'<filter id="crwn-dim" color-interpolation-filters="sRGB"><feColorMatrix type="saturate" values="{style["saturate"]}"/>'
        f'<feComponentTransfer><feFuncR type="linear" slope="0.78"/><feFuncG type="linear" slope="0.78"/><feFuncB type="linear" slope="0.78"/></feComponentTransfer></filter>'
        f'<linearGradient id="crwn-gloss" x1="0" y1="{top}" x2="0" y2="{bottom}" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="#fff" stop-opacity="{style["gloss"]}"/><stop offset="0.45" stop-color="#fff" stop-opacity="{style["gloss"] * 0.25:.3f}"/>'
        f'<stop offset="0.6" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="{style["shade"]}"/></linearGradient>'
        f'<linearGradient id="crwn-rim" x1="0" y1="{top}" x2="0" y2="{bottom}" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="#fff" stop-opacity="{style["rim_top"]}"/><stop offset="0.5" stop-color="#fff" stop-opacity="{style["rim_mid"]}"/>'
        f'<stop offset="1" stop-color="#fff" stop-opacity="{style["rim_bottom"]}"/></linearGradient>'
    )


def lighting_layers(style):
    return (
        f'<circle cx="{CENTER}" cy="{CENTER}" r="{RADIUS}" fill="url(#crwn-gloss)"/>'
        f'<circle cx="{CENTER}" cy="{CENTER}" r="{RADIUS - 0.35}" fill="none" stroke="url(#crwn-rim)" stroke-width="0.7"/>'
        f'<circle cx="{CENTER}" cy="{CENTER}" r="{RADIUS - 0.05}" fill="none" stroke="#000" stroke-opacity="0.12" stroke-width="0.1"/>'
    )


def shadow_layer(style):
    return f'<circle cx="{CENTER}" cy="{CENTER + 0.9}" r="{RADIUS - 0.2}" fill="#000" opacity="{style["shadow"]}" filter="url(#crwn-shadow)"/>'


def plate_circle(fill):
    return f'<circle cx="{CENTER}" cy="{CENTER}" r="{RADIUS}" fill="{fill}"/>'


def plate_defs(settings, analysis):
    if settings["mode"] == "fit":
        top = bottom = settings.get("plate", LIGHT_FIT_PLATE)
    else:
        top, bottom = hex_color(analysis["plate_top"]), hex_color(analysis["plate_bottom"])
    return (
        f'<linearGradient id="crwn-plate" x1="0" y1="{CENTER - RADIUS}" x2="0" y2="{CENTER + RADIUS}" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>'
    )


def light_svg(source, settings, analysis):
    x, y, size = analysis["placement"]
    return (
        SVG_HEAD.format(ns=source[0]) + "<defs>" + lighting_defs(LIGHT_STYLE) + plate_defs(settings, analysis) + "</defs>"
        + shadow_layer(LIGHT_STYLE)
        + f'<g clip-path="url(#crwn-clip)"><g filter="url(#crwn-vivid)">{plate_circle("url(#crwn-plate)")}{nested(source, x, y, size)}</g></g>'
        + lighting_layers(LIGHT_STYLE) + "</svg>"
    )


def dark_svg(source, settings, analysis):
    x, y, size = analysis["placement"]
    artwork = nested(source, x, y, size)
    defs = lighting_defs(DARK_STYLE) + plate_defs(settings, analysis) + (
        f'<linearGradient id="crwn-dark-plate" x1="0" y1="{CENTER - RADIUS}" x2="0" y2="{CENTER + RADIUS}" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="{DARK_PLATE_TOP}"/><stop offset="1" stop-color="{DARK_PLATE_BOTTOM}"/></linearGradient>'
    )
    mode = analysis["dark"]
    if mode in ("keep", "dim"):
        body = f'<g filter="url(#crwn-{"vivid" if mode == "keep" else "dim"})">{plate_circle("url(#crwn-plate)")}{artwork}</g>'
    else:
        defs += f'<mask id="crwn-glyph" maskUnits="userSpaceOnUse" x="0" y="0" width="64" height="64"><image width="64" height="64" xlink:href="{png_data_uri(analysis["mask"])}"/></mask>'
        tint = analysis["tint"]
        if tint == "original":
            glyph = f'<g mask="url(#crwn-glyph)"><g filter="url(#crwn-vivid)">{artwork}</g></g>'
        else:
            if tint == "plate":
                top, bottom = readable_tint(analysis["plate_top"]), readable_tint(analysis["plate_bottom"])
            else:
                top = bottom = np.array(LIGHT_GLYPH, dtype=np.float64)
            defs += (
                f'<linearGradient id="crwn-tint" x1="0" y1="{CENTER - RADIUS}" x2="0" y2="{CENTER + RADIUS}" gradientUnits="userSpaceOnUse">'
                f'<stop offset="0" stop-color="{hex_color(top)}"/><stop offset="1" stop-color="{hex_color(bottom)}"/></linearGradient>'
            )
            glyph = f'<circle cx="{CENTER}" cy="{CENTER}" r="{RADIUS}" fill="url(#crwn-tint)" mask="url(#crwn-glyph)"/>'
        body = plate_circle("url(#crwn-dark-plate)") + glyph
    return (
        SVG_HEAD.format(ns=source[0]) + "<defs>" + defs + "</defs>" + shadow_layer(DARK_STYLE)
        + f'<g clip-path="url(#crwn-clip)">{body}</g>' + lighting_layers(DARK_STYLE) + "</svg>"
    )


def build_icon(name, overrides):
    settings = {"mode": "crop", **overrides.get(name, {})}
    source = parse_source((SOURCE_DIR / f"{name}.svg").read_text(encoding="utf-8", errors="replace"))
    override = overrides.get(name, {})
    analysis = analyse(name, source, settings)
    if settings["mode"] == "crop" and "not-a-plate" in analysis["flags"] and "mode" not in override:
        settings["mode"] = "fit"
        analysis = analyse(name, source, settings)
        analysis["flags"].append("auto-fit")
    elif settings["mode"] == "crop" and "lost-detail" in analysis["flags"] and "zoom" not in override:
        trials = []
        for zoom in AUTO_ZOOMS:
            trial = analyse(name, source, {**settings, "zoom": zoom})
            trial["flags"] = [flag for flag in trial["flags"] if flag != "not-a-plate"] + [f"auto-zoom-{zoom}"]
            trials.append((trial["metrics"]["lost_detail"], zoom, trial))
            if trial["metrics"]["lost_detail"] <= LOST_DETAIL_LIMIT:
                break
        _, zoom, analysis = min(trials, key=lambda entry: entry[0])
        settings["zoom"] = zoom
    outputs = {}
    for variant, builder in (("light", light_svg), ("dark", dark_svg)):
        path = OUTPUT_DIR / variant / "apps/scalable" / f"{name}.svg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(builder(source, settings, analysis))
        outputs[variant] = path
    previews = {variant: render(path.read_text(), 112) for variant, path in outputs.items()}
    previews["original"] = render((SOURCE_DIR / f"{name}.svg").read_text(encoding="utf-8", errors="replace"), 112)
    record = {
        "mode": settings["mode"], "zoom": settings.get("zoom", 1.0), "dark": analysis["dark"], "tint": analysis["tint"],
        "flags": analysis["flags"], "metrics": analysis["metrics"], "override": overrides.get(name),
    }
    return name, record, {k: v.astype(np.uint8) for k, v in previews.items()}


def compose_sheet(entries, path):
    cell_w, cell_h, columns = 3 * 112 + 4 * 8, 112 + 44, 4
    rows = -(-len(entries) // columns)
    sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), "#2a2c31")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(13)
    backgrounds = {"original": (232, 234, 239), "light": (232, 234, 239), "dark": (22, 23, 27)}
    for index, (name, record, previews) in enumerate(entries):
        ox, oy = (index % columns) * cell_w, (index // columns) * cell_h
        for slot, variant in enumerate(("original", "light", "dark")):
            x = ox + 8 + slot * (112 + 8)
            tile = Image.new("RGB", (112, 112), backgrounds[variant])
            icon = Image.fromarray(previews[variant], "RGBA")
            tile.paste(icon, (0, 0), icon)
            sheet.paste(tile, (x, oy + 4))
        label = f"{index:02d} {name}"[:44]
        detail = f'{record["mode"]}/{record["dark"]}/{record["tint"]} {" ".join(record["flags"])}'[:52]
        draw.text((ox + 8, oy + 118), label, fill=(240, 240, 245), font=font)
        draw.text((ox + 8, oy + 134), detail, fill=(255, 196, 92) if record["flags"] else (150, 154, 166), font=font)
    sheet.save(path, optimize=True)


def build(names, shard, make_sheets, subset):
    overrides = load_overrides()
    with ProcessPoolExecutor() as pool:
        results = list(pool.map(build_icon, names, [overrides] * len(names), chunksize=4))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUTPUT_DIR / "manifest.json"
    with open(OUTPUT_DIR / ".manifest.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        manifest.update({name: record for name, record, _ in results})
        manifest_path.write_text(json.dumps(dict(sorted(manifest.items())), indent=1))
    if make_sheets:
        sheet_dir = OUTPUT_DIR / "review" / (f"shard-{shard}" if shard is not None else "adhoc") / ("check" if subset else "")
        sheet_dir.mkdir(parents=True, exist_ok=True)
        for stale in sheet_dir.glob("sheet-*.png"):
            stale.unlink()
        index = {}
        for start in range(0, len(results), 24):
            chunk = results[start:start + 24]
            path = sheet_dir / f"sheet-{start // 24:02d}.png"
            compose_sheet(chunk, path)
            index[path.name] = [name for name, _, _ in chunk]
        (sheet_dir / "index.json").write_text(json.dumps(index, indent=1))
    flagged = {name: record["flags"] for name, record, _ in results if record["flags"]}
    print(json.dumps({"built": len(results), "flagged": len(flagged), "flags": flagged}, indent=1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "list"))
    parser.add_argument("--shard", type=int)
    parser.add_argument("--names")
    parser.add_argument("--no-sheets", action="store_true")
    arguments = parser.parse_args()
    names = source_names()
    if arguments.names:
        names = [name for name in arguments.names.split(",") if name]
    elif arguments.shard is not None:
        names = shard_names(names, arguments.shard)
    unknown = sorted(set(names) - set(source_names()))
    if unknown:
        sys.exit(f"unknown icons: {', '.join(unknown)}")
    if arguments.command == "list":
        print("\n".join(names))
        return
    build(names, arguments.shard, not arguments.no_sheets, bool(arguments.names))


if __name__ == "__main__":
    sys.exit(main())
