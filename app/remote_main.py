import json
import os
import secrets
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
JOBS_DIR = DATA_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
APP_USERNAME = os.getenv("APP_USERNAME", "owner")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "devmyatmin/Video-MMSUB")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_WORKFLOW_FILE = os.getenv("GITHUB_WORKFLOW_FILE", "process-video.yml")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
MAX_BYTES = 5 * 1024 * 1024 * 1024
jobs = {}


def login(credentials: HTTPBasicCredentials = Depends(HTTPBasic())):
    if not APP_PASSWORD:
        raise HTTPException(503, "Set APP_PASSWORD in the server environment.")
    ok = secrets.compare_digest(credentials.username.encode(), APP_USERNAME.encode())
    ok &= secrets.compare_digest(credentials.password.encode(), APP_PASSWORD.encode())
    if not ok:
        raise HTTPException(401, "Incorrect username or password",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username


def public_base(request: Request):
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    host = request.headers.get("x-forwarded-host")
    if host:
        return request.headers.get("x-forwarded-proto", "https") + "://" + host
    return str(request.base_url).rstrip("/")


def patch_job(job_id, **updates):
    if job_id in jobs:
        jobs[job_id].update(updates)
        jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()


def trigger(inputs):
    if not GITHUB_TOKEN:
        raise HTTPException(503, "Set GITHUB_TOKEN on the Termux server.")
    endpoint = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/workflows/{GITHUB_WORKFLOW_FILE}/dispatches"
    body = json.dumps({"ref": "main", "inputs": inputs}).encode()
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "Accept": "application/vnd.github+json", "Authorization": "Bearer " + GITHUB_TOKEN,
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
        "User-Agent": "Video-MMSUB"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status not in (201, 204):
                raise HTTPException(502, "GitHub Workflow could not be started.")
    except urllib.error.HTTPError as exc:
        raise HTTPException(502, "GitHub API rejected the request: " +
                            exc.read().decode("utf-8", "replace")[:300])
    except urllib.error.URLError as exc:
        raise HTTPException(502, "Could not reach GitHub: " + str(exc))


PAGE = """<!doctype html><html lang="my"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Video MMSUB</title>
<style>body{font-family:system-ui,sans-serif;background:#101827;color:#eef2ff;margin:0}
main{max-width:720px;margin:6vh auto;padding:22px}.panel{background:#1c293d;padding:24px;border-radius:18px}
p{line-height:1.7;color:#cbd5e1}input,button{box-sizing:border-box;width:100%;padding:14px;margin:10px 0;border-radius:10px}
input{background:#fff;color:#111827;border:0}button{background:#38bdf8;color:#082f49;font-weight:700;border:0}button:disabled{opacity:.5}
a{color:#7dd3fc}#status{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>
<main><section class="panel"><h1>🎬 Video MMSUB</h1>
<p>Google Drive ထဲက Video URL ကို ထည့်ပြီး Generate နှိပ်ပါ။ Drive sharing ကို “Anyone with the link” ထားပါ။ GitHub က Video ကိုယူပြီး မြန်မာစာတန်းထိုးကာ ဒီ Website ကို ပြန်ပို့ပေးမယ်။</p>
<form id="form"><input id="driveUrl" type="url" placeholder="Google Drive video URL ထည့်ပါ" required>
<button id="generateBtn">မြန်မာစာတန်းထိုး Generate</button></form><p id="status"></p><p id="download"></p>
<p>မှတ်ချက် — Output ပြန်ပို့ရန် Website Tunnel အလုပ်လုပ်နေရပါမယ်။</p>
</section></main><script>
const form=document.getElementById('form'),url=document.getElementById('driveUrl'),gen=document.getElementById('generateBtn'),status=document.getElementById('status'),dl=document.getElementById('download');let job='';
form.onsubmit=async e=>{e.preventDefault();gen.disabled=true;status.textContent='GitHub Workflow စတင်နေပါတယ်…';dl.textContent='';try{let r=await fetch('/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({drive_url:url.value.trim()})}),j=await r.json();if(!r.ok)throw Error(j.detail||'Generate failed');job=j.job_id;poll();}catch(x){status.textContent=x.message;gen.disabled=false;}};
async function poll(){try{let r=await fetch('/status/'+job),j=await r.json();status.textContent=j.message+'\\nStatus: '+j.status;if(j.status==='completed'){let a=document.createElement('a');a.href=j.download_url;a.textContent='မြန်မာစာတန်းထိုး Video Download';dl.replaceChildren(a);gen.disabled=false;return;}if(j.status==='failed'){status.textContent+='\\n'+(j.error||'Workflow failed');gen.disabled=false;return;}setTimeout(poll,5000);}catch(e){status.textContent='Status စစ်မရပါ: '+e.message;gen.disabled=false;}}
</script></body></html>"""

app = FastAPI(title="Video MMSUB", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
def home(_: str = Depends(login)):
    return PAGE


@app.post("/generate")
async def generate(request: Request, _: str = Depends(login)):
    try:
        payload = await request.json()
        drive_url = str(payload.get("drive_url", "")).strip()
    except Exception:
        raise HTTPException(400, "Google Drive URL ထည့်ပါ။")
    parsed = urlparse(drive_url)
    if parsed.scheme != "https" or parsed.hostname not in {"drive.google.com", "www.drive.google.com", "docs.google.com"}:
        raise HTTPException(400, "Google Drive URL မှန်မှန်ထည့်ပါ။")
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    jobs[job_id] = {"job_id": job_id, "status": "queued", "progress": 0,
                    "message": "GitHub Workflow စတင်နေပါတယ်…", "created_at": now,
                    "updated_at": now, "callback_token": secrets.token_urlsafe(32),
                    "download_url": None}
    base = public_base(request).rstrip("/")
    inputs = {"job_id": job_id, "video_url": drive_url,
              "callback_url": f"{base}/callback/{job_id}",
              "callback_token": jobs[job_id]["callback_token"]}
    try:
        trigger(inputs)
    except Exception:
        patch_job(job_id, status="failed", message="Workflow မစတင်နိုင်ပါ။ GitHub Token/URL ကိုစစ်ပါ။")
        raise
    patch_job(job_id, status="processing", message="Google Drive က Video ကိုယူပြီး စာတန်းထိုးနေပါတယ်…")
    return {"job_id": job_id, "status": "processing"}


@app.get("/status/{job_id}")
def status(job_id: str, _: str = Depends(login)):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job မတွေ့ပါ။ Server restart ဖြစ်ထားနိုင်ပါတယ်။")
    return {k: v for k, v in job.items() if k not in {"source_token", "callback_token", "source_name"}}


@app.post("/callback/{job_id}/complete")
async def complete(job_id: str, file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    job = jobs.get(job_id)
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not job or not secrets.compare_digest(supplied, job["callback_token"]):
        raise HTTPException(403, "Callback token မမှန်ပါ။")
    folder = JOBS_DIR / job_id
    path = folder / "with-myanmar-subtitles.mp4"
    size = 0
    try:
        with path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Output 5 GiB ထက်ကြီးနေပါတယ်။")
                out.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    patch_job(job_id, status="completed", progress=100,
              message="ပြီးပါပြီ။ Download လုပ်နိုင်ပါပြီ။", download_url="/download/" + job_id)
    return {"status": "completed"}


@app.post("/callback/{job_id}/failed")
async def failed(job_id: str, request: Request, authorization: str | None = Header(default=None)):
    job = jobs.get(job_id)
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not job or not secrets.compare_digest(supplied, job["callback_token"]):
        raise HTTPException(403, "Callback token မမှန်ပါ။")
    try:
        error = str((await request.json()).get("error", "GitHub Workflow failed"))[:1000]
    except Exception:
        error = "GitHub Workflow failed"
    patch_job(job_id, status="failed", progress=100, message="Processing မအောင်မြင်ပါ။", error=error)
    return {"status": "failed"}


@app.get("/download/{job_id}")
def download(job_id: str, _: str = Depends(login)):
    job = jobs.get(job_id)
    path = JOBS_DIR / job_id / "with-myanmar-subtitles.mp4"
    if not job or job.get("status") != "completed" or not path.is_file():
        raise HTTPException(404, "Download ဖိုင် မရသေးပါ။")
    return FileResponse(path, media_type="video/mp4", filename=f"Video-MMSUB-{job_id[:8]}.mp4")
