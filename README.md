# Video Editor - 画像から動画を生成

画像1枚からエフェクト付き動画を生成するPythonスクリプトです。

## 必要環境

- Python 3.8+
- Pillow (`pip install Pillow`)
- FFmpeg

## 使い方

```bash
# 基本的な使い方（ケン・バーンズエフェクト）
python3 video_editor.py photo.png

# エフェクトを指定
python3 video_editor.py photo.png --effect fade_in --duration 5

# テキストオーバーレイ付き
python3 video_editor.py photo.png --effect ken_burns --text "旅の思い出"

# 全エフェクトを連結した動画
python3 video_editor.py photo.png --effect all --duration 20

# 解像度を指定
python3 video_editor.py photo.png --resolution 1280x720 -o output.mp4
```

## 利用可能なエフェクト

| エフェクト | 説明 |
|---|---|
| `ken_burns` | ズーム＆パン（デフォルト） |
| `ken_burns_out` | ズームアウト |
| `fade_in` | 黒からフェードイン |
| `fade_out` | 黒へフェードアウト |
| `slide_left` | 左からスライドイン |
| `slide_up` | 下からスライドイン |
| `blur_to_sharp` | ぼかし→シャープに変化 |
| `brightness_pulse` | 明るさが脈動 |
| `all` | 全エフェクトを連結 |

## サンプル画像の生成

```bash
python3 create_sample_image.py
```
