# Video MMSUB

Password-protected web app that accepts a video, transcribes its audio with Faster-Whisper, translates detected speech to Burmese, burns Burmese subtitles into an MP4, and offers the result for download.

## Features

- HTTP Basic authentication protects the web UI, upload, status, and download endpoints.
- Audio transcription using Faster-Whisper.
- Automatic translation to Burmese using Google Translate via deep-translator.
- Hard-subtitled MP4 output using FFmpeg.
- Docker deployment with a persistent data volume.
- No app-defined upload-size or video-duration limit.

## Deploy on a server

This repository contains the app and CI workflow; it does **not** automatically provision a public server. Deploy the Docker image to a server/host that supports persistent storage and long-running CPU workloads.

1. Install Docker Engine and Docker Compose on the server.
2. Copy this repository to the server.
3. Copy .env.example to .env and set a strong unique APP_PASSWORD.
4. Run docker compose up -d --build.
5. Open http://SERVER-HOST:8000 and sign in with APP_USERNAME / APP_PASSWORD.

For internet-facing use, put it behind HTTPS (for example, Caddy or a managed reverse proxy). Do not expose the app over plain HTTP on the public internet because HTTP Basic credentials are not encrypted without TLS.

## Model and resource sizing

The first transcription downloads the selected Whisper model and can take time. Set WHISPER_MODEL to tiny, base, small, medium, or large-v3 depending on available RAM and CPU. Larger models may improve transcription but use more resources. The default is small, CPU, int8.

## Important limits and current implementation notes

- There is no hard upload-size or duration limit in this app code. **Unlimited in practice is impossible to guarantee**: disk capacity, RAM, CPU, request timeouts, network/proxy limits, and the host's policies still apply. Large videos need a server with sufficient persistent disk and memory.
- The job queue and status records are currently in memory. If the app process restarts, active jobs/status records are lost; use a persistent queue/database before relying on it for production workloads.
- Processing is CPU-intensive. This starter app handles one server process; for multiple simultaneous users, move jobs to a worker queue and add per-user quotas/cleanup.
- Translation uses an unofficial Google Translate wrapper and can be rate-limited or change without notice.
- Translation quality and subtitle timing vary with audio quality, background music, accents, and overlapping speech.
- Uploads and generated files remain on the server volume until manually cleaned up. Protect and back up the server appropriately.
- Only upload content you own or have permission to process.

## GitHub Actions

The CI workflow installs Python dependencies, compiles the app, and builds the Docker image. It does not host the app or store uploaded videos.
