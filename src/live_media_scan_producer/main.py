import asyncio
import argparse
import json
from dataclasses import dataclass, asdict
import logging
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from dotenv import load_dotenv
import websockets
import wave
import uuid

from .models import StartRequest, StopRequest, SourceIds, Metadata, Properties, StartRequestPayload, \
    StopRequestPayload

try:
    from websockets.exceptions import ConnectionClosed
except Exception:
    try:
        from websockets import ConnectionClosed  # type: ignore
    except Exception:
        ConnectionClosed = None  # type: ignore

log = logging.getLogger(__name__)

EXTENSION_MEDIA_TYPES: dict[str, str] = {
    ".wav": "audio/wav",
    ".ulaw": "audio/basic",
    ".l16": "audio/L16",
    ".pcm": "audio/L16",
    ".s16le": "audio/L16",
    ".raw": "audio/L16",
    ".mp3": "audio/mpeg",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".amr": "audio/amr",
    ".m4a": "audio/x-m4a",
    ".mp4": "audio/mp4",
}

G711_MEDIA_TYPES = frozenset({"audio/basic"})
LPCM_MEDIA_TYPES = frozenset({"audio/l16", "audio/pcm", "audio/x-l16", "audio/x-wav-lpcm"})
COMPRESSED_MEDIA_TYPES = frozenset({
    "audio/mpeg", "audio/mp3", "audio/aac", "audio/ogg", "application/ogg",
    "audio/vorbis", "audio/opus", "audio/flac", "audio/amr",
    "audio/x-m4a", "audio/m4a", "audio/mp4",
})
DEFAULT_COMPRESSED_BITRATE = 128_000
DEFAULT_G711_BITRATE = 64_000


def media_type_base(media_type: str) -> str:
    """Return type/subtype lowercased, without MIME parameters."""
    base = media_type.strip().lower()
    if ";" in base:
        base = base.split(";", 1)[0].strip()
    return base


def parse_media_type_rate(media_type: str) -> int | None:
    """Extract rate=N from a MIME string, or None if absent."""
    for part in media_type.split(";")[1:]:
        part = part.strip()
        if not part:
            continue
        key, _, val = part.partition("=")
        if key.strip().lower() != "rate":
            continue
        val = val.strip().strip("'\"")
        try:
            rate = int(val)
        except ValueError as e:
            raise ValueError(f"invalid rate={val!r} in media_type {media_type!r}") from e
        if rate <= 0:
            raise ValueError(f"invalid rate={val!r} in media_type {media_type!r}")
        return rate
    return None


def detect_media_type(file_path: str) -> str:
    """Map a file extension to an LMS media type."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in EXTENSION_MEDIA_TYPES:
        raise ValueError(
            f"Unknown file extension {ext!r} for {file_path!r}. "
            "Specify --mime-type explicitly."
        )
    return EXTENSION_MEDIA_TYPES[ext]


def is_lpcm_family(media_type: str) -> bool:
    return media_type_base(media_type) in LPCM_MEDIA_TYPES


def is_g711_family(media_type: str) -> bool:
    return media_type_base(media_type) in G711_MEDIA_TYPES


def is_raw_pcm_family(media_type: str) -> bool:
    """Headerless telephony or LPCM types that may need WAV-header stripping."""
    return is_g711_family(media_type) or is_lpcm_family(media_type)


def ensure_rate(media_type: str, rate: int | None) -> str:
    """Ensure LPCM media types include rate=; append from rate when needed."""
    existing = parse_media_type_rate(media_type)
    if existing is not None:
        return media_type
    if rate is None:
        if is_lpcm_family(media_type):
            raise ValueError(
                f"mime type {media_type!r} requires rate= "
                "(e.g. --mime-type 'audio/L16;rate=8000' or --rate 8000)"
            )
        return media_type
    if rate <= 0:
        raise ValueError(f"rate must be a positive integer, got {rate}")
    return f"{media_type};rate={rate}"


def resolve_media_type(
    file_path: str,
    mime_type_override: str | None = None,
    rate: int | None = None,
) -> str:
    """Resolve the wire media_type from override/extension and optional rate."""
    media_type = (mime_type_override or "").strip() or detect_media_type(file_path)
    return ensure_rate(media_type, rate)


def is_allowed_media_type(media_type: str, allowed_media: list[str]) -> bool:
    base = media_type_base(media_type)
    if not base:
        return False
    return any(media_type_base(allowed) == base for allowed in allowed_media)


def default_bitrate(media_type: str, wav_bitrate: int | None = None) -> int:
    """Sensible default bitrate (bps) for pacing, by media type family."""
    base = media_type_base(media_type)
    if base == "audio/wav":
        if wav_bitrate is None:
            raise ValueError("wav_bitrate is required for audio/wav")
        return wav_bitrate
    if is_g711_family(media_type):
        return DEFAULT_G711_BITRATE
    if is_lpcm_family(media_type):
        rate = parse_media_type_rate(media_type)
        if rate is None:
            raise ValueError(f"media_type {media_type!r} requires rate= for bitrate")
        return rate * 16  # mono s16le
    if base in COMPRESSED_MEDIA_TYPES:
        return DEFAULT_COMPRESSED_BITRATE
    # Unknown types still need a pacing hint for LMS
    return DEFAULT_COMPRESSED_BITRATE


@dataclass
class Config:
    api_key: str
    lms_endpoint: str
    file_path: str
    enable_result_retrieval: bool = True
    mime_type_override: str | None = None
    sample_rate: int | None = None
    bitrate: int | None = None

    @property
    def session_api_base_url(self) -> str:
        """Derive the session API base URL from the LMS WebSocket endpoint.

        Transforms e.g. wss://dev.lms.os.realitydefender.xyz:443/ws
                     -> https://dev.session-api.os.realitydefender.xyz
        """
        parsed = urllib.parse.urlparse(self.lms_endpoint)
        scheme = "https" if parsed.scheme in ("wss", "https") else "http"
        hostname = parsed.hostname or ""
        host = hostname.replace(".lms.", ".session-api.", 1)
        standard_port = 443 if scheme == "https" else 80
        netloc = f"{host}:{parsed.port}" if parsed.port and parsed.port != standard_port else host
        return urllib.parse.urlunparse((scheme, netloc, "", "", "", ""))

    def resolve_media_type(self) -> str:
        return resolve_media_type(self.file_path, self.mime_type_override, self.sample_rate)

    @property
    def media_type(self) -> str:
        """Resolved media type (extension or override, with rate= when needed)."""
        return self.resolve_media_type()

    @classmethod
    def from_env(
        cls,
        file_path: str | None = None,
        mime_type: str | None = None,
        sample_rate: int | None = None,
        bitrate: int | None = None,
    ) -> 'Config':
        load_dotenv()

        # Command-line --file takes precedence over FILE_PATH
        file_path = file_path or os.environ.get('FILE_PATH', './audio.wav')

        if 'LMS_ENDPOINT' in os.environ:
            lms_endpoint = os.environ['LMS_ENDPOINT']
        elif 'SERVER_ADDRESS' in os.environ:
            server_address = os.environ['SERVER_ADDRESS']
            server_port = int(os.environ.get('SERVER_PORT', '443'))
            server_path = os.environ.get('SERVER_PATH', 'ws')
            lms_endpoint = f"wss://{server_address}:{server_port}/{server_path}"
        else:
            raise KeyError('LMS_ENDPOINT')

        enable_result_retrieval = os.environ.get('ENABLE_RESULT_RETRIEVAL', 'true').lower() in ('true', '1', 'yes')

        return cls(
            api_key=os.environ['API_KEY'],
            lms_endpoint=lms_endpoint,
            file_path=file_path,
            enable_result_retrieval=enable_result_retrieval,
            mime_type_override=mime_type,
            sample_rate=sample_rate,
            bitrate=bitrate,
        )

    @property
    def source_filename(self) -> str:
        return os.path.basename(self.file_path)


async def read_start_response(ws, pending_messages: list[str] | None = None) -> str:
    if pending_messages is None:
        pending_messages = []
    try:
        while True:
            try:
                if pending_messages:
                    message = pending_messages.pop(0)
                else:
                    message = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                raise Exception("Timeout waiting for start response from server")
            except Exception as e:
                if ConnectionClosed is not None and isinstance(e, ConnectionClosed):
                    log.warning("Connection closed while waiting for response: code=%s, reason=%s", e.code, e.reason)
                    if e.reason:
                        raise Exception(f"Connection closed before receiving start response: code={e.code}, reason={e.reason}")
                    raise Exception(
                        f"Connection closed before receiving start response: code={e.code}. "
                        "Server may have rejected the request."
                    )
                raise

            packet = json.loads(message)
            log.debug("Received message:\n%s", json.dumps(packet, indent=2))

            if packet.get('type') == 'response' and packet.get('subtype') == 'start':
                status = packet.get('status')
                payload = packet.get('payload', {})

                if status == 'success':
                    stream_id = payload['stream_id']
                    return stream_id
                if status == 'fail':
                    raise Exception(f"Failure while negotiating start: {json.dumps(payload, indent=2)}")
                raise Exception(f"Unknown status in start response: {status}")
            if packet.get('type') == 'notice':
                log.debug("Server sent notice:\n%s", json.dumps(packet, indent=2))
                continue

            log.warning("Received unexpected message type: %s, subtype: %s", packet.get('type'), packet.get('subtype'))
            raise Exception(f"Received erroneous response: {message}")
    except Exception as e:
        if any(
            marker in str(e)
            for marker in (
                "Connection closed",
                "Failure while negotiating",
                "Timeout",
                "Unknown status in start response",
                "Received erroneous response",
            )
        ):
            raise
        raise Exception(f"Error reading start response: {e}") from e


def get_wav_header_size(file_path: str) -> int:
    """
    Get the size of the WAV header by finding the data chunk.
    Returns the offset where audio data starts.
    """
    with open(file_path, 'rb') as f:
        riff_header = f.read(12)
        if riff_header[0:4] != b'RIFF' or riff_header[8:12] != b'WAVE':
            raise ValueError(f"Not a valid WAV file: {file_path}")
        
        while True:
            chunk_header = f.read(8)
            if not chunk_header or len(chunk_header) < 8:
                raise ValueError(f"Unexpected end of file or fmt chunk not found: {file_path}")
            
            chunk_id = chunk_header[0:4]
            chunk_size = struct.unpack('<I', chunk_header[4:8])[0]
            
            if chunk_id == b'fmt ':
                break
            
            f.seek(chunk_size, 1)
        
        f.seek(chunk_size, 1)
        
        while True:
            chunk_header = f.read(8)
            if not chunk_header or len(chunk_header) < 8:
                raise ValueError(f"Unexpected end of file or data chunk not found: {file_path}")
            
            chunk_id = chunk_header[0:4]
            chunk_size = struct.unpack('<I', chunk_header[4:8])[0]
            
            if chunk_id == b'data':
                return f.tell()
            
            f.seek(chunk_size, 1)


def parse_wav_header(file_path: str) -> tuple[int, int, int]:
    """
    Parse WAV file header to extract sample rate, channels, and sample width.
    Supports both PCM (format 1) and G.711 μ-law (format 7) formats.
    
    Returns:
        Tuple of (sample_rate, num_channels, sample_width_bytes)
    """
    with open(file_path, 'rb') as f:
        riff_header = f.read(12)
        if riff_header[0:4] != b'RIFF' or riff_header[8:12] != b'WAVE':
            raise ValueError(f"Not a valid WAV file: {file_path}")
        
        while True:
            chunk_header = f.read(8)
            if not chunk_header or len(chunk_header) < 8:
                raise ValueError(f"Unexpected end of file or fmt chunk not found: {file_path}")
            
            chunk_id = chunk_header[0:4]
            chunk_size = struct.unpack('<I', chunk_header[4:8])[0]
            
            if chunk_id == b'fmt ':
                break
            
            f.seek(chunk_size, 1)
        
        if chunk_size < 16:
            raise ValueError(f"Invalid fmt chunk size: {chunk_size}")
        
        fmt_data = f.read(chunk_size)
        if len(fmt_data) < 16:
            raise ValueError(f"Not enough fmt chunk data: {len(fmt_data)}")
        
        format_tag = struct.unpack('<H', fmt_data[0:2])[0]
        num_channels = struct.unpack('<H', fmt_data[2:4])[0]
        sample_rate = struct.unpack('<I', fmt_data[4:8])[0]
        bits_per_sample = struct.unpack('<H', fmt_data[14:16])[0]
        
        if format_tag == 7:  # WAVE_FORMAT_MULAW (G.711 μ-law)
            sample_width = 1
        elif format_tag == 6:  # WAVE_FORMAT_ALAW (G.711 A-law)
            sample_width = 1
        elif format_tag == 1:  # WAVE_FORMAT_PCM
            sample_width = bits_per_sample // 8
        else:
            raise ValueError(f"Unsupported WAV format tag: {format_tag} (expected 1=PCM, 6=A-law, 7=μ-law)")
        
        if sample_rate <= 0 or num_channels <= 0 or sample_width <= 0:
            raise ValueError(f"Invalid WAV format: sample_rate={sample_rate}, channels={num_channels}, sample_width={sample_width}")
        
        return sample_rate, num_channels, sample_width


async def read_stop_response(ws, pending_messages: list[str] | None = None) -> None:
    if pending_messages is None:
        pending_messages = []
    while True:
        if pending_messages:
            message = pending_messages.pop(0)
        else:
            message = await ws.recv()
        packet = json.loads(message)

        if packet.get('type') == 'notice':
            if packet.get('subtype') == 'transmission_stop':
                reason = packet.get('payload', {}).get('reason', 'unknown')
                log.info("Transmission stop received (reason: %s)", reason)
                return
            if packet.get('subtype') == 'analysis_complete':
                log.info("Analysis complete notice received")
                log.debug("Analysis complete payload:\n%s", json.dumps(packet, indent=2))
                return
            log.debug("Received notice while waiting for stop response:\n%s", json.dumps(packet, indent=2))
            continue

        if packet.get('type') != 'response' or packet.get('subtype') != 'stop':
            raise Exception(f"Received erroneous response: {message}")

        status = packet.get('status')
        payload = packet.get('payload', {})

        if status == 'success':
            log.info(
                "Server approved stop of stream: stream_id=%s, total_bytes=%s",
                payload.get('stream_id'), payload.get('total_bytes'),
            )
            log.debug("Stop response payload:\n%s", json.dumps(payload, indent=2))
            return
        elif status == 'fail':
            raise Exception(f"Failure while stopping the stream: {json.dumps(payload, indent=2)}")
        else:
            raise Exception("Unknown status in stop response")


async def stream_audio(
    ws,
    audio_file,
    chunk_size: int,
    sleep_per_chunk: float,
    pending_messages: list[str],
) -> None:
    """Stream audio at real-time rate, stopping early if analysis_complete is received.

    After each chunk is sent the remaining pacing interval is spent attempting
    to receive server messages, so analysis_complete is caught with minimal delay
    without needing a separate concurrent task.
    """
    loop = asyncio.get_event_loop()
    while True:
        chunk = audio_file.read(chunk_size)
        if not chunk:
            return
        try:
            await ws.send(chunk)
        except Exception as e:
            if ConnectionClosed is not None and isinstance(e, ConnectionClosed):
                log.warning("Connection closed while sending: code=%s, reason=%s", e.code, e.reason)
                return
            raise

        deadline = loop.time() + sleep_per_chunk
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            except Exception as e:
                if ConnectionClosed is not None and isinstance(e, ConnectionClosed):
                    log.warning("Connection closed during streaming: code=%s, reason=%s", e.code, e.reason)
                    return
                raise
            packet = json.loads(message)
            if packet.get('type') == 'notice' and packet.get('subtype') == 'transmission_stop':
                reason = packet.get('payload', {}).get('reason', 'unknown')
                log.info("Transmission stop received (reason: %s); stopping media transmission.", reason)
                return
            if packet.get('type') == 'notice' and packet.get('subtype') == 'analysis_complete':
                log.info("Analysis complete received; stopping media transmission.")
                return
            if packet.get('type') == 'notice' and packet.get('subtype') == 'delay':
                delay_ms = packet.get('payload', {}).get('delay_ms', 0)
                log.info("Delay requested by server: %d ms", delay_ms)
                deadline += delay_ms / 1000
                continue
            log.debug("Received message during streaming:\n%s", json.dumps(packet, indent=2))
            pending_messages.append(message)


def poll_and_print_session_results(
    session_api_base_url: str,
    api_key: str,
    session_id: str,
    poll_interval: float = 2.0,
    max_attempts: int = 3,
) -> None:
    """Poll /stream_results until results are available, then pretty-print them.

    The session API returns an empty array when results are not yet ready;
    per the API docs, callers should wait at least 1 second before retrying.
    """
    url = f"{session_api_base_url}/stream_results?{urllib.parse.urlencode({'session_id': session_id})}"

    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"X-API-KEY": api_key})
            with urllib.request.urlopen(req) as response:
                results = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            log.error("Failed to retrieve session results (HTTP %s): %s", e.code, body)
            return
        except Exception as e:
            log.error("Failed to retrieve session results: %s", e)
            return

        if results:
            output = results[0] if len(results) == 1 else results
            if isinstance(output, dict):
                log.info(
                    "Session results: session_id=%s, stream_id=%s, conclusion=%s, probability=%.2f",
                    output.get('session_id'), output.get('stream_id'),
                    output.get('conclusion'), output.get('probability') or 0.0,
                )
            log.debug("Session results (full):\n%s", json.dumps(output, indent=2))
            return

        if attempt < max_attempts:
            log.info("Results not ready yet (attempt %d/%d), retrying in %ds...", attempt, max_attempts, int(poll_interval))
            time.sleep(poll_interval)

    log.warning("Session results were not available after %d attempts.", max_attempts)


def parse_args():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description='Live Media Scan Producer - Send audio to LMS service via WebSocket',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables (loaded from .env file):
  API_KEY                   - API key for authentication (required)
  LMS_ENDPOINT              - Full WebSocket URL (required, e.g. wss://lms.example.com/ws)
  FILE_PATH                 - Path to audio file (default: ./audio.wav)
  ENABLE_RESULT_RETRIEVAL   - Poll and pretty-print analysis results after session (default: true)

Mime type is detected from file extension unless --mime-type is set:
  .wav              -> audio/wav
  .ulaw             -> audio/basic
  .l16 / .pcm / .s16le / .raw -> audio/L16 (requires --rate)
  .mp3              -> audio/mpeg
  .aac / .ogg / .opus / .flac / .amr / .m4a / .mp4 -> matching compressed type

Examples:
  --file audio.wav
  --file audio.ulaw
  --file raw.pcm --rate 8000
  --file track.mp3
  --file data.bin --mime-type 'audio/L16;rate=16000'
        """
    )
    parser.add_argument(
        '--file', '-f',
        dest='file_path',
        help='Path to audio file (overrides FILE_PATH env var)'
    )
    parser.add_argument(
        '--mime-type', '-m',
        dest='mime_type',
        help='Override MIME type sent as media_type in the start request (overrides extension detection)'
    )
    parser.add_argument(
        '--rate',
        dest='rate',
        type=int,
        help='Sample rate (Hz) for headerless LPCM; appended as ;rate=N when missing'
    )
    parser.add_argument(
        '--bitrate',
        dest='bitrate',
        type=int,
        help='Bitrate override in bits/sec used for pacing (defaults by mime type)'
    )
    parser.add_argument(
        '--test',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Mark session as a test call, use --no-test for a real call (default: --test)'
    )
    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        help='Enable debug logging (includes full JSON message bodies)'
    )
    return parser.parse_args()


def _wav_bitrate(file_path: str) -> int:
    try:
        with wave.open(file_path, "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            num_channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
    except wave.Error:
        sample_rate, num_channels, sample_width = parse_wav_header(file_path)

    calculated = sample_rate * num_channels * sample_width * 8
    log.info(
        "WAV file properties: sample_rate=%d, channels=%d, sample_width=%d, calculated_bitrate=%d",
        sample_rate, num_channels, sample_width, calculated,
    )
    return calculated


async def _websocket_session(args) -> tuple[Config, str]:
    """Run the WebSocket streaming session and return (config, session_id)."""
    config = Config.from_env(
        file_path=getattr(args, 'file_path', None),
        mime_type=getattr(args, 'mime_type', None),
        sample_rate=getattr(args, 'rate', None),
        bitrate=getattr(args, 'bitrate', None),
    )
    is_test_call = args.test

    # Resolve media type before connecting so LPCM missing-rate fails fast.
    media_type = config.resolve_media_type()
    media_source = "override" if config.mime_type_override else "file extension"

    url = config.lms_endpoint
    headers = {
        'X-API-KEY': config.api_key
    }

    async with websockets.connect(url, additional_headers=headers) as ws:
        log.info("WebSocket connected: %s", url)

        message = await ws.recv()
        packet = json.loads(message)

        if packet.get('type') != 'notice' or packet.get('subtype') != 'hello':
            raise Exception("Server did not send hello!")

        hello_payload = packet.get('payload') or {}
        log.info("Server sent hello")
        log.debug("Hello payload:\n%s", json.dumps(hello_payload, indent=2))

        if 'allowed_media' not in hello_payload:
            raise Exception("Server hello did not include allowed_media")
        allowed_media = hello_payload['allowed_media']
        if not isinstance(allowed_media, list):
            raise Exception("Server hello allowed_media must be a list")

        if not is_allowed_media_type(media_type, allowed_media):
            raise Exception(
                f"Media type {media_type!r} is not in server allowed_media: {allowed_media}"
            )

        session_id = str(uuid.uuid4())
        log.info("Created session_id: %s", session_id)
        log.info("Resolved media type: %s (from %s)", media_type, media_source)
        log.info("Call type: %s", "TEST CALL" if is_test_call else "REAL CALL")

        wav_bitrate = None
        if media_type_base(media_type) == "audio/wav":
            wav_bitrate = _wav_bitrate(config.file_path)

        if config.bitrate is not None:
            if config.bitrate <= 0:
                raise ValueError(f"bitrate must be a positive integer, got {config.bitrate}")
            calculated_bitrate = config.bitrate
            log.info("Using bitrate override: %d bps", calculated_bitrate)
        else:
            calculated_bitrate = default_bitrate(media_type, wav_bitrate=wav_bitrate)
            log.info("Using bitrate: %d bps", calculated_bitrate)

        start_request = StartRequest(
            session_id=session_id,
            media_type=media_type,
            payload=StartRequestPayload(
                bitrate=calculated_bitrate,
                analysis_channel="1.1",
                primary_source_id="file_name",
                source_ids=SourceIds(
                    phone_number="+1234567890",
                    display_name="John Doe",
                    file_name=config.source_filename,
                    email="johndoe@example.com",
                ),
                metadata=Metadata(),
                properties=Properties(
                    direction="inbound",
                    session_type="call",
                    test=is_test_call,
                ),
            )
        )

        request_dict = asdict(start_request)
        log.info("Sending start request")
        log.debug("Start request:\n%s", json.dumps(request_dict, indent=2))
        await ws.send(json.dumps(request_dict))

        await asyncio.sleep(0.1)

        pending_messages: list[str] = []
        stream_id = await read_start_response(ws, pending_messages)
        log.info("Started stream_id: %s (session_id: %s)", stream_id, session_id)

        try:
            # calculate the expected duration of the transmission as an aid to the user
            chunk_size = 1024
            sleep_per_chunk = chunk_size * 8 / calculated_bitrate

            file_size = os.path.getsize(config.file_path)
            duration_secs = int(file_size * 8 / calculated_bitrate)
            duration_str = f"{duration_secs // 60}m {duration_secs % 60}s" if duration_secs >= 60 else f"{duration_secs}s"
            log.info("Beginning streaming audio... (source duration: ~%s)", duration_str)

            with open(config.file_path, "rb") as audio_file:
                if is_raw_pcm_family(media_type):
                    header_check = audio_file.read(4)
                    audio_file.seek(0)
                    if header_check == b'RIFF':
                        header_size = get_wav_header_size(config.file_path)
                        audio_file.seek(header_size)
                        log.info(
                            "Skipping WAV header (%d bytes) for raw media type %s",
                            header_size, media_type,
                        )
                    else:
                        log.info("Reading raw audio data (no WAV header)")

                await stream_audio(ws, audio_file, chunk_size, sleep_per_chunk, pending_messages)

            log.info("Finished streaming audio")

            stop_request = StopRequest(
                stream_id=stream_id,
                payload=StopRequestPayload(reason="NORMAL"),
            )

            await ws.send(json.dumps(asdict(stop_request)))
            await read_stop_response(ws, pending_messages)
        except Exception:
            log.error("Error during session: session_id=%s, stream_id=%s", session_id, stream_id)
            raise

    return config, session_id


def main(args) -> None:
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # websockets logs every binary frame at DEBUG — suppress it regardless of our level
    logging.getLogger("websockets").setLevel(logging.WARNING)

    config, session_id = asyncio.run(_websocket_session(args))

    if config.enable_result_retrieval:
        poll_and_print_session_results(config.session_api_base_url, config.api_key, session_id)


if __name__ == "__main__":
    main(parse_args())
