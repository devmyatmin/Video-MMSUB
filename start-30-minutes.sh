#!/usr/bin/env bash
set -euo pipefail

# Run from the directory containing docker-compose.yml.
# The 30-minute timer starts after the service has started.
docker compose up -d --build
echo "Video MMSUB is running. It will stop automatically in 30 minutes."
sleep 1800
docker compose stop video-mmsub
echo "Video MMSUB has been stopped. Run this script again when you need it."
