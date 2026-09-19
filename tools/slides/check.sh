#!/usr/bin/env bash
# Reports every RevealJS slide whose content is taller than the deck frame.
#
#   tools/slides/check.sh                    # every experiment's deck
#   tools/slides/check.sh S1-daq-static ...  # only these
#
# The decks are authored at 1024x768 (4:3, the ratio the lecture rooms
# project at). A slide taller than USABLE_PX overflows the frame in
# full-screen presentation and has to be split.
#
# Why a measurement and not a screenshot: obscura cannot finish Reveal.js's
# own initialisation (its math plugin throws in that engine), so the deck
# never paints. The script instead imposes the same 1024x768 geometry Reveal
# would impose and measures each slide's content height inside it. Reveal's
# viewport fitting is a pure CSS transform, which does not change layout, so
# this measures exactly what the audience sees.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PORT="${PORT:-8931}"
USABLE_PX="${USABLE_PX:-720}"    # 768 less the footer and the slide number

cd "$ROOT"
[ -d _site ] || { echo "no _site: run 'quarto render' first" >&2; exit 1; }

if ! curl -sf "http://localhost:$PORT/" >/dev/null 2>&1; then
  python3 -m http.server "$PORT" --directory _site >/dev/null 2>&1 &
  SERVER_PID=$!
  trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
  sleep 2
fi

if [ $# -gt 0 ]; then
  DECKS=("$@")
else
  DECKS=()
  for d in _site/experiments/*/slides.html; do
    DECKS+=("$(basename "$(dirname "$d")")")
  done
fi

EVAL="$(cat "$ROOT/tools/slides/overflow.js")"
STATUS=0
for deck in "${DECKS[@]}"; do
  json="$(obscura fetch --allow-private-network --wait 8 --eval "$EVAL" \
            "http://localhost:$PORT/experiments/$deck/slides.html" 2>/dev/null \
          | tail -1)"
  if ! echo "$json" | USABLE="$USABLE_PX" DECK="$deck" \
       python3 "$ROOT/tools/slides/report.py"; then STATUS=1; fi
done
exit $STATUS
