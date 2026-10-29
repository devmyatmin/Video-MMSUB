# Video MMSUB

Password-protected web app hosted on your phone/Termux. Paste a direct `.mp4` video URL copied from 1DM. Choose one of the two modes: transcribe speech into source-language SRT, or burn a supplied SRT into the MP4.

## How it works

1. Termux runs the web app; expose it through a public HTTPS tunnel while you use it.
2. The workflow accepts direct `.mp4` URLs only, up to 10 GB, subject to runner disk space, network speed, and source URL access.
3. Press Generate. The server dispatches `.github/workflows/process-video.yml` using a GitHub token stored only in the server environment.
4. GitHub Actions downloads the MP4, processes it, then publishes the SRT or subtitled MP4 as a GitHub Release asset.
5. The web page polls for completion and displays a direct download link to the asset.

The workflow is separate from `.github/workflows/ci.yml`; CI only validates the repository.

## GitHub token setup

Create a fine-grained personal access token for this repository with **Actions: Read and write** and **Contents: Read-only** permissions. Actions permission lets the server dispatch the workflow; Contents read lets the app check whether the output release asset is ready. Do not paste it into browser JavaScript, commit it to Git, or share it with friends. If it is exposed, revoke it and create a new one.

## Android Termux setup

Install Termux from a trusted source such as F-Droid or the official Termux GitHub releases. In Termux:

```sh
pkg update && pkg upgrade -y
pkg install python git nano -y
termux-setup-storage
git clone https://github.com/devmyatmin/Video-MMSUB.git
cd Video-MMSUB
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-termux.txt
cp .env.example .env
nano .env
```

Edit `.env` and set:
- `APP_USERNAME` and a strong `APP_PASSWORD`
- `GITHUB_TOKEN` to the fine-grained token above
- `PUBLIC_BASE_URL` to the public HTTPS tunnel URL for this phone

Load the settings and start the app:

```sh
set -a
. ./.env
set +a
uvicorn app.remote_main:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000` on the phone to test it. The public URL must be an HTTPS URL that can reach port 8000 through your tunnel. Keep Termux and the tunnel running while GitHub downloads the source and uploads the result. If the public tunnel URL changes, update `PUBLIC_BASE_URL`, reload the environment, and restart the app.

## Public access and 30-minute use

A tunnel such as Cloudflare Tunnel can expose the local port; the app itself does not create a public URL. A stable custom tunnel URL generally requires configuring the tunnel provider. A temporary tunnel URL can change between sessions.

If you use a 30-minute auto-stop timer, the phone server must remain online until GitHub has downloaded the source and returned the finished MP4. A job still running when the phone server stops will fail to send its output back. Android battery optimization can also kill Termux; keep the phone charging and allow background activity.

## Limits and security

- The workflow rejects input videos larger than 10 GB (decimal). This is a configured cap, not a guarantee that every 10 GB video can be processed: GitHub-hosted runner disk space, network speed, processing time, Actions usage limits, and source URL access can impose lower practical limits. GitHub Releases has per-file asset limits, so very large MP4 outputs may not publish successfully.
- The video URL must point directly to a file whose URL path ends in `.mp4`. Links that require special headers or authentication may fail. Only process videos you have permission to download.
- The phone needs enough free storage for the original and output video. Output files remain on the phone until manually deleted.
- Job status is kept in memory. Restarting the app loses the page's job status even though files may remain on disk.
- The one-time source URL and per-job callback token are secrets. The website password protects the user interface; the GitHub token must stay on the server.
- HTTP Basic credentials are not encrypted over plain HTTP. Use HTTPS for public access.
- Only upload content you have permission to process.
