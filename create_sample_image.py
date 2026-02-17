"""サンプル画像を生成するスクリプト"""
from PIL import Image, ImageDraw, ImageFont

def create_sample_image(output_path="input_image.png", width=1920, height=1080):
    """グラデーション背景にテキストを配置したサンプル画像を生成"""
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    # グラデーション背景
    for y in range(height):
        r = int(30 + (y / height) * 100)
        g = int(60 + (y / height) * 120)
        b = int(120 + (y / height) * 135)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # 装飾的な円
    for i in range(5):
        x = width // 6 * (i + 1)
        y_pos = height // 2
        radius = 80 + i * 20
        draw.ellipse(
            [x - radius, y_pos - radius, x + radius, y_pos + radius],
            outline=(255, 255, 255, 180),
            width=3,
        )

    # テキスト
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
    except OSError:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    title = "Video Editing Demo"
    bbox = draw.textbbox((0, 0), title, font=font_large)
    text_w = bbox[2] - bbox[0]
    draw.text(((width - text_w) // 2, height // 3), title, fill=(255, 255, 255), font=font_large)

    subtitle = "Created with Python + FFmpeg"
    bbox2 = draw.textbbox((0, 0), subtitle, font=font_small)
    text_w2 = bbox2[2] - bbox2[0]
    draw.text(((width - text_w2) // 2, height // 3 + 100), subtitle, fill=(200, 220, 255), font=font_small)

    img.save(output_path)
    print(f"サンプル画像を作成しました: {output_path}")
    return output_path


if __name__ == "__main__":
    create_sample_image()
