import sys
import subprocess
from pathlib import Path
import pyidaungsu as pds
from faster_whisper import WhisperModel


def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Command failed")[-4000:])


def srt_time(seconds):
    ms = max(0, round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def transcribe_to_srt(source, output_srt):
    source, output_srt = Path(source).resolve(), Path(output_srt).resolve()
    audio = output_srt.parent / "audio.wav"
    run([
        "ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1",
        "-ar", "16000", "-c:a", "pcm_s16le", str(audio)
    ])

    model = WhisperModel("medium", device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio), language=None, beam_size=5, vad_filter=False,
        condition_on_previous_text=True,
    )
    language = getattr(info, "language", "unknown")
    print(f"Detected source language: {language}")
    segments = list(segments)
    entries = [
        (srt_time(s.start), srt_time(s.end), s.text.strip())
        for s in segments if s.text.strip()
    ]
    if not entries:
        raise RuntimeError("Speech transcription returned no usable text.")

    blocks = [f"{i}\n{start} --> {end}\n{text}\n"
              for i, (start, end, text) in enumerate(entries, 1)]
    output_srt.write_text("\n".join(blocks), encoding="utf-8")
    audio.unlink(missing_ok=True)
    print(
        f"Created source-language SRT with {len(entries)} entries; "
        f"detected language={language}."
    )


def read_srt_text(path):
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        text, encoding = raw.decode("utf-8-sig"), "UTF-8 with BOM"
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text, encoding = raw.decode("utf-16"), "UTF-16 with BOM"
    else:
        try:
            text, encoding = raw.decode("utf-8"), "UTF-8"
        except UnicodeDecodeError as exc:
            raise RuntimeError(
                "SRT file encoding is not UTF-8/UTF-16. Save it as UTF-8 and retry."
            ) from exc
    print(f"Detected SRT text encoding: {encoding}")
    return text


def normalize_myanmar_srt(text):
    # fasttext (used by pyidaungsu) only accepts one line per predict call.
    # Join subtitle text lines with spaces so the detector receives one line.
    lines = text.splitlines()
    candidate_lines = [
        line for line in lines
        if "-->" not in line and not line.strip().isdigit()
        and any("\u1000" <= ch <= "\u109f" for ch in line)
    ]
    candidate = " ".join(line.strip() for line in candidate_lines)
    if not candidate.strip():
        print("No Myanmar-script text detected; keeping subtitle text unchanged.")
        return text

    try:
        detected = pds.detect(candidate)
    except Exception as exc:
        print(f"Myanmar encoding detection failed ({exc}); preserving original text.")
        return text

    print(f"Detected Myanmar subtitle type: {detected}")
    if detected == "mm_zg":
        converted = pds.cvt2uni(text)
        print("Converted Zawgyi subtitle text to Unicode for correct rendering.")
        return converted
    if detected == "mm_uni":
        print("Subtitle text is already Unicode; preserving its wording.")
        return text

    print("Myanmar encoding detection was uncertain; preserving original text.")
    return text


def burn_srt_into_video(source, srt_path, output):
    source, srt_path, output = (
        Path(source).resolve(), Path(srt_path).resolve(), Path(output).resolve()
    )
    if not srt_path.is_file() or not srt_path.stat().st_size:
        raise RuntimeError("The supplied SRT file is missing or empty.")

    subtitle_text = read_srt_text(srt_path)
    if "-->" not in subtitle_text:
        raise RuntimeError("The supplied file does not look like an SRT (missing -->).")
    subtitle_text = subtitle_text.replace("\r\n", "\n").replace("\r", "\n")
    subtitle_text = normalize_myanmar_srt(subtitle_text)
    srt_path.write_text(subtitle_text, encoding="utf-8")

    escaped = (
        str(srt_path).replace("\\", "\\\\")
        .replace(":", "\\:").replace("'", "\\'")
    )
    vf = (
        f"subtitles='{escaped}':"
        "force_style='FontName=Noto Sans Myanmar,FontSize=26,PrimaryColour=&H0000D7FF,OutlineColour=&H00000000,Outline=2,Shadow=1,Alignment=2,MarginV=30'"
    )
    run([
        "ffmpeg", "-y", "-i", str(source), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(output),
    ])


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "transcribe":
        transcribe_to_srt(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == "burn":
        burn_srt_into_video(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit(
            "Usage: process_video.py transcribe INPUT_VIDEO OUTPUT_SRT | "
            "burn INPUT_VIDEO INPUT_SRT OUTPUT_MP4"
        )
