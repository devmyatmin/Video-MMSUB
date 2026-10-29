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


def translate_srt_entries(entries, translator, batch_size=12):
    """Translate groups of subtitle lines in one request while preserving timing."""
    translated_all = []
    for start in range(0, len(entries), batch_size):
        batch = entries[start:start + batch_size]
        # Unique separators let us translate multiple subtitle lines in one request.
        marker = "ZXQSUBTITLEBOUNDARY"
        joined = f"\n{marker}\n".join(text for _, _, text in batch)
        translated_text = None
        last_error = None
        for attempt in range(4):
            try:
                candidate = translator.translate(joined)
                parts = candidate.split(marker) if candidate else []
                parts = [part.strip() for part in parts]
                if len(parts) == len(batch) and all(parts):
                    translated_text = parts
                    break
                last_error = "Translator changed subtitle separators"
            except Exception as exc:
                last_error = str(exc)
            if attempt < 3:
                delay = 3 * (2 ** attempt)
                print(f"Translation chunk retry {attempt + 1}; waiting {delay}s: {last_error}")
                time.sleep(delay)
        if translated_text is None:
            # Smaller fallback requests if a combined chunk is rejected or separators change.
            translated_text = []
            for _, _, original in batch:
                value = ""
                for attempt in range(3):
                    try:
                        value = translator.translate(original) or ""
                        if value.strip():
                            break
                    except Exception as exc:
                        last_error = str(exc)
                    if attempt < 2:
                        time.sleep(4 * (attempt + 1))
                translated_text.append(value.strip())
        translated_all.extend(translated_text)
        if start + batch_size < len(entries):
            time.sleep(1)
    return translated_all


def main(source, output):
    source = Path(source).resolve()
    output = Path(output).resolve()
    work = output.parent
    audio = work / "audio.wav"
    original_srt = work / "original.srt"
    translated_srt = work / "myanmar.srt"

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

    entries = [
        (srt_time(segment.start), srt_time(segment.end), segment.text.strip())
        for segment in segments if segment.text.strip()
    ]
    if not entries:
        raise RuntimeError("Speech transcription returned no usable subtitle text.")

    # Stage 1: write the source-language SRT before translating.
    original_blocks = [
        f"{i}\n{start} --> {end}\n{text}\n"
        for i, (start, end, text) in enumerate(entries, 1)
    ]
    original_srt.write_text("\n".join(original_blocks), encoding="utf-8")
    print(f"Created source SRT with {len(entries)} subtitle entries.")

    # Stage 2: translate groups of SRT text while preserving original timestamps.
    translator = GoogleTranslator(source="auto", target="my")
    translations = translate_srt_entries(entries, translator)
    translated_blocks = []
    failed = 0
    for i, ((start, end, original), translated) in enumerate(zip(entries, translations), 1):
        if not translated:
            failed += 1
            continue
        if detected_language == "en" and translated == original:
            failed += 1
            continue
        translated_blocks.append(f"{i}\n{start} --> {end}\n{translated}\n")

    if not translated_blocks:
        raise RuntimeError("Myanmar translation failed; no translated subtitle lines were produced.")
    if failed:
        print(f"Warning: {failed} subtitle entries could not be translated.")

    translated_srt.write_text("\n".join(translated_blocks), encoding="utf-8")
    escaped = str(translated_srt).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf = f"subtitles='{escaped}':force_style='FontName=Noto Sans Myanmar,FontSize=22,Outline=2,Shadow=1,Alignment=2,MarginV=28'"
    run(["ffmpeg", "-y", "-i", str(source), "-vf", vf, "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(output)])
    audio.unlink(missing_ok=True)
    original_srt.unlink(missing_ok=True)
    translated_srt.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: process_video.py INPUT_VIDEO OUTPUT_MP4")
    main(sys.argv[1], sys.argv[2])
