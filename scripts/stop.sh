#!/usr/bin/env bash
kill "$(cat /tmp/wikimania_backend.pid 2>/dev/null)" 2>/dev/null && echo "Backend stopped" || echo "Backend not running"
kill "$(cat /tmp/wikimania_frontend.pid 2>/dev/null)" 2>/dev/null && echo "Frontend stopped" || echo "Frontend not running"
rm -f /tmp/wikimania_backend.pid /tmp/wikimania_frontend.pid
