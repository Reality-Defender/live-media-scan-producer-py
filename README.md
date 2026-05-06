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

`FILE_PATH` is optional — it defaults to `./audio.wav` if not set, and is typically overridden with --file on the command line:

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

**Note:** Media type is automatically detected from the file extension:
- Files ending in `.wav` -> `audio/wav`
- All other files -> `audio/basic` (raw u-law)

**Note:** The source filename sent in metadata is always the basename of `FILE_PATH`.

## Usage

Once your `.env` file is in place, run:

```bash
uv run python -m live_media_scan_producer.main
```

You can override `FILE_PATH` for a single run using `--file` (or `-f`):

```bash
uv run python -m live_media_scan_producer.main --file ./audio.wav
```

Command-line arguments take precedence over `.env` values.

### Example 1: Send a WAV file

```bash
uv run python -m live_media_scan_producer.main --file ./audio.wav
```

### Example 2: Send audio/basic (raw u-law)

Use any non-`.wav` extension and the producer automatically sends `audio/basic`:

```bash
uv run python -m live_media_scan_producer.main -f ./audio.ulaw
```

If your raw u-law audio happens to be in a WAV container, the WAV header is automatically stripped before sending.

## Media Types

### `audio/wav`
- Sends the complete WAV file including header
- LMS parses the WAV header to detect format (PCM, μ-law, A-law)
- Supports Linear PCM and G.711 μ-law/A-law WAV files
- Bitrate is calculated from WAV file properties

### `audio/basic`
- Sends raw G.711 μ-law audio data (no WAV header)
- Fixed bitrate: 64 kbps (8000 bytes/sec)
- If input is a WAV file, the header is automatically stripped
- Can also use raw `.ulaw` files created with sox

## Creating Audio Files

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

## Supported Audio Formats

- **Linear PCM** (WAV format 1): 16-bit, 8000 Hz, mono
- **G.711 μ-law** (WAV format 7): 8-bit, 8000 Hz, mono
- **G.711 A-law** (WAV format 6): 8-bit, 8000 Hz, mono
