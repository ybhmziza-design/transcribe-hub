#!/usr/bin/env bash
# Startup script for TranscribeHub
cd "$(dirname "$0")"
PORT="${PORT:-5000}"
echo "🚀 Starting TranscribeHub Video Task Platform on port $PORT..."
python3 app.py
