# Video MMSUB

Password-protected web app hosted on your phone/Termux. Upload a video, press Generate, and GitHub Actions downloads the video through a one-time URL, transcribes and translates speech to Burmese, burns subtitles into an MP4, then uploads the result back to the phone server.

## How it works

1. Termux runs the web app; expose it through a public HTTPS tunnel while you use it.
2. The app accepts a video (up to 5 GiB), stores it on the phone, and returns a job ID.
3. Press Generate. The server dispatches `.github/workflows/process-video.yml` using a GitHub token stored only in the server environment.
4. GitHub Actions downloads the one-time video URL, processes the video, and POSTs the output back to the phone's public callback URL.
5. The web page shows a download link when processing completes.

The workflow is separate from `.github/workflows/ci.yml`; CI only validates the repository.

## GitHub token setup

Create a fine-grained personal access token for this repository with **Actions: Read and write** permission. The token is used by the server to dispatch the workflow. Do not paste it into browser JavaScript, commit it to Git, or share it with friends. If it is exposed, revoke it and create a new one.

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

- The app accepts at most 5 GiB per input and output. GitHub-hosted runner disk space, network speed, processing time, Actions usage limits, and the phone's free storage may impose lower practical limits.
- The phone needs enough free storage for the original and output video. Output files remain on the phone until manually deleted.
- Job status is kept in memory. Restarting the app loses the page's job status even though files may remain on disk.
- The one-time source URL and per-job callback token are secrets. The website password protects the user interface; the GitHub token must stay on the server.
- HTTP Basic credentials are not encrypted over plain HTTP. Use HTTPS for public access.
- Only upload content you have permission to process.
