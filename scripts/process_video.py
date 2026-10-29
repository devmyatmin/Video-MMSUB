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
    run(["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)])
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(audio), language=None, beam_size=5, vad_filter=True)
    language = getattr(info, "language", "unknown")
    print(f"Detected source language: {language}")
    segments = list(segments)
    if not segments:
        segments, info = model.transcribe(str(audio), language=None, beam_size=5, vad_filter=False)
        segments = list(segments)
    entries = [(srt_time(s.start), srt_time(s.end), s.text.strip()) for s in segments if s.text.strip()]
    if not entries:
        raise RuntimeError("Speech transcription returned no usable text.")
    blocks = [f"{i}\n{start} --> {end}\n{text}\n" for i, (start, end, text) in enumerate(entries, 1)]
    output_srt.write_text("\n".join(blocks), encoding="utf-8")
    audio.unlink(missing_ok=True)
    print(f"Created source-language SRT with {len(entries)} entries; detected language={language}.")

def burn_srt_into_video(source, srt_path, output):
    source, srt_path, output = Path(source).resolve(), Path(srt_path).resolve(), Path(output).resolve()
    if not srt_path.is_file() or not srt_path.stat().st_size:
        raise RuntimeError("The supplied SRT file is missing or empty.")
    escaped = str(srt_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf = f"subtitles='{escaped}':force_style='FontName=Noto Sans CJK JP,FontSize=22,Outline=2,Shadow=1,Alignment=2,MarginV=28'"
    run(["ffmpeg", "-y", "-i", str(source), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output)])

if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "transcribe":
        transcribe_to_srt(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == "burn":
        burn_srt_into_video(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        raise SystemExit("Usage: process_video.py transcribe INPUT_VIDEO OUTPUT_SRT | burn INPUT_VIDEO INPUT_SRT OUTPUT_MP4")
