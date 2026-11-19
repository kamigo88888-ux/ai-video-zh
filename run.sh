#!/usr/bin/env bash
# 啟動 AI 電商影片自動中文化系統： ./run.sh  然後開啟 http://localhost:8000
cd "$(dirname "$0")"
exec python3 -m uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
