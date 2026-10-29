import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    media = root / "media"
    media.mkdir(exist_ok=True)
    for name, color, frequency in [("blue.mp4", "blue", 440), ("green.mp4", "green", 660)]:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=25:d=2",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=2",
            "-c:v", "mpeg4", "-q:v", "5", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(media / name),
        ], check=True)
    print("Created two synthetic 2-second clips in examples/media/.")


if __name__ == "__main__":
    main()
