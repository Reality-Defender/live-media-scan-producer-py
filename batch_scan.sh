#!/usr/bin/env bash
#
# batch_scan.sh — Run live_media_scan_producer on every WAV file in a directory
# and write results to a CSV file.
#
# Usage:
#   ./batch_scan.sh <audio-dir> [output.csv]
#
# Example:
#   ./batch_scan.sh ./samples results.csv
#
set -uo pipefail

AUDIO_DIR="${1:?Usage: $0 <audio-dir> [output.csv]}"
OUTPUT_CSV="${2:-results.csv}"

# Verify the directory exists
if [ ! -d "$AUDIO_DIR" ]; then
    echo "ERROR: directory not found: $AUDIO_DIR" >&2
    exit 1
fi

echo "filename,session_id,stream_id,conclusion,probability" > "$OUTPUT_CSV"

processed=0
errors=0

for wav_file in "$AUDIO_DIR"/*.wav; do
    [ -f "$wav_file" ] || { echo "No WAV files found in $AUDIO_DIR" >&2; break; }

    base=$(basename "$wav_file")
    echo "Processing: $base" >&2

    # Capture all output (logging writes to stderr)
    if output=$(uv run python src/live_media_scan_producer --file "$wav_file" 2>&1); then
        result_line=$(echo "$output" | grep " INFO Session results:" || true)
    else
        echo "  ERROR: producer failed for $base" >&2
        echo "$output" | sed 's/^/  /' >&2
        echo "$base,,,," >> "$OUTPUT_CSV"
        ((errors++)) || true
        continue
    fi

    if [ -z "$result_line" ]; then
        echo "  WARNING: no session results returned for $base" >&2
        echo "$base,,,," >> "$OUTPUT_CSV"
        ((errors++)) || true
        continue
    fi

    session_id=$(echo  "$result_line" | sed 's/.*session_id=\([^,]*\).*/\1/')
    stream_id=$(echo   "$result_line" | sed 's/.*stream_id=\([^,]*\).*/\1/')
    conclusion=$(echo  "$result_line" | sed 's/.*conclusion=\([^,]*\).*/\1/')
    probability=$(echo "$result_line" | sed 's/.*probability=\([^ ]*\).*/\1/')

    echo "$base,$session_id,$stream_id,$conclusion,$probability" >> "$OUTPUT_CSV"
    echo "  $conclusion  (probability=$probability)" >&2
    ((processed++)) || true
done

echo "" >&2
echo "Done: $processed processed, $errors errors — results written to $OUTPUT_CSV" >&2
