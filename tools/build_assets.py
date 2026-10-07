#!/usr/bin/env python3
"""Build Float Ghost's lossless runtime atlas and public previews from source pixels."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

CELL = (192, 208)
ATLAS = (1536, 2288)
STATES = {
    "idle": [280, 110, 110, 140, 140, 320],
    "running-right": [120] * 7 + [220],
    "running-left": [120] * 7 + [220],
    "waving": [140, 140, 140, 280],
    "jumping": [140, 140, 140, 140, 280],
    "failed": [140] * 7 + [240],
    "waiting": [150] * 5 + [260],
    "running": [120] * 5 + [220],
    "review": [150] * 5 + [280],
}
COUNTS = [len(durations) for durations in STATES.values()] + [8, 8]
ANGLES = [f"{i * 22.5:g}" for i in range(16)]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate(image):
    """Check the structural contract without claiming semantic or motion review."""
    errors = []
    if image.size != ATLAS:
        return [f"Expected {ATLAS}, got {image.size}"]
    for row, count in enumerate(COUNTS):
        for column in range(8):
            cell = image.crop((column * CELL[0], row * CELL[1],
                               (column + 1) * CELL[0], (row + 1) * CELL[1]))
            occupied = cell.getchannel("A").getbbox() is not None
            if occupied != (column < count):
                errors.append(f"Unexpected occupancy in row {row}, cell {column}")
    if image.getchannel("A").getextrema() != (0, 255):
        errors.append("The atlas must contain transparent and opaque pixels")
    pixels = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
    if any(a == 0 and (r or g or b) for r, g, b, a in pixels):
        errors.append("Transparent pixels contain hidden RGB data")
    return errors


def frames(image, row, count):
    return [image.crop((column * CELL[0], row * CELL[1],
                        (column + 1) * CELL[0], (row + 1) * CELL[1]))
            for column in range(count)]


def font(size=15):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def card(image, label, width=240):
    result = Image.new("RGB", (width, 250), "#202337")
    result.paste(image, ((width - CELL[0]) // 2, 18), image)
    ImageDraw.Draw(result).text((10, 228), label, fill="#eef0ff", font=font(13))
    return result


def save_animation(images, durations, path, videos=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=durations, loop=0, disposal=2, optimize=False)
    if videos:
        video_path = path.parent.parent / "videos" / f"{path.stem}.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(path), "-vf", "fps=30,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(video_path),
        ], check=True)


def contact_sheet(image):
    # Previews use full native cells; labels remain outside the source pixels.
    result = Image.new("RGB", (ATLAS[0], 11 * (CELL[1] + 28)), "#202337")
    draw = ImageDraw.Draw(result)
    labels = list(STATES) + ["look: 000 through 157.5", "look: 180 through 337.5"]
    for row, label in enumerate(labels):
        y = row * (CELL[1] + 28)
        draw.text((8, y + 4), f"Row {row}: {label}", fill="#eef0ff", font=font())
        for column, pose in enumerate(frames(image, row, 8)):
            x = column * CELL[0]
            result.paste(pose, (x, y + 28), pose)
            draw.line([(x, y + 28), (x, y + 28 + CELL[1])], fill="#41455c")
    return result


def verify(root):
    source = Image.open(root / "source/spritesheet.png").convert("RGBA")
    runtime = Image.open(root / "pet/spritesheet.webp").convert("RGBA")
    errors = validate(source) + validate(runtime)
    if runtime.tobytes() != source.tobytes():
        errors.append("Runtime WebP does not preserve source RGBA pixels exactly")
    metadata = json.loads((root / "pet/pet.json").read_text(encoding="utf-8"))
    if metadata.get("id") != "float-ghost" or metadata.get("spritesheetPath") != "spritesheet.webp":
        errors.append("Unexpected pet identity or runtime path")
    if metadata.get("spriteVersionNumber") != 2:
        errors.append("The extended atlas requires spriteVersionNumber: 2")
    for name in STATES:
        if not (root / "preview/gifs" / f"{name}.gif").is_file():
            errors.append(f"Missing {name} GIF preview")
    result = {
        "ok": not errors,
        "checks": "Structure, metadata, and lossless source/runtime pixel equality",
        "atlas_dimensions": list(source.size),
        "cell_dimensions": list(CELL),
        "grid": [8, 11],
        "required_frames": sum(COUNTS),
        "transparent_unused_cells": 88 - sum(COUNTS),
        "runtime_rgba_matches_source": runtime.tobytes() == source.tobytes(),
        "rgba_sha256": digest(source.tobytes()),
        "source_png_sha256": digest((root / "source/spritesheet.png").read_bytes()),
        "runtime_webp_sha256": digest((root / "pet/spritesheet.webp").read_bytes()),
        "errors": errors,
    }
    return result


def build(root, videos):
    source_path = root / "source/spritesheet.png"
    image = Image.open(source_path).convert("RGBA")
    errors = validate(image)
    if errors:
        raise ValueError("; ".join(errors))
    (root / "pet").mkdir(exist_ok=True)
    image.save(root / "pet/spritesheet.webp", lossless=True, exact=True, method=6)
    # Stop before producing previews if the encoding changed any source pixels.
    encoded = Image.open(root / "pet/spritesheet.webp").convert("RGBA")
    if encoded.tobytes() != image.tobytes():
        raise ValueError("Lossless WebP encoding changed source pixels")
    preview = root / "preview"
    contact_sheet(encoded).save(preview / "contact-sheet.png")
    frames(encoded, 0, 1)[0].save(preview / "base-transparent-preview.png")
    sequences = {}
    for row, (name, durations) in enumerate(STATES.items()):
        poses = frames(encoded, row, len(durations))
        label = "Doing/thinking: glide" if name == "running" else name
        cards = [card(pose, label) for pose in poses]
        sequences[name] = (poses, cards, durations)
        save_animation(cards, durations, preview / "gifs" / f"{name}.gif", videos)
    all_images, all_durations = [], []
    for _, cards, durations in sequences.values():
        all_images.extend(cards)
        all_durations.extend(durations)
    save_animation(all_images, all_durations, preview / "gifs/all-states.gif", videos)
    look = frames(encoded, 9, 8) + frames(encoded, 10, 8)
    look_cards = [card(pose, f"Look {angle} degrees") for pose, angle in zip(look, ANGLES)]
    save_animation(look_cards, [180] * 16, preview / "gifs/look-loop.gif", videos)
    idle, _, idle_durations = sequences["idle"]
    jump, _, jump_durations = sequences["jumping"]
    jump_labels = ["Anticipation", "Lift", "Peak", "Descent", "Settle"]
    transition = [card(pose, "Idle") for pose in idle]
    transition += [card(pose, label) for pose, label in zip(jump, jump_labels)]
    transition += [card(pose, "Idle") for pose in idle]
    transition_durations = idle_durations + jump_durations + idle_durations
    save_animation(transition, transition_durations, preview / "gifs/idle-jump-idle.gif", videos)
    # Screen travel illustrates movement. It is not embedded into the atlas.
    glide, _, glide_durations = sequences["running"]
    travel = []
    cycle = sum(glide_durations)
    for milliseconds in range(0, 6400, 50):
        within_cycle, elapsed = milliseconds % cycle, 0
        pose = glide[-1]
        for candidate, duration in zip(glide, glide_durations):
            elapsed += duration
            if within_cycle < elapsed:
                pose = candidate
                break
        right = milliseconds < 3200
        progress = milliseconds / 3200 if right else 2 - milliseconds / 3200
        pose = pose if right else ImageOps.mirror(pose)
        canvas = Image.new("RGB", (560, 250), "#202337")
        canvas.paste(pose, (round(8 + 352 * progress), 18), pose)
        ImageDraw.Draw(canvas).text((10, 228), "Gliding right" if right else "Gliding left",
                                  fill="#eef0ff", font=font(13))
        travel.append(canvas)
    save_animation(travel, [50] * len(travel), preview / "gifs/gliding-left-right.gif", videos)
    complete = travel + [card(pose, label, 560) for pose, label in zip(
        idle + jump + idle, ["Idle"] * 6 + jump_labels + ["Idle"] * 6)]
    complete += [card(pose, f"Look {angle} degrees", 560) for pose, angle in zip(look, ANGLES)]
    complete_times = [50] * len(travel) + transition_durations + [180] * 16
    save_animation(complete, complete_times, preview / "gifs/complete-motion.gif", videos)
    result = verify(root)
    (root / "validation").mkdir(exist_ok=True)
    (root / "validation/validation.json").write_text(json.dumps(result, indent=2) + "\n")
    (root / "validation/package-notes.json").write_text(json.dumps({
        "id": "float-ghost", "display_name": "Float Ghost", "atlas_version": 2,
        "source": "source/spritesheet.png", "runtime": "pet/spritesheet.webp",
        "rgba_sha256": result["rgba_sha256"],
        "encoding": "Lossless WebP; decoded RGBA pixels are identical to the source PNG",
        "compatibility": "Requires an 8-column, 11-row v2 atlas consumer; older local clients are unverified",
    }, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="Check existing assets without changing them")
    parser.add_argument("--no-videos", action="store_true", help="Build GIF previews without invoking ffmpeg")
    args = parser.parse_args()
    if not args.check and not args.no_videos and not shutil.which("ffmpeg"):
        parser.error("ffmpeg is required for MP4 previews; install it or use --no-videos")
    result = verify(args.root) if args.check else build(args.root, not args.no_videos)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
