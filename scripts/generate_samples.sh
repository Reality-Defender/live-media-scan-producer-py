#!/usr/bin/env bash
# Generate LMS producer sample audio files from a source WAV using ffmpeg.
#
# Usage:
#   ./scripts/generate_samples.sh [source.wav]
#
# Defaults to ./audio.wav in the repo root.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="${1:-$ROOT/audio.wav}"
OUT="$ROOT/samples"

if [[ ! -f "$SOURCE" ]]; then
  echo "ERROR: source file not found: $SOURCE" >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ERROR: ffmpeg is required" >&2
  exit 1
fi

mkdir -p "$OUT"

echo "Source: $SOURCE"
echo "Output: $OUT"

# WAV (PCM s16le) — canonical container sample
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -c:a pcm_s16le "$OUT/sample.wav"
# WAV with μ-law payload (still audio/wav on the wire)
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -c:a pcm_mulaw "$OUT/sample_ulaw.wav"

# Headerless G.711
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -f mulaw -ar 8000 -ac 1 "$OUT/sample.ulaw"
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -f alaw -ar 8000 -ac 1 "$OUT/sample.alaw"

# Headerless LPCM s16le (use with --rate 8000)
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -f s16le -ar 8000 -ac 1 "$OUT/sample.pcm"

# Compressed / ffmpeg-decode path
# AAC/M4A/MP4: upsample to 44.1 kHz so bitrate matches typical consumer AAC
# (8 kHz AAC tops out ~32 kb/s and often fails in Apple Music).
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -c:a libmp3lame -b:a 128k "$OUT/sample.mp3"
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -ar 44100 -c:a aac -b:a 128k -f adts "$OUT/sample.aac"
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -c:a flac "$OUT/sample.flac"
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -c:a libopus -b:a 64k "$OUT/sample.opus"
# +faststart puts moov before mdat so ffmpeg can demux from a non-seekable
# pipe (stream_worker's decode path). Default layout fails with 0 PCM on stdin.
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -ar 44100 -c:a aac -b:a 128k -movflags +faststart "$OUT/sample.m4a"
ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -ar 44100 -c:a aac -b:a 128k -movflags +faststart "$OUT/sample.mp4"

# Ogg container: prefer Vorbis (stereo — native vorbis is mono-hostile), else Opus-in-Ogg
if ffmpeg -hide_banner -encoders 2>&1 | grep -q 'libvorbis'; then
  ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -ac 2 -c:a libvorbis -b:a 64k "$OUT/sample.ogg"
elif ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -ac 2 -c:a vorbis -strict experimental "$OUT/sample.ogg" 2>/dev/null; then
  :
else
  echo "WARNING: Vorbis unavailable; writing Opus-in-Ogg as sample.ogg" >&2
  ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -c:a libopus -b:a 32k "$OUT/sample.ogg"
fi

# AMR-NB when an encoder is available
if ffmpeg -hide_banner -encoders 2>&1 | grep -Eq 'libopencore_amrnb|libvo_amrwbenc|amr_nb'; then
  ffmpeg -y -hide_banner -loglevel error -i "$SOURCE" -ar 8000 -ac 1 -c:a libopencore_amrnb "$OUT/sample.amr" \
    || echo "WARNING: AMR encode failed; skipping sample.amr" >&2
else
  echo "WARNING: no AMR encoder in this ffmpeg build; skipping sample.amr" >&2
fi

echo
echo "Generated samples:"
ls -lh "$OUT"
echo
echo "Example producer commands:"
cat <<'EOF'
  uv run python src/live_media_scan_producer -f samples/sample.wav
  uv run python src/live_media_scan_producer -f samples/sample.ulaw
  uv run python src/live_media_scan_producer -f samples/sample.alaw --mime-type audio/pcma
  uv run python src/live_media_scan_producer -f samples/sample.pcm --rate 8000
  uv run python src/live_media_scan_producer -f samples/sample.mp3
  uv run python src/live_media_scan_producer -f samples/sample.aac
  uv run python src/live_media_scan_producer -f samples/sample.ogg
  uv run python src/live_media_scan_producer -f samples/sample.opus
  uv run python src/live_media_scan_producer -f samples/sample.flac
  uv run python src/live_media_scan_producer -f samples/sample.m4a
  uv run python src/live_media_scan_producer -f samples/sample.mp4
  # or embed rate in the mime type:
  uv run python src/live_media_scan_producer -f samples/sample.pcm --mime-type 'audio/L16;rate=8000'
EOF
