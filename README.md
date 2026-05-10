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

**Note:** Media type is automatically detected from the file extension:
- Files ending in `.wav` -> `audio/wav`
- All other files -> `audio/basic` (raw u-law)

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

### Example 1: Send a WAV file

```bash
uv run python src/live_media_scan_producer --file ./audio.wav
```

### Example 2: Send audio/basic (raw u-law)

Use any non-`.wav` extension and the producer automatically sends `audio/basic`:

```bash
uv run python src/live_media_scan_producer -f ./audio.ulaw
```

If your raw u-law audio happens to be in a WAV container, the WAV header is automatically stripped before sending.

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
2026-05-09 20:52:49 INFO Server sent hello:
{ ... }
2026-05-09 20:52:49 INFO Detected media type: audio/wav (from file extension)
2026-05-09 20:52:49 INFO WAV file properties: sample_rate=8000, channels=1, sample_width=2, calculated_bitrate=128000
2026-05-09 20:52:49 INFO Sending start request:
{ ... }
2026-05-09 20:52:49 INFO Received message:
{ ... }
2026-05-09 20:52:49 INFO Beginning streaming audio...
2026-05-09 20:52:57 INFO Analysis complete received; stopping media transmission.
{
  "stream_id": "3DVjwW2XFqB9WIxmMCRqscCLB2E",
  "type": "notice",
  "subtype": "analysis_complete"
}
2026-05-09 20:52:57 INFO Finished streaming audio
2026-05-09 20:52:57 INFO Server approved stop of stream:
{
  "stream_id": "3DVjwW2XFqB9WIxmMCRqscCLB2E",
  "total_bytes": 117760,
  "stream_start": "2026-05-10T00:52:49.646029031Z",
  "stream_stop": "2026-05-10T00:52:57.309626523Z"
}
2026-05-09 20:52:57 INFO Session results:
{
  "call_id": "3DVjwY322flLObss05zyWiv03BA",
  "conclusion": "AUTHENTIC",
  "created_at": "2026-05-10T00:52:57.253083Z",
  "languages": [
    {
      "language": "english",
      "supported": true
    }
  ],
  "metadata": {},
  "milliseconds_to_conclusion": 7478,
  "probability": 0.0008578640117775649,
  "session_id": "9c9daa19-a737-4196-999b-a0b58789462d",
  "stream_id": "3DVjwW2XFqB9WIxmMCRqscCLB2E",
  "updated_at": "2026-05-10T00:52:57.253083Z"
}
```

In this example the LMS service reached a conclusion after ~7.5 seconds of audio. The `conclusion` field will be `AUTHENTIC`, `ARTIFICIAL`, or `INCONCLUSIVE`.

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
