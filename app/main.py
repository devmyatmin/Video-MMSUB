import os
import shutil
import subprocess
import threading
import uuid
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deep_translator import GoogleTranslator
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from faster_whisper import WhisperModel

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
JOBS_DIR = DATA_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
APP_USERNAME = os.getenv("APP_USERNAME", "owner")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB

app = FastAPI(title="Video MMSUB", docs_url=None, redoc_url=None)
security = HTTPBasic()
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()
model_lock = threading.Lock()
_whisper_model = None


def require_login(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not APP_PASSWORD:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Server admin must configure APP_PASSWORD.")
    user_ok = secrets.compare_digest(credentials.username.encode(), APP_USERNAME.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), APP_PASSWORD.encode())
    if not (user_ok and pass_ok):
        from fastapi import status
        raise HTTPException(status_code=401, detail="Incorrect username or password",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username


def update_job(job_id: str, **values: Any) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(values)
            jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()


def get_model():
    global _whisper_model
    with model_lock:
        if _whisper_model is None:
            _whisper_model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                                          compute_type=WHISPER_COMPUTE_TYPE)
        return _whisper_model


def run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg failed")[-4000:])


def seconds_to_srt(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def process_video(job_id: str, input_path: Path, output_dir: Path) -> None:
    try:
        update_job(job_id, status="processing", progress=5, message="Video အသံကို စာသားပြောင်းနေပါတယ်…")
        audio_path = output_dir / "audio.wav"
        srt_path = output_dir / "myanmar.srt"
        output_path = output_dir / "with-myanmar-subtitles.mp4"
        run_ffmpeg(["ffmpeg", "-y", "-i", str(input_path), "-vn", "-ac", "1",
                    "-ar", "16000", "-c:a", "pcm_s16le", str(audio_path)])
        model = get_model()
        segments, info = model.transcribe(str(audio_path), beam_size=5, vad_filter=True)
        translated = []
        translator = GoogleTranslator(source="auto", target="my")
        for index, segment in enumerate(segments, start=1):
            source_text = segment.text.strip()
            if not source_text:
                continue
            try:
                myanmar_text = translator.translate(source_text) or source_text
            except Exception:
                myanmar_text = source_text
            translated.append(
                f"{len(translated) + 1}\n{seconds_to_srt(segment.start)} --> "
                f"{seconds_to_srt(segment.end)}\n{myanmar_text.strip()}\n"
            )
            if index % 10 == 0:
                update_job(job_id, progress=min(75, 10 + index // 2),
                           message=f"မြန်မာစာတန်း ပြုလုပ်နေပါတယ်… ({index} segments)")
        if not translated:
            raise RuntimeError("အသံထဲမှ စာသားမတွေ့ပါ။")
        srt_path.write_text("\n".join(translated), encoding="utf-8")
        update_job(job_id, progress=80, message="Video ထဲ စာတန်းထည့်နေပါတယ်…")
        subtitle_filter = (
            f"subtitles={srt_path}:force_style="
            "'FontName=Noto Sans Myanmar,FontSize=22,Outline=2,Shadow=1,Alignment=2,MarginV=28'"
        )
        run_ffmpeg(["ffmpeg", "-y", "-i", str(input_path), "-vf", subtitle_filter,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output_path)])
        audio_path.unlink(missing_ok=True)
        input_path.unlink(missing_ok=True)
        update_job(job_id, status="completed", progress=100,
                   message="ပြီးပါပြီ။ မြန်မာစာတန်းထိုး Video ကို ဒေါင်းလုဒ်လုပ်နိုင်ပါပြီ။",
                   download_url=f"/download/{job_id}",
                   detected_language=getattr(info, "language", None))
    except Exception as exc:
        update_job(job_id, status="failed", progress=100,
                   message="လုပ်ဆောင်မှု မအောင်မြင်ပါ။", error=str(exc)[-1500:])
        input_path.unlink(missing_ok=True)


PAGE = """<!doctype html>
<html lang="my"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Video MMSUB</title><style>
body{font-family:system-ui,'Noto Sans Myanmar',sans-serif;background:#101827;color:#eef2ff;margin:0}
main{max-width:760px;margin:6vh auto;padding:24px}.panel{background:#1c293d;padding:24px;border-radius:18px}
h1{margin-top:0}p{line-height:1.7;color:#cbd5e1}input,button{box-sizing:border-box;width:100%;padding:14px;margin:10px 0;border-radius:10px;border:1px solid #475569}
button{background:#38bdf8;color:#082f49;font-weight:700;cursor:pointer;border:0}
#status{white-space:pre-wrap;overflow-wrap:anywhere}.hint{font-size:.9rem;color:#94a3b8}
</style></head><body><main><section class="panel">
<h1>🎬 Video MMSUB</h1>
<p>Video တင်ပါ။ အသံကို စာသားပြောင်းပြီး မြန်မာဘာသာပြန်စာတန်းထိုးထားတဲ့ MP4 ကို ဒီ Web မှာပဲ ပြန်ယူနိုင်ပါတယ်။</p>
<form id="upload"><label for="video">Video ဖိုင်</label><input id="video" name="video" type="file" accept="video/*,.mkv,.avi,.mov,.webm" required><button id="submit" type="submit">တင်ပြီး စာတန်းထိုးမယ်</button></form>
<div id="status" role="status"></div><div id="download"></div>
<p class="hint">Video ဖိုင်အရွယ်အစား အများဆုံး 5 GB (5 GiB) အထိ လက်ခံပါတယ်။ Server/hosting ကန့်သတ်ချက်များ သက်ရောက်နိုင်ပါတယ်။</p>
</section></main><script>
const form=document.querySelector('#upload'),statusEl=document.querySelector('#status'),button=document.querySelector('#submit'),download=document.querySelector('#download');
form.addEventListener('submit',async e=>{e.preventDefault();download.innerHTML='';statusEl.textContent='Video တင်နေပါတယ်…';button.disabled=true;
try{const data=new FormData();data.append('video',document.querySelector('#video').files[0]);const res=await fetch('/upload',{method:'POST',body:data});const body=await res.json();if(!res.ok)throw new Error(body.detail||'Upload မအောင်မြင်ပါ');statusEl.textContent='အလုပ်စတင်ပါပြီ။ Job ID: '+body.job_id;
const poll=async()=>{const r=await fetch('/status/'+body.job_id);const j=await r.json();statusEl.textContent=j.message+'\\nProgress: '+j.progress+'%';if(j.status==='completed'){download.innerHTML='<a style="color:#7dd3fc" href="'+j.download_url+'">မြန်မာစာတန်းထိုး Video ကို ဒေါင်းလုဒ်ယူရန်</a>';button.disabled=false;return}if(j.status==='failed'){statusEl.textContent+='\\nError: '+(j.error||'unknown');button.disabled=false;return}setTimeout(poll,4000)};poll();
}catch(err){statusEl.textContent=err.message;button.disabled=false}});
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def home(_: str = Depends(require_login)):
    return PAGE


@app.post("/upload")
async def upload_video(background_tasks: BackgroundTasks, video: UploadFile = File(...),
                       _: str = Depends(require_login)):
    if not video.filename:
        raise HTTPException(status_code=400, detail="Video ဖိုင်ရွေးပေးပါ။")
    suffix = Path(video.filename).suffix.lower()
    if suffix not in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}:
        raise HTTPException(status_code=400, detail="MP4, MKV, MOV, AVI, WEBM ဖိုင်များသာ လက်ခံပါတယ်။")
    job_id = uuid.uuid4().hex
    output_dir = JOBS_DIR / job_id
    output_dir.mkdir(parents=True, exist_ok=False)
    input_path = output_dir / f"input{suffix}"
    try:
        total_bytes = 0
        with input_path.open("wb") as destination:
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Video ဖိုင်အရွယ်အစား 5 GB ထက်မကျော်ရပါ။")
                destination.write(chunk)
    except HTTPException:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Upload သိမ်းဆည်းမရပါ။ Server disk ကိုစစ်ပါ။")
    finally:
        await video.close()
    now = datetime.now(timezone.utc).isoformat()
    with jobs_lock:
        jobs[job_id] = {"job_id": job_id, "status": "queued", "progress": 0,
                        "message": "Queue ထဲထည့်ပြီးပါပြီ။", "created_at": now,
                        "updated_at": now, "download_url": None}
    background_tasks.add_task(process_video, job_id, input_path, output_dir)
    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
def job_status(job_id: str, _: str = Depends(require_login)):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job မတွေ့ပါ။ Server restart ဖြစ်ထားနိုင်ပါတယ်။")
        return dict(job)


@app.get("/download/{job_id}")
def download_video(job_id: str, _: str = Depends(require_login)):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job.get("status") != "completed":
        raise HTTPException(status_code=404, detail="ဒေါင်းလုဒ်လုပ်ရန် Video မရသေးပါ။")
    output_path = JOBS_DIR / job_id / "with-myanmar-subtitles.mp4"
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output ဖိုင်မတွေ့ပါ။")
    return FileResponse(output_path, media_type="video/mp4",
                        filename=f"Video-MMSUB-{job_id[:8]}.mp4")
