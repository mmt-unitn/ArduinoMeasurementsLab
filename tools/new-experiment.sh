#!/usr/bin/env bash
# Creates a new experiment folder from experiments/_template.
#
#   tools/new-experiment.sh S4 daq-noise "Noise of the whole chain" "DAQ, noise"
#
# Arguments: the experiment id (S4, T4, M4...), the folder suffix, the title,
# and optionally a comma-separated category list. The folder is named
# <id>-<suffix>, lower-cased, which is what the sidebar and the listing expect.
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "usage: $0 <id> <folder-suffix> <title> [categories]" >&2
  echo "   e.g. $0 S4 daq-noise 'Noise of the whole chain' 'DAQ, noise'" >&2
  exit 2
fi

id="$1"
suffix="$2"
title="$3"
categories="${4:-TODO}"

root="$(cd "$(dirname "$0")/.." && pwd)"
template="$root/experiments/_template"
idl="$(echo "$id" | tr '[:upper:]' '[:lower:]')"
target="$root/experiments/${id}-${suffix}"

if [ ! -d "$template" ]; then
  echo "template not found: $template" >&2
  exit 1
fi
if [ -e "$target" ]; then
  echo "already exists: $target" >&2
  exit 1
fi

cp -R "$template" "$target"
rm -f "$target/README.md"

# Substitute the placeholders. BSD and GNU sed disagree about -i, so the file
# is rewritten rather than edited in place.
for file in "$target"/*.qmd; do
  tmp="$file.tmp"
  sed -e "s|{{ID}}|$id|g" \
      -e "s|{{IDL}}|$idl|g" \
      -e "s|{{TITLE}}|$title|g" \
      -e "s|{{CATEGORIES}}|$categories|g" "$file" > "$tmp"
  mv "$tmp" "$file"
done

echo "created $target"
echo
echo "Next:"
echo "  1. add it to the experiments sidebar in _quarto.yml"
echo "  2. write tools/synth/${idl}_*.py and tools/synth/params/${idl}.yaml,"
echo "     and register the generator in tools/synth/generate.py"
echo "  3. read experiments/_template/README.md for the conventions"
