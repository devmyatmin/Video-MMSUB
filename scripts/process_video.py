import sys
import subprocess
import time
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


def translate_in_batches(translator, texts, batch_size=8):
    """Translate small batches with retries and backoff to reduce rate-limit errors."""
    translated = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        result = None
        last_error = None
        for attempt in range(4):
            try:
                result = translator.translate_batch(batch)
                if result and len(result) == len(batch) and all(
                    isinstance(item, str) and item.strip() for item in result
                ):
                    break
                last_error = "Translator returned missing/empty batch items"
                result = None
            except Exception as exc:
                last_error = str(exc)
                result = None
            if attempt < 3:
                delay = min(30, 3 * (2 ** attempt))
                print(f"Translation batch retry {attempt + 1}; waiting {delay}s: {last_error}")
                time.sleep(delay)
        if result is None:
            # Fall back to translating items individually, slowly, after batch retries.
            result = []
            for item in batch:
                value = None
                for attempt in range(3):
                    try:
                        value = translator.translate(item)
                        if value and value.strip():
                            break
                    except Exception as exc:
                        last_error = str(exc)
                    if attempt < 2:
                        time.sleep(5 * (attempt + 1))
                result.append(value.strip() if value and value.strip() else "")
        translated.extend(result)
        if start + batch_size < len(texts):
            time.sleep(1.5)
    return translated


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

    usable_segments = [segment for segment in segments if segment.text.strip()]
    originals = [segment.text.strip() for segment in usable_segments]
    translator = GoogleTranslator(source="auto", target="my")
    translations = translate_in_batches(translator, originals)

    lines = []
    translation_errors = []
    for segment, original, translated in zip(usable_segments, originals, translations):
        if not translated:
            translation_errors.append(f"Translation failed: {original[:100]}")
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

    if translation_errors:
        print(f"Warning: {len(translation_errors)} subtitle segments could not be translated.")

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
