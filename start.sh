#!/bin/bash
echo "Starting Backend..."
cd backend && uv run uvicorn main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "Starting Frontend..."
cd .. && npm run start &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID" EXIT
wait
