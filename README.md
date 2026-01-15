# Live Media Scan Producer

A Python producer for sending audio to the Live Media Scan (LMS) service via WebSocket.

## Configuration

The producer is configured via environment variables (loaded from `.env` file):

### Required Variables

- `API_KEY` - API key for authentication
- `SERVER_ADDRESS` - LMS server address (hostname)
- `SERVER_PATH` - WebSocket path (e.g., `ws`)

### Optional Variables

- `SERVER_PORT` - Server port (default: `443`)
- `FILE_PATH` - Path to audio file (default: `./audio.wav`)

**Note:** Media type is automatically detected from file extension:
- Files ending in `.wav` → `audio/wav`
- All other files → `audio/basic` (raw μ-law)

**Note:** The source filename sent in metadata is always the basename of `FILE_PATH`.

## Usage

### Command-Line Arguments

You can specify the file path via command-line arguments:

```bash
python -m live_media_scan_producer.main --file ./audio.wav
```

Or use the short option:

```bash
python -m live_media_scan_producer.main -f ./audio.wav
```

**Note:** Command-line arguments take precedence over environment variables.

### Example 1: Send WAV file (default)

```bash
export API_KEY="your-api-key"
export SERVER_ADDRESS="lms.example.com"
export SERVER_PATH="ws"
export FILE_PATH="./audio.wav"
python -m live_media_scan_producer.main
```

Or with command-line arguments:

```bash
python -m live_media_scan_producer.main --file ./audio.wav
```

### Example 2: Send audio/basic (raw μ-law)

```bash
export API_KEY="your-api-key"
export SERVER_ADDRESS="lms.example.com"
export SERVER_PATH="ws"
export FILE_PATH="./audio.ulaw"  # Non-.wav extension → automatically uses audio/basic
python -m live_media_scan_producer.main
```

Or with command-line arguments:

```bash
python -m live_media_scan_producer.main -f ./audio.ulaw
```

Or use a WAV file (header will be automatically stripped):

```bash
export FILE_PATH="./audio_ulaw.wav"  # .wav extension → uses audio/wav (includes header)
# To use audio/basic with a WAV file, rename it or use a different extension
```

### Example 3: Using .env file

Create a `.env` file:

```env
API_KEY=your-api-key
SERVER_ADDRESS=lms.example.com
SERVER_PATH=ws
SERVER_PORT=443
FILE_PATH=./audio.wav
```

Then run:

```bash
python -m live_media_scan_producer.main
```

Or override specific values:

```bash
python -m live_media_scan_producer.main --file ./different.wav
```

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
