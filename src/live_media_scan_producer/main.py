import asyncio
import argparse
import json
from dataclasses import dataclass, asdict
import os
import struct
from dotenv import load_dotenv
import websockets
import wave
import uuid

from .types import StartRequest, StopRequest, SourceIds, Metadata, Properties, StartRequestPayload, \
    StopRequestPayload

try:
    from websockets.exceptions import ConnectionClosed
except Exception:
    try:
        from websockets import ConnectionClosed  # type: ignore
    except Exception:
        ConnectionClosed = None  # type: ignore


@dataclass
class Config:
    api_key: str
    server_address: str
    server_port: int
    server_path: str
    file_path: str
    
    @property
    def media_type(self) -> str:
        """Automatically determine media type from file extension"""
        if self.file_path.lower().endswith('.wav'):
            return "audio/wav"
        else:
            return "audio/basic"

    @classmethod
    def from_env(cls, file_path: str = None) -> 'Config':
        load_dotenv()
        
        # Command-line args take precedence over environment variables
        file_path = file_path or os.environ.get('FILE_PATH', './audio.wav')

        return cls(
            api_key=os.environ['API_KEY'],
            server_address=os.environ['SERVER_ADDRESS'],
            server_port=int(os.environ.get('SERVER_PORT', '443')),
            server_path=os.environ['SERVER_PATH'],
            file_path=file_path
        )

    @property
    def source_filename(self) -> str:
        return os.path.basename(self.file_path)

async def read_start_response(ws, pending_messages: list[str] | None = None) -> str:
    if pending_messages is None:
        pending_messages = []
    try:
        while True:
            # Use a timeout to detect if server is taking too long or closing
            try:
                if pending_messages:
                    message = pending_messages.pop(0)
                else:
                    message = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                raise Exception("Timeout waiting for start response from server")
            except Exception as e:
                if ConnectionClosed is not None and isinstance(e, ConnectionClosed):
                    print(f"Connection closed while waiting for response: code={e.code}, reason={e.reason}")
                    # Check if there's a close reason that might contain error info
                    if e.reason:
                        raise Exception(f"Connection closed before receiving start response: code={e.code}, reason={e.reason}")
                    raise Exception(
                        f"Connection closed before receiving start response: code={e.code}. "
                        "Server may have rejected the request."
                    )
                raise

            packet = json.loads(message)
            print(f"Received message: {packet}")

            # Check if this is an error response
            if packet.get('type') == 'response' and packet.get('subtype') == 'start':
                status = packet.get('status')
                payload = packet.get('payload', {})

                if status == 'success':
                    stream_id = payload['stream_id']
                    print(f"Server approved the start of the stream: {payload}")
                    return stream_id
                if status == 'fail':
                    raise Exception(f"Failure while negotiating start: {payload}")
                raise Exception(f"Unknown status in start response: {status}")
            if packet.get('type') == 'notice':
                # Server might send a notice before closing
                print(f"Server sent notice: {packet}")
                # Continue waiting for the actual response
                continue

            # Log unexpected message
            print(f"Received unexpected message type: {packet.get('type')}, subtype: {packet.get('subtype')}")
            raise Exception(f"Received erroneous response: {message}")
    except Exception as e:
        # Re-raise if it's already our custom exception
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
        # Otherwise wrap it
        raise Exception(f"Error reading start response: {e}") from e


def get_wav_header_size(file_path: str) -> int:
    """
    Get the size of the WAV header by finding the data chunk.
    Returns the offset where audio data starts.
    """
    with open(file_path, 'rb') as f:
        # Read RIFF header
        riff_header = f.read(12)
        if riff_header[0:4] != b'RIFF' or riff_header[8:12] != b'WAVE':
            raise ValueError(f"Not a valid WAV file: {file_path}")
        
        # Find the 'fmt ' chunk
        while True:
            chunk_header = f.read(8)
            if not chunk_header or len(chunk_header) < 8:
                raise ValueError(f"Unexpected end of file or fmt chunk not found: {file_path}")
            
            chunk_id = chunk_header[0:4]
            chunk_size = struct.unpack('<I', chunk_header[4:8])[0]
            
            if chunk_id == b'fmt ':
                break
            
            # Skip this chunk
            f.seek(chunk_size, 1)
        
        # Skip fmt chunk
        f.seek(chunk_size, 1)
        
        # Find the 'data' chunk
        while True:
            chunk_header = f.read(8)
            if not chunk_header or len(chunk_header) < 8:
                raise ValueError(f"Unexpected end of file or data chunk not found: {file_path}")
            
            chunk_id = chunk_header[0:4]
            chunk_size = struct.unpack('<I', chunk_header[4:8])[0]
            
            if chunk_id == b'data':
                # Return current position (start of data chunk + 8 bytes for chunk header)
                return f.tell()
            
            # Skip this chunk
            f.seek(chunk_size, 1)


def parse_wav_header(file_path: str) -> tuple[int, int, int]:
    """
    Parse WAV file header to extract sample rate, channels, and sample width.
    Supports both PCM (format 1) and G.711 μ-law (format 7) formats.
    
    Returns:
        Tuple of (sample_rate, num_channels, sample_width_bytes)
    """
    with open(file_path, 'rb') as f:
        # Read RIFF header
        riff_header = f.read(12)
        if riff_header[0:4] != b'RIFF' or riff_header[8:12] != b'WAVE':
            raise ValueError(f"Not a valid WAV file: {file_path}")
        
        # Find the 'fmt ' chunk
        while True:
            chunk_header = f.read(8)
            if not chunk_header or len(chunk_header) < 8:
                raise ValueError(f"Unexpected end of file or fmt chunk not found: {file_path}")
            
            chunk_id = chunk_header[0:4]
            chunk_size = struct.unpack('<I', chunk_header[4:8])[0]
            
            if chunk_id == b'fmt ':
                break
            
            # Skip this chunk
            f.seek(chunk_size, 1)
        
        # Read format chunk (minimum 16 bytes for standard fmt chunk)
        if chunk_size < 16:
            raise ValueError(f"Invalid fmt chunk size: {chunk_size}")
        
        fmt_data = f.read(chunk_size)
        if len(fmt_data) < 16:
            raise ValueError(f"Not enough fmt chunk data: {len(fmt_data)}")
        
        # Parse fmt chunk
        # Format: audioFormat (2) + numChannels (2) + sampleRate (4) + 
        #         byteRate (4) + blockAlign (2) + bitsPerSample (2)
        format_tag = struct.unpack('<H', fmt_data[0:2])[0]
        num_channels = struct.unpack('<H', fmt_data[2:4])[0]
        sample_rate = struct.unpack('<I', fmt_data[4:8])[0]
        bits_per_sample = struct.unpack('<H', fmt_data[14:16])[0]
        
        # Handle different format tags
        # 1 = PCM, 7 = μ-law, 6 = A-law
        if format_tag == 7:  # WAVE_FORMAT_MULAW (G.711 μ-law)
            # μ-law is 8-bit
            sample_width = 1
        elif format_tag == 6:  # WAVE_FORMAT_ALAW (G.711 A-law)
            # A-law is 8-bit
            sample_width = 1
        elif format_tag == 1:  # WAVE_FORMAT_PCM
            # PCM uses bits_per_sample from header
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
            if packet.get('subtype') == 'analysis_complete':
                print(f"Analysis complete notice received: {packet}")
                return
            print(f"Received notice while waiting for stop response: {packet}")
            continue

        if packet.get('type') != 'response' or packet.get('subtype') != 'stop':
            raise Exception(f"Received erroneous response: {message}")

        status = packet.get('status')
        payload = packet.get('payload', {})

        if status == 'success':
            print(f"Server approved the stop of the stream: {payload}")
            return
        elif status == 'fail':
            raise Exception(f"Failure while stopping the stream: {payload}")
        else:
            raise Exception("Unknown status in stop response")


async def check_for_analysis_complete(
    ws,
    pending_messages: list[str],
    analysis_complete_event: asyncio.Event,
    timeout: float = 0.01
) -> None:
    if analysis_complete_event.is_set() or pending_messages:
        return

    try:
        message = await asyncio.wait_for(ws.recv(), timeout=timeout)
    except asyncio.TimeoutError:
        return
    except Exception as e:
        if ConnectionClosed is not None and isinstance(e, ConnectionClosed):
            print(f"Connection closed while waiting for analysis complete notice: code={e.code}, reason={e.reason}")
            return
        raise

    packet = json.loads(message)
    if packet.get('type') == 'notice' and packet.get('subtype') == 'analysis_complete':
        print(f"Analysis complete notice received: {packet}")
        analysis_complete_event.set()
        return

    pending_messages.append(message)


def parse_args():
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(
        description='Live Media Scan Producer - Send audio to LMS service via WebSocket',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables (loaded from .env file):
  API_KEY          - API key for authentication (required)
  SERVER_ADDRESS   - LMS server address (required)
  SERVER_PATH      - WebSocket path (required)
  SERVER_PORT      - Server port (default: 443)
  FILE_PATH        - Path to audio file (default: ./audio.wav)

Media type is automatically detected from file extension:
  - Files ending in .wav → audio/wav
  - All other files → audio/basic (raw μ-law)
        """
    )
    parser.add_argument(
        '--file', '-f',
        dest='file_path',
        help='Path to audio file (overrides FILE_PATH env var)'
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    config = Config.from_env(
        file_path=args.file_path
    )

    url = f"wss://{config.server_address}:{config.server_port}/{config.server_path}"
    headers = {
        'X-API-KEY': config.api_key
    }

    async with websockets.connect(url, additional_headers=headers) as ws:
        print(f"WebSocket connected: {url}")

        message = await ws.recv()
        packet = json.loads(message)

        if packet.get('type') != 'notice' or packet.get('subtype') != 'hello':
            raise Exception("Server did not send hello!")

        print(f"Server sent hello: {packet['payload']}")

        # Generate a unique session ID
        session_id = str(uuid.uuid4())

        # Calculate bitrate based on media type (auto-detected from file extension)
        media_type = config.media_type
        print(f"Detected media type: {media_type} (from file extension)")
        
        if media_type == "audio/basic":
            # audio/basic is G.711 μ-law: 8000 Hz, mono, 8-bit = 64 kbps = 8000 bytes/sec
            calculated_bitrate = 64000  # 64 kbps
            print(f"Using audio/basic format: bitrate={calculated_bitrate} bps (8000 bytes/sec)")
        else:
            # For audio/wav, read file properties to calculate bitrate
            # Use manual parsing to support G.711 μ-law (format 7) which wave module doesn't support
            try:
                # Try using wave module first (faster for PCM files)
                with wave.open(config.file_path, "rb") as wav_file:
                    sample_rate = wav_file.getframerate()
                    num_channels = wav_file.getnchannels()
                    sample_width = wav_file.getsampwidth()
            except wave.Error:
                # Fall back to manual parsing for formats wave doesn't support (e.g., μ-law)
                sample_rate, num_channels, sample_width = parse_wav_header(config.file_path)
            
            # Calculate bitrate: sample_rate * channels * bits_per_sample
            calculated_bitrate = sample_rate * num_channels * sample_width * 8
            print(f"WAV file properties: sample_rate={sample_rate}, channels={num_channels}, sample_width={sample_width}, calculated_bitrate={calculated_bitrate}")

        start_request = StartRequest(
            session_id=session_id,
            media_type=media_type,
            payload=StartRequestPayload(
                bitrate=calculated_bitrate,
                analysis_channel="1.1",
                primary_source_id="phone_number",
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
                    test=True,
                ),
            )
        )

        print("Sending start request")
        
        # Serialize the request and print it for debugging
        request_dict = asdict(start_request)
        request_json = json.dumps(request_dict)
        print(f"Start request JSON: {request_json}")
        
        await ws.send(request_json)
        
        # Small delay to allow server to process the request
        await asyncio.sleep(0.1)

        pending_messages: list[str] = []
        stream_id = await read_start_response(ws, pending_messages)

        print("Beginning streaming audio...")
        analysis_complete_event = asyncio.Event()

        # Read and stream audio file in chunks
        if media_type == "audio/basic":
            # For audio/basic, send raw audio data (no WAV header)
            # If the file is a WAV file, we need to skip the header
            with open(config.file_path, "rb") as audio_file:
                # Check if it's a WAV file by reading the first 4 bytes
                header_check = audio_file.read(4)
                audio_file.seek(0)  # Reset to beginning
                
                if header_check == b'RIFF':
                    # It's a WAV file, skip the header
                    header_size = get_wav_header_size(config.file_path)
                    audio_file.seek(header_size)  # Skip to audio data
                    print(f"Skipping WAV header ({header_size} bytes) for audio/basic")
                else:
                    # It's already raw audio data
                    print("Reading raw audio data (no WAV header)")
                
                chunk_size = 1024  # bytes - can be any size, LMS will buffer appropriately
                while True:
                    await check_for_analysis_complete(ws, pending_messages, analysis_complete_event)
                    if analysis_complete_event.is_set():
                        print("Analysis complete received; stopping media transmission.")
                        break
                    chunk = audio_file.read(chunk_size)
                    if not chunk:
                        break
                    await ws.send(chunk)
                    await check_for_analysis_complete(ws, pending_messages, analysis_complete_event)
                    await asyncio.sleep(0.01)  # Small delay for real-time simulation
        else:
            # For audio/wav, send the actual WAV file (including header)
            # The LMS will parse the header and buffer to create 1-second snippets
            with open(config.file_path, "rb") as wav_file:
                chunk_size = 1024  # bytes - can be any size, LMS will buffer appropriately
                while True:
                    await check_for_analysis_complete(ws, pending_messages, analysis_complete_event)
                    if analysis_complete_event.is_set():
                        print("Analysis complete received; stopping media transmission.")
                        break
                    chunk = wav_file.read(chunk_size)
                    if not chunk:
                        break
                    await ws.send(chunk)
                    await check_for_analysis_complete(ws, pending_messages, analysis_complete_event)
                    await asyncio.sleep(0.01)  # Small delay for real-time simulation

        print("Finished streaming audio")

        stop_request = StopRequest(
            stream_id=stream_id,
            payload=StopRequestPayload(reason="Normal"),
        )

        await ws.send(json.dumps(asdict(stop_request)))
        await read_stop_response(ws, pending_messages)


if __name__ == "__main__":
    asyncio.run(main())
