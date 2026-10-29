import sys
import subprocess
from pathlib import Path

from deep_translator import GoogleTranslator
from faster_whisper import WhisperModel


def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Command failed")[-4000:])


def srt_time(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, rem = divmod(milliseconds, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def main(source, output):
    source = Path(source).resolve()
    output = Path(output).resolve()
    work = output.parent
    audio = work / "audio.wav"
    subtitles = work / "myanmar.srt"
    run(["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(audio)])
    model = WhisperModel("small", device="cpu", compute_type="int8")
    # Auto-detect the spoken language. English and Japanese are the primary expected
    # source languages, but leave detection automatic so other languages still work.
    segments, info = model.transcribe(
        str(audio), language=None, beam_size=5, vad_filter=True
    )
    detected_language = getattr(info, "language", "unknown")
    print(f"Detected source language: {detected_language}")
    if detected_language in {"en", "ja"}:
        print("Primary source language detected (English/Japanese).")
    segments = list(segments)
    if not segments:
        # VAD can mistakenly discard quiet or noisy speech; retry without VAD.
        segments, _info = model.transcribe(str(audio), beam_size=5, vad_filter=False)
        segments = list(segments)
    translator = GoogleTranslator(source="auto", target="my")
    lines = []
    for segment in segments:
        original = segment.text.strip()
        if not original:
            continue
        try:
            translated = translator.translate(original) or original
        except Exception:
            translated = original
        lines.append(
            f"{len(lines) + 1}\n{srt_time(segment.start)} --> {srt_time(segment.end)}\n"
            f"{translated.strip()}\n"
        )
    if not lines:
        raise RuntimeError("No speech text was detected, even after retrying without voice-activity filtering.")
    subtitles.write_text("\n".join(lines), encoding="utf-8")
    # Use a relative subtitle path to avoid filter parsing issues with absolute paths.
    escaped = str(subtitles).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf = f"subtitles='{escaped}':force_style='FontName=Noto Sans Myanmar,FontSize=22,Outline=2,Shadow=1,Alignment=2,MarginV=28'"
    run(["ffmpeg", "-y", "-i", str(source), "-vf", vf, "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(output)])
    audio.unlink(missing_ok=True)
    subtitles.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: process_video.py INPUT_VIDEO OUTPUT_MP4")
    main(sys.argv[1], sys.argv[2])
