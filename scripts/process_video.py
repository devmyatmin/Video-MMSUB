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
    segments, info = model.transcribe(
        str(audio), language=None, beam_size=5, vad_filter=True
    )
    detected_language = getattr(info, "language", "unknown")
    print(f"Detected source language: {detected_language}")
    segments = list(segments)
    if not segments:
        segments, info = model.transcribe(
            str(audio), language=None, beam_size=5, vad_filter=False
        )
        detected_language = getattr(info, "language", detected_language)
        segments = list(segments)
    if not segments:
        raise RuntimeError("Speech transcription returned no text.")

    translator = GoogleTranslator(source="auto", target="my")
    lines = []
    translation_errors = []
    for segment in segments:
        original = segment.text.strip()
        if not original:
            continue
        translated = None
        last_error = None
        # Retry translation instead of silently keeping the original-language text.
        for attempt in range(3):
            try:
                translated = translator.translate(original)
                if translated and translated.strip():
                    break
                last_error = "Translator returned an empty result"
            except Exception as exc:
                last_error = str(exc)
        if not translated or not translated.strip():
            translation_errors.append(f"{original[:100]}: {last_error}")
            continue
        translated = translated.strip()
        # Do not accept an unchanged Latin-script line as a successful Burmese translation.
        if detected_language in {"en", "ja"} and translated == original:
            translation_errors.append(f"Translation unchanged: {original[:100]}")
            continue
        lines.append(
            f"{len(lines) + 1}\n{srt_time(segment.start)} --> {srt_time(segment.end)}\n"
            f"{translated}\n"
        )

    if not lines:
        detail = "; ".join(translation_errors[:3])
        raise RuntimeError(
            "Myanmar translation failed; refusing to publish an unsubtitled/original-language video. "
            + detail
        )

    subtitles.write_text("\n".join(lines), encoding="utf-8")
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
