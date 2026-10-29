import sys
import subprocess
from pathlib import Path

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from faster_whisper import WhisperModel

MODEL_NAME = "facebook/nllb-200-distilled-600M"
TARGET_LANGUAGE = "mya_Mymr"

# Map Whisper language IDs to NLLB-200 language codes.
LANGUAGE_CODES = {
    "af": "afr_Latn", "am": "amh_Ethi", "ar": "arb_Arab", "az": "azj_Latn",
    "be": "bel_Cyrl", "bg": "bul_Cyrl", "bn": "ben_Beng", "bs": "bos_Latn",
    "ca": "cat_Latn", "ceb": "ceb_Latn", "cs": "ces_Latn", "cy": "cym_Latn",
    "da": "dan_Latn", "de": "deu_Latn", "el": "ell_Grek", "en": "eng_Latn",
    "es": "spa_Latn", "et": "est_Latn", "fa": "pes_Arab", "fi": "fin_Latn",
    "fr": "fra_Latn", "ga": "gle_Latn", "gl": "glg_Latn", "gu": "guj_Gujr",
    "ha": "hau_Latn", "he": "heb_Hebr", "hi": "hin_Deva", "hr": "hrv_Latn",
    "ht": "hat_Latn", "hu": "hun_Latn", "hy": "hye_Armn", "id": "ind_Latn",
    "is": "isl_Latn", "it": "ita_Latn", "ja": "jpn_Jpan", "jv": "jav_Latn",
    "ka": "kat_Geor", "kk": "kaz_Cyrl", "km": "khm_Khmr", "kn": "kan_Knda",
    "ko": "kor_Hang", "lo": "lao_Laoo", "lt": "lit_Latn", "lv": "lvs_Latn",
    "mk": "mkd_Cyrl", "ml": "mal_Mlym", "mn": "khk_Cyrl", "mr": "mar_Deva",
    "ms": "zsm_Latn", "my": "mya_Mymr", "ne": "npi_Deva", "nl": "nld_Latn",
    "no": "nob_Latn", "pa": "pan_Guru", "pl": "pol_Latn", "ps": "pbt_Arab",
    "pt": "por_Latn", "ro": "ron_Latn", "ru": "rus_Cyrl", "si": "sin_Sinh",
    "sk": "slk_Latn", "sl": "slv_Latn", "sq": "als_Latn", "sr": "srp_Cyrl",
    "sv": "swe_Latn", "sw": "swh_Latn", "ta": "tam_Taml", "te": "tel_Telu",
    "th": "tha_Thai", "tl": "tgl_Latn", "tr": "tur_Latn", "uk": "ukr_Cyrl",
    "ur": "urd_Arab", "uz": "uzn_Latn", "vi": "vie_Latn", "yo": "yor_Latn",
    "zh": "zho_Hans", "yue": "yue_Hant",
}


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


def translate_to_myanmar(texts, source_language):
    source_code = LANGUAGE_CODES.get(source_language)
    if not source_code:
        raise RuntimeError(
            f"NLLB-200 language mapping is not configured for detected language "
            f"'{source_language}'. Add its NLLB language code to LANGUAGE_CODES."
        )
    print(f"Loading offline translation model: {MODEL_NAME}")
    print("The model is downloaded into the Actions runner cache for this job.")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, src_lang=source_code)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME)
    model.eval()

    translated = []
    batch_size = 8
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        encoded = tokenizer(
            batch, return_tensors="pt", padding=True, truncation=True, max_length=512
        )
        with torch.inference_mode():
            generated = model.generate(
                **encoded,
                forced_bos_token_id=tokenizer.convert_tokens_to_ids(TARGET_LANGUAGE),
                max_new_tokens=256,
                num_beams=3,
            )
        translated.extend(tokenizer.batch_decode(generated, skip_special_tokens=True))
        print(f"Translated {min(start + len(batch), len(texts))}/{len(texts)} subtitle segments")
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
    translations = translate_to_myanmar(originals, detected_language)

    lines = []
    for segment, original, translated in zip(usable_segments, originals, translations):
        translated = translated.strip()
        if not translated:
            continue
        # Prevent accidentally publishing source-language subtitles as Burmese.
        if detected_language == "en" and translated == original:
            raise RuntimeError("Translation returned unchanged English text; refusing to publish.")
        lines.append(
            f"{len(lines) + 1}\\n{srt_time(segment.start)} --> {srt_time(segment.end)}\\n"
            f"{translated}\\n"
        )

    if not lines:
        raise RuntimeError("Myanmar translation returned no subtitle lines.")

    subtitles.write_text("\\n".join(lines), encoding="utf-8")
    escaped = str(subtitles).replace("\\\\", "\\\\\\\\").replace(":", "\\\\:").replace("'", "\\\\'")
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
