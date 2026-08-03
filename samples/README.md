# Sample audio files

Generated from the repo-root `audio.wav` (30s, 8 kHz mono) via:

```bash
./scripts/generate_samples.sh
```

| File | Detected / intended mime | Notes |
|---|---|---|
| `sample.wav` | `audio/wav` | Linear PCM WAV |
| `sample_ulaw.wav` | `audio/wav` | μ-law in a WAV container |
| `sample.ulaw` | `audio/basic` | Headerless G.711 μ-law |
| `sample.alaw` | `audio/pcma` | Headerless G.711 A-law (may need hello to advertise `audio/pcma`) |
| `sample.pcm` | `audio/L16` | Headerless s16le — requires `--rate 8000` |
| `sample.mp3` | `audio/mpeg` | ~128 kb/s |
| `sample.aac` | `audio/aac` | ADTS, 44.1 kHz / 128 kb/s |
| `sample.ogg` | `audio/ogg` | Vorbis in Ogg |
| `sample.opus` | `audio/opus` | |
| `sample.flac` | `audio/flac` | |
| `sample.m4a` | `audio/x-m4a` | AAC, 44.1 kHz / 128 kb/s (`+faststart` for pipe decode) |
| `sample.mp4` | `audio/mp4` | AAC in MP4, 44.1 kHz / 128 kb/s (`+faststart` for pipe decode) |

`sample.amr` is only produced when ffmpeg has an AMR encoder.

## Example commands

```bash
uv run python src/live_media_scan_producer -f samples/sample.wav
uv run python src/live_media_scan_producer -f samples/sample.ulaw
uv run python src/live_media_scan_producer -f samples/sample.pcm --rate 8000
uv run python src/live_media_scan_producer -f samples/sample.pcm --mime-type 'audio/L16;rate=8000'
uv run python src/live_media_scan_producer -f samples/sample.mp3
```
