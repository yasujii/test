#!/usr/bin/env python3
"""
画像から動画を生成する動画編集スクリプト

機能:
  - ケン・バーンズエフェクト（ズーム＆パン）
  - フェードイン / フェードアウト
  - テキストオーバーレイ
  - スライドトランジション
  - 複数エフェクトの連結

使い方:
  python3 video_editor.py input_image.png -o output.mp4
  python3 video_editor.py input_image.png --effect ken_burns --duration 8
  python3 video_editor.py input_image.png --effect all --text "Hello World"
"""

import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


# ──────────────────────────────────────────────
# エフェクト関数群
# ──────────────────────────────────────────────

def effect_ken_burns(img, frame_idx, total_frames, direction="zoom_in"):
    """ケン・バーンズエフェクト：ゆっくりズーム＆パンする"""
    w, h = img.size
    t = frame_idx / max(total_frames - 1, 1)

    if direction == "zoom_in":
        scale = 1.0 + t * 0.3
        cx = w // 2 + int(t * w * 0.05)
        cy = h // 2 + int(t * h * 0.03)
    elif direction == "zoom_out":
        scale = 1.3 - t * 0.3
        cx = w // 2 - int(t * w * 0.05)
        cy = h // 2 - int(t * h * 0.03)
    else:
        scale = 1.0 + 0.15 * math.sin(t * math.pi)
        cx = w // 2 + int(math.sin(t * math.pi * 2) * w * 0.05)
        cy = h // 2 + int(math.cos(t * math.pi * 2) * h * 0.03)

    crop_w = int(w / scale)
    crop_h = int(h / scale)
    x1 = max(0, min(cx - crop_w // 2, w - crop_w))
    y1 = max(0, min(cy - crop_h // 2, h - crop_h))

    cropped = img.crop((x1, y1, x1 + crop_w, y1 + crop_h))
    return cropped.resize((w, h), Image.LANCZOS)


def effect_fade_in(img, frame_idx, total_frames):
    """フェードインエフェクト：黒からフェードイン"""
    t = frame_idx / max(total_frames - 1, 1)
    alpha = min(1.0, t * 2)  # 前半でフェードイン完了
    black = Image.new("RGB", img.size, (0, 0, 0))
    return Image.blend(black, img, alpha)


def effect_fade_out(img, frame_idx, total_frames):
    """フェードアウトエフェクト：黒へフェードアウト"""
    t = frame_idx / max(total_frames - 1, 1)
    alpha = max(0.0, 1.0 - (t - 0.5) * 2) if t > 0.5 else 1.0
    black = Image.new("RGB", img.size, (0, 0, 0))
    return Image.blend(black, img, alpha)


def effect_slide(img, frame_idx, total_frames, direction="left"):
    """スライドエフェクト：画像がスライドして登場"""
    w, h = img.size
    t = frame_idx / max(total_frames - 1, 1)
    ease_t = t * t * (3 - 2 * t)  # smoothstep

    canvas = Image.new("RGB", (w, h), (0, 0, 0))
    if direction == "left":
        offset_x = int(w * (1 - ease_t))
        canvas.paste(img, (offset_x, 0))
    elif direction == "right":
        offset_x = int(-w * (1 - ease_t))
        canvas.paste(img, (offset_x, 0))
    elif direction == "up":
        offset_y = int(h * (1 - ease_t))
        canvas.paste(img, (0, offset_y))
    else:
        offset_y = int(-h * (1 - ease_t))
        canvas.paste(img, (0, offset_y))
    return canvas


def effect_blur_to_sharp(img, frame_idx, total_frames):
    """ぼかし→シャープに変化するエフェクト"""
    t = frame_idx / max(total_frames - 1, 1)
    blur_radius = max(0, int(20 * (1 - t)))
    if blur_radius > 0:
        return img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    return img.copy()


def effect_brightness_pulse(img, frame_idx, total_frames):
    """明るさが脈動するエフェクト"""
    t = frame_idx / max(total_frames - 1, 1)
    brightness = 0.7 + 0.6 * math.sin(t * math.pi * 2)
    enhancer = ImageEnhance.Brightness(img)
    return enhancer.enhance(brightness)


def add_text_overlay(img, text, frame_idx, total_frames, position="center"):
    """テキストオーバーレイを追加"""
    result = img.copy()
    draw = ImageDraw.Draw(result)
    w, h = result.size

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60
        )
    except OSError:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    if position == "center":
        x = (w - text_w) // 2
        y = (h - text_h) // 2
    elif position == "bottom":
        x = (w - text_w) // 2
        y = h - text_h - 80
    else:
        x = (w - text_w) // 2
        y = 60

    # フェードイン表示
    t = frame_idx / max(total_frames - 1, 1)
    alpha = int(min(1.0, t * 3) * 255)

    # テキスト背景（半透明）
    padding = 20
    overlay = Image.new("RGBA", result.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(
        [x - padding, y - padding, x + text_w + padding, y + text_h + padding],
        fill=(0, 0, 0, min(alpha, 160)),
    )
    overlay_draw.text((x, y), text, fill=(255, 255, 255, alpha), font=font)

    result = result.convert("RGBA")
    result = Image.alpha_composite(result, overlay)
    return result.convert("RGB")


# ──────────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────────

EFFECTS = {
    "ken_burns": "ケン・バーンズ（ズーム＆パン）",
    "ken_burns_out": "ケン・バーンズ（ズームアウト）",
    "fade_in": "フェードイン",
    "fade_out": "フェードアウト",
    "slide_left": "スライド（左から）",
    "slide_up": "スライド（下から）",
    "blur_to_sharp": "ぼかし→シャープ",
    "brightness_pulse": "明るさパルス",
    "all": "全エフェクトを連結",
}


def apply_effect(img, effect_name, frame_idx, total_frames):
    """指定エフェクトを適用"""
    if effect_name == "ken_burns":
        return effect_ken_burns(img, frame_idx, total_frames, "zoom_in")
    elif effect_name == "ken_burns_out":
        return effect_ken_burns(img, frame_idx, total_frames, "zoom_out")
    elif effect_name == "fade_in":
        return effect_fade_in(img, frame_idx, total_frames)
    elif effect_name == "fade_out":
        return effect_fade_out(img, frame_idx, total_frames)
    elif effect_name == "slide_left":
        return effect_slide(img, frame_idx, total_frames, "left")
    elif effect_name == "slide_up":
        return effect_slide(img, frame_idx, total_frames, "up")
    elif effect_name == "blur_to_sharp":
        return effect_blur_to_sharp(img, frame_idx, total_frames)
    elif effect_name == "brightness_pulse":
        return effect_brightness_pulse(img, frame_idx, total_frames)
    else:
        return img.copy()


def generate_video(
    image_path,
    output_path="output.mp4",
    effect="ken_burns",
    duration=5,
    fps=30,
    text=None,
    resolution=None,
):
    """画像からエフェクト付き動画を生成"""

    print(f"画像を読み込み中: {image_path}")
    img = Image.open(image_path).convert("RGB")

    if resolution:
        img = img.resize(resolution, Image.LANCZOS)
        print(f"解像度をリサイズ: {resolution[0]}x{resolution[1]}")

    w, h = img.size
    print(f"画像サイズ: {w}x{h}")

    # 幅と高さを偶数に揃える (ffmpeg要件)
    if w % 2 != 0:
        w -= 1
    if h % 2 != 0:
        h -= 1
    if (w, h) != img.size:
        img = img.resize((w, h), Image.LANCZOS)

    tmpdir = tempfile.mkdtemp(prefix="video_edit_")

    try:
        if effect == "all":
            # 全エフェクトを連結
            effects_list = [
                "fade_in",
                "ken_burns",
                "blur_to_sharp",
                "slide_left",
                "ken_burns_out",
                "brightness_pulse",
                "slide_up",
                "fade_out",
            ]
            segment_duration = max(2, duration // len(effects_list))
            total_segment_frames = segment_duration * fps
            global_frame = 0

            for eff in effects_list:
                print(f"  エフェクト適用中: {EFFECTS.get(eff, eff)}")
                for i in range(total_segment_frames):
                    frame = apply_effect(img, eff, i, total_segment_frames)
                    if text:
                        frame = add_text_overlay(frame, text, i, total_segment_frames)
                    frame.save(os.path.join(tmpdir, f"frame_{global_frame:06d}.png"))
                    global_frame += 1
        else:
            total_frames = duration * fps
            print(f"エフェクト: {EFFECTS.get(effect, effect)}")
            print(f"フレーム生成中: {total_frames}フレーム ({duration}秒 x {fps}fps)")

            for i in range(total_frames):
                frame = apply_effect(img, effect, i, total_frames)
                if text:
                    frame = add_text_overlay(frame, text, i, total_frames)
                frame.save(os.path.join(tmpdir, f"frame_{i:06d}.png"))

                if (i + 1) % (fps * 2) == 0 or i == total_frames - 1:
                    pct = (i + 1) / total_frames * 100
                    print(f"  進捗: {pct:.0f}% ({i + 1}/{total_frames})")

        # ffmpegで動画生成
        print(f"動画をエンコード中: {output_path}")
        cmd = [
            "ffmpeg",
            "-y",
            "-framerate", str(fps),
            "-i", os.path.join(tmpdir, "frame_%06d.png"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-crf", "23",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ffmpegエラー: {result.stderr}")
            sys.exit(1)

        file_size = os.path.getsize(output_path)
        print(f"動画を生成しました: {output_path} ({file_size / 1024:.1f} KB)")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(
        description="画像から動画を生成する動画編集ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python3 video_editor.py photo.png
  python3 video_editor.py photo.jpg -o movie.mp4 --effect ken_burns --duration 8
  python3 video_editor.py photo.png --effect all --text "旅の思い出" --duration 20
  python3 video_editor.py photo.png --effect fade_in --resolution 1280x720

利用可能なエフェクト:
"""
        + "\n".join(f"  {k:20s} {v}" for k, v in EFFECTS.items()),
    )

    parser.add_argument("image", help="入力画像のパス")
    parser.add_argument("-o", "--output", default="output.mp4", help="出力動画のパス (デフォルト: output.mp4)")
    parser.add_argument(
        "--effect",
        default="ken_burns",
        choices=list(EFFECTS.keys()),
        help="適用するエフェクト (デフォルト: ken_burns)",
    )
    parser.add_argument("--duration", type=int, default=5, help="動画の長さ（秒）(デフォルト: 5)")
    parser.add_argument("--fps", type=int, default=30, help="フレームレート (デフォルト: 30)")
    parser.add_argument("--text", help="テキストオーバーレイ")
    parser.add_argument("--resolution", help="出力解像度 (例: 1920x1080)")

    args = parser.parse_args()

    if not os.path.exists(args.image):
        print(f"エラー: 画像が見つかりません: {args.image}")
        sys.exit(1)

    resolution = None
    if args.resolution:
        try:
            rw, rh = args.resolution.split("x")
            resolution = (int(rw), int(rh))
        except ValueError:
            print("エラー: 解像度は '1920x1080' の形式で指定してください")
            sys.exit(1)

    generate_video(
        image_path=args.image,
        output_path=args.output,
        effect=args.effect,
        duration=args.duration,
        fps=args.fps,
        text=args.text,
        resolution=resolution,
    )


if __name__ == "__main__":
    main()
