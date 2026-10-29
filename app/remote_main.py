import json
import os
import secrets
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

APP_USERNAME = os.getenv("APP_USERNAME", "owner")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "devmyatmin/Video-MMSUB")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_WORKFLOW_FILE = os.getenv("GITHUB_WORKFLOW_FILE", "process-video.yml")
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


def patch_job(job_id, **updates):
    if job_id in jobs:
        jobs[job_id].update(updates)
        jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()


def github_request(url, method="GET", payload=None):
    if not GITHUB_TOKEN:
        raise HTTPException(503, "Set GITHUB_TOKEN on the Termux server.")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + GITHUB_TOKEN,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "Video-MMSUB"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 404, b""
        raise HTTPException(502, "GitHub API error: " +
                            exc.read().decode("utf-8", "replace")[:300])
    except urllib.error.URLError as exc:
        raise HTTPException(502, "Could not reach GitHub: " + str(exc))


def trigger(inputs):
    endpoint = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/workflows/{GITHUB_WORKFLOW_FILE}/dispatches"
    status, _ = github_request(endpoint, method="POST", payload={"ref": "main", "inputs": inputs})
    if status not in (201, 204):
        raise HTTPException(502, "GitHub Workflow could not be started.")


PAGE = """<!doctype html><html lang="my"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Video MMSUB</title>
<style>body{font-family:system-ui,sans-serif;background:#101827;color:#eef2ff;margin:0}
main{max-width:720px;margin:6vh auto;padding:22px}.panel{background:#1c293d;padding:24px;border-radius:18px}
p{line-height:1.7;color:#cbd5e1}input,button{box-sizing:border-box;width:100%;padding:14px;margin:10px 0;border-radius:10px}
input{background:#fff;color:#111827;border:0}button,.download{background:#38bdf8;color:#082f49;font-weight:700;border:0;padding:14px;border-radius:10px;display:block;text-align:center;text-decoration:none;margin:10px 0}
button:disabled{opacity:.5}#status{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body>
<main><section class="panel"><h1>🎬 Video MMSUB</h1>
<p>Video URL သို့မဟုတ် ပံ့ပိုးထားသော Website ရဲ့ video link ကို ထည့်ပါ။ GitHub က video ကို တိုက်ရိုက်ဆွဲယူပြီး မြန်မာစာတန်းထိုးကာ MP4 download link ကို ဒီ Website မှာ ပြပေးမယ်။</p>
<form id="form"><input id="videoUrl" type="url" placeholder="Video URL / One-click link ထည့်ပါ" required>
<button id="generateBtn">မြန်မာစာတန်းထိုး Generate</button></form><p id="status"></p><p id="download"></p>
<p>ပြီးသွားရင် GitHub ပေါ်က MP4 ကို တိုက်ရိုက် Download လုပ်နိုင်ပါတယ်။ ဖုန်းဆီ ဖိုင်ပြန်ပို့ရန် မလိုပါ။</p>
</section></main><script>
const form=document.getElementById('form'),url=document.getElementById('videoUrl'),gen=document.getElementById('generateBtn'),status=document.getElementById('status'),dl=document.getElementById('download');let job='';
form.onsubmit=async e=>{e.preventDefault();gen.disabled=true;status.textContent='GitHub Workflow စတင်နေပါတယ်…';dl.replaceChildren();try{let r=await fetch('/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_url:url.value.trim()})}),j=await r.json();if(!r.ok)throw Error(j.detail||'Generate failed');job=j.job_id;poll();}catch(x){status.textContent=x.message;gen.disabled=false;}};
async function poll(){try{let r=await fetch('/status/'+job),j=await r.json();status.textContent=j.message+'\\nStatus: '+j.status;if(j.status==='completed'){let a=document.createElement('a');a.href=j.download_url;a.textContent='⬇ One-click Download MP4';a.className='download';a.target='_blank';a.rel='noopener';dl.replaceChildren(a);gen.disabled=false;return;}if(j.status==='failed'){status.textContent+='\\n'+(j.error||'Workflow failed');gen.disabled=false;return;}setTimeout(poll,8000);}catch(e){status.textContent='Status စစ်မရပါ: '+e.message;setTimeout(poll,10000);}}
</script></body></html>"""

app = FastAPI(title="Video MMSUB", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
def home(_: str = Depends(login)):
    return PAGE


@app.post("/generate")
async def generate(request: Request, _: str = Depends(login)):
    try:
        payload = await request.json()
        video_url = str(payload.get("video_url", payload.get("drive_url", ""))).strip()
    except Exception:
        raise HTTPException(400, "Video URL ထည့်ပါ။")
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "မှန်ကန်တဲ့ http/https Video URL ထည့်ပါ။")
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    jobs[job_id] = {"job_id": job_id, "status": "queued", "progress": 0,
                    "message": "GitHub Workflow စတင်နေပါတယ်…", "created_at": now,
                    "updated_at": now, "download_url": None, "release_tag": "mmsub-" + job_id}
    try:
        trigger({"job_id": job_id, "video_url": video_url})
    except Exception:
        patch_job(job_id, status="failed", message="Workflow မစတင်နိုင်ပါ။ GitHub Token/URL ကိုစစ်ပါ။")
        raise
    patch_job(job_id, status="processing", message="GitHub က video ကိုဆွဲယူပြီး မြန်မာစာတန်းထိုးနေပါတယ်…")
    return {"job_id": job_id, "status": "processing"}


@app.get("/status/{job_id}")
def status(job_id: str, _: str = Depends(login)):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job မတွေ့ပါ။ Server restart ဖြစ်ထားနိုင်ပါတယ်။")
    if job.get("status") not in {"completed", "failed"}:
        tag = "mmsub-" + job_id
        url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/tags/{tag}"
        code, raw = github_request(url)
        if code == 200:
            release = json.loads(raw.decode("utf-8"))
            assets = release.get("assets", [])
            mp4 = next((a for a in assets if a.get("name") == "output.mp4"), None)
            if mp4:
                patch_job(job_id, status="completed", progress=100,
                          message="ပြီးပါပြီ။ GitHub က MP4 ကို တိုက်ရိုက် Download လုပ်နိုင်ပါပြီ။",
                          download_url=mp4.get("browser_download_url"))
            elif "PROCESSING_FAILED" in (release.get("body") or ""):
                patch_job(job_id, status="failed", progress=100,
                          message="Processing မအောင်မြင်ပါ။ GitHub Actions logs ကို စစ်ပါ။",
                          error="GitHub Actions workflow failed. Check the Actions run logs.")
    return {k: v for k, v in job.items() if k not in {"callback_token", "source_token"}}
