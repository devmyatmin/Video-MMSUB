import sys
import subprocess
from pathlib import Path
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
    # Preserve Whisper output exactly; do not run Myanmar encoding detection or conversion.
    srt_text = "\n".join(blocks)
    output_srt.write_text(srt_text, encoding="utf-8")
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



def add_srt_track_to_video(source, srt_path, output):
    """Add styled ASS subtitles as a selectable track in MKV; do not burn them."""
    source, srt_path, output = (
        Path(source).resolve(), Path(srt_path).resolve(), Path(output).resolve()
    )
    if not srt_path.is_file() or not srt_path.stat().st_size:
        raise RuntimeError("The supplied SRT file is missing or empty.")

    subtitle_text = read_srt_text(srt_path)
    if "-->" not in subtitle_text:
        raise RuntimeError("The supplied file does not look like an SRT (missing -->).")

    # Convert SRT to ASS so style settings can travel with the selectable subtitle
    # track. Gold is ASS BGR color &H0000D7FF; use a smaller font and keep subtitles closer to the bottom.
    ass_path = output.with_suffix(".styled.ass")
    try:
        run(["ffmpeg", "-y", "-i", str(srt_path), "-c:s", "ass", str(ass_path)])
        ass_text = ass_path.read_text(encoding="utf-8-sig")
        lines = ass_text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("Style: Default,"):
                fields = line[len("Style: "):].split(",")
                if len(fields) >= 23:
                    fields[1] = "Noto Sans Myanmar"
                    fields[2] = "19"
                    fields[3] = "&H0000D7FF"
                    fields[4] = "&H0000D7FF"
                    fields[5] = "&H00000000"
                    fields[6] = "&H80000000"
                    fields[7] = "0"
                    fields[16] = "2"
                    fields[17] = "1"
                    fields[18] = "2"
                    fields[21] = "60"
                    lines[i] = "Style: " + ",".join(fields)
                break
        else:
            raise RuntimeError("Could not find the default style in converted ASS subtitles.")
        ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # MKV supports a selectable ASS track with embedded style metadata.
        # Video/audio are stream-copied; subtitle text is not rendered into frames.
        run([
            "ffmpeg", "-y", "-i", str(source), "-i", str(ass_path),
            "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
            "-c", "copy", "-c:s", "ass",
            "-metadata:s:s:0", "language=und",
            "-metadata:s:s:0", "title=Gold Myanmar subtitles",
            str(output),
        ])
    finally:
        ass_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "transcribe":
        transcribe_to_srt(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] in {"add-srt", "mux-srt"}:
        add_srt_track_to_video(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit(
            "Usage: process_video.py transcribe INPUT_VIDEO OUTPUT_SRT | "
            "add-srt INPUT_VIDEO INPUT_SRT OUTPUT_MKV"
        )
