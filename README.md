# Live Media Scan Producer

A Python producer for sending audio to the Live Media Scan (LMS) service via WebSocket.

## Prerequisites

- **Python 3.12 or later** — [python.org/downloads](https://www.python.org/downloads/)
- **uv** — fast Python package and project manager

Install `uv` if you don't already have it:

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**
```cmd
winget install astral-sh.uv
```

Then install all dependencies (including dev dependencies) from the lockfile:

```bash
uv sync
```

## Configuration

Create a `.env` file in the project root with your credentials:

```env
API_KEY=your-api-key
LMS_ENDPOINT=wss://lms.example.com/ws
```

`FILE_PATH` is optional — it defaults to `./audio.wav` if not set, and is typically overridden with `--file` on the command line:

```env
API_KEY=your-api-key
LMS_ENDPOINT=wss://lms.example.com/ws
FILE_PATH=./audio.wav
```

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | — | API key for authentication |
| `LMS_ENDPOINT` | Yes | — | Full WebSocket URL (e.g. `wss://lms.example.com/ws`) |
| `FILE_PATH` | No | `./audio.wav` | Path to the audio file to send |
| `ENABLE_RESULT_RETRIEVAL` | No | `true` | Fetch and display analysis results after the session (see below) |

Mime type, sample rate, and bitrate are CLI-only (`--mime-type`, `--rate`, `--bitrate`).

**Note:** The source filename sent in metadata is always the basename of `FILE_PATH`.

## Usage

Once your `.env` file is in place, run from the project root:

```bash
uv run python src/live_media_scan_producer
```

You can also invoke it as a module:

```bash
uv run python -m live_media_scan_producer
```

Override `FILE_PATH` for a single run using `--file` (or `-f`):

```bash
uv run python src/live_media_scan_producer --file ./audio.wav
```

Command-line arguments take precedence over `.env` values.

Ready-made example files live in [`samples/`](samples/) (regenerate with `./scripts/generate_samples.sh`).

### Example 1: Send a WAV file

```bash
uv run python src/live_media_scan_producer --file samples/sample.wav
```

### Example 2: Send audio/basic (raw μ-law)

```bash
uv run python src/live_media_scan_producer -f samples/sample.ulaw
```

If your raw μ-law audio happens to be in a WAV container, the WAV header is automatically stripped before sending.

### Example 3: Send headerless LPCM

LPCM requires a sample rate (`rate=`). Use `--rate` or include it in `--mime-type`:

```bash
uv run python src/live_media_scan_producer -f samples/sample.pcm --rate 8000
uv run python src/live_media_scan_producer -f samples/sample.pcm --mime-type 'audio/L16;rate=8000'
```

### Example 4: Send compressed audio (e.g. MP3)

```bash
uv run python src/live_media_scan_producer -f samples/sample.mp3
uv run python src/live_media_scan_producer -f samples/sample.mp3 --bitrate 192000
```

### Example 5: A-law / μ-law in a WAV container

Headerless μ-law uses `audio/basic`. A-law is sent via WAV (`audio/wav`); LMS does not advertise a headerless A-law MIME type.

```bash
uv run python src/live_media_scan_producer -f samples/sample_alaw.wav
uv run python src/live_media_scan_producer -f samples/sample_ulaw.wav
uv run python src/live_media_scan_producer -f samples/sample.ulaw
```

The producer checks the chosen MIME type against the server hello `allowed_media` list and fails early if it is not advertised.

## How It Works

The producer streams audio to the LMS service in real time over a WebSocket connection. Audio is paced to match the file's native bitrate so the server receives data at the same rate it would arrive from a live call.

Once the LMS service has received enough audio to reach a conclusion, it sends an `analysis_complete` notice over the WebSocket. The producer stops transmitting immediately upon receiving this notice, sends a stop request, and closes the session.

By default, the producer then queries the Session API to retrieve and display the analysis results. This can be disabled by setting `ENABLE_RESULT_RETRIEVAL=false` in `.env` or in the environment:

```bash
ENABLE_RESULT_RETRIEVAL=false uv run python src/live_media_scan_producer
```

## Example Output

```
$ uv run python src/live_media_scan_producer
2026-05-09 20:52:49 INFO WebSocket connected: wss://lms.example.com/ws
2026-05-09 20:52:49 INFO Server sent hello
2026-05-09 20:52:49 INFO Resolved media type: audio/wav (from file extension)
2026-05-09 20:52:49 INFO WAV file properties: sample_rate=8000, channels=1, sample_width=2, calculated_bitrate=128000
2026-05-09 20:52:49 INFO Sending start request
2026-05-09 20:52:49 INFO Beginning streaming audio...
2026-05-09 20:52:57 INFO Analysis complete received; stopping media transmission.
2026-05-09 20:52:57 INFO Finished streaming audio
2026-05-09 20:52:57 INFO Server approved stop of stream: stream_id=..., total_bytes=117760
2026-05-09 20:52:57 INFO Session results: session_id=..., stream_id=..., conclusion=AUTHENTIC, probability=0.00
```

In this example the LMS service reached a conclusion after ~7.5 seconds of audio. The `conclusion` field will be `AUTHENTIC`, `ARTIFICIAL`, or `INCONCLUSIVE`.

## Media Types

MIME type is detected from the file extension unless overridden with `--mime-type` / `-m`:

| Extension | media_type (wire) |
|---|---|
| `.wav` | `audio/wav` |
| `.ulaw` | `audio/basic` |
| `.l16`, `.pcm`, `.s16le`, `.raw` | `audio/L16` (requires `--rate`) |
| `.mp3` | `audio/mpeg` |
| `.aac` | `audio/aac` |
| `.ogg` | `audio/ogg` |
| `.opus` | `audio/opus` |
| `.flac` | `audio/flac` |
| `.amr` | `audio/amr` |
| `.m4a` | `audio/x-m4a` |
| `.mp4` | `audio/mp4` |

Unknown extensions require `--mime-type`.

### Bitrate defaults

| Family | Default bitrate |
|---|---|
| G.711 (`audio/basic`) | 64000 |
| LPCM (`audio/L16`, …) | `rate * 16` (mono s16le) |
| `audio/wav` | calculated from WAV header |
| Compressed (mp3/aac/ogg/…) | 128000 |

Override with `--bitrate` when needed (required by LMS for compressed types if you want a non-default pacing rate).

### Streaming notes

- **`audio/wav`**: sends the complete WAV file including header; LMS parses format from the header.
- **Raw G.711 / LPCM**: sends payload bytes only; if the file starts with a RIFF/WAV header, that header is stripped.
- **Compressed**: sends container bytes as-is for LMS/ffmpeg decode.

## Creating Audio Files

Regenerate the checked-in `samples/` set from a source WAV (defaults to `./audio.wav`):

```bash
./scripts/generate_samples.sh
./scripts/generate_samples.sh path/to/source.wav
```

Or with sox:

### Create μ-law WAV file

```bash
sox input.wav -r 8000 -c 1 -b 8 -e mu-law output_ulaw.wav
```

### Create raw μ-law file for audio/basic

```bash
# From WAV file (extracts raw data, no header)
sox input_ulaw.wav -t raw output.ulaw

# Or convert directly from PCM
sox input.wav -r 8000 -c 1 -b 8 -e mu-law -t raw output.ulaw
```

### Create headerless LPCM (s16le)

```bash
sox input.wav -r 8000 -c 1 -e signed -b 16 -t raw output.pcm
```
