import asyncio
import json
from dataclasses import dataclass, asdict
import os
from dotenv import load_dotenv
import websockets
import wave
import uuid

from .types import StartRequest, StopRequest, SourceIds, Metadata, Properties, StartRequestPayload, \
    StopRequestPayload


@dataclass
class Config:
    api_key: str
    server_address: str
    server_port: int
    server_path: str
    file_path: str

    @classmethod
    def from_env(cls) -> 'Config':
        load_dotenv()
        return cls(
            api_key=os.environ['API_KEY'],
            server_address=os.environ['SERVER_ADDRESS'],
            server_port=int(os.environ.get('SERVER_PORT', '443')),
            server_path=os.environ['SERVER_PATH'],
            file_path=os.environ.get('FILE_PATH', './audio.wav')
        )


async def read_start_response(ws) -> str:
    try:
        # Use a timeout to detect if server is taking too long or closing
        try:
            message = await asyncio.wait_for(ws.recv(), timeout=5.0)
        except asyncio.TimeoutError:
            raise Exception("Timeout waiting for start response from server")
        except websockets.exceptions.ConnectionClosed as e:
            print(f"Connection closed while waiting for response: code={e.code}, reason={e.reason}")
            # Check if there's a close reason that might contain error info
            if e.reason:
                raise Exception(f"Connection closed before receiving start response: code={e.code}, reason={e.reason}")
            else:
                raise Exception(f"Connection closed before receiving start response: code={e.code}. Server may have rejected the request.")
        
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
            elif status == 'fail':
                raise Exception(f"Failure while negotiating start: {payload}")
            else:
                raise Exception(f"Unknown status in start response: {status}")
        elif packet.get('type') == 'notice':
            # Server might send a notice before closing
            print(f"Server sent notice: {packet}")
            # Continue waiting for the actual response
            return await read_start_response(ws)
        else:
            # Log unexpected message
            print(f"Received unexpected message type: {packet.get('type')}, subtype: {packet.get('subtype')}")
            raise Exception(f"Received erroneous response: {message}")
    except Exception as e:
        # Re-raise if it's already our custom exception
        if "Connection closed" in str(e) or "Failure while negotiating" in str(e) or "Timeout" in str(e):
            raise
        # Otherwise wrap it
        raise Exception(f"Error reading start response: {e}") from e


async def read_stop_response(ws) -> None:
    while True:
        message = await ws.recv()
        packet = json.loads(message)

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


async def main():
    config = Config.from_env()

    url = f"wss://{config.server_address}:{config.server_port}/{config.server_path}"
    headers = {
        'X-API-KEY': config.api_key,
        'Origin': 'https://localhost'
    }

    async with websockets.connect(url, additional_headers=headers) as ws:
        print("WebSocket connected")

        message = await ws.recv()
        packet = json.loads(message)

        if packet.get('type') != 'notice' or packet.get('subtype') != 'hello':
            raise Exception("Server did not send hello!")

        print(f"Server sent hello: {packet['payload']}")

        # Generate a unique session ID
        session_id = str(uuid.uuid4())

        # Read WAV file properties to calculate correct bitrate and get format info
        with wave.open(config.file_path, "rb") as wav_file:
            sample_rate = wav_file.getframerate()
            num_channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            # Calculate bitrate: sample_rate * channels * bits_per_sample
            calculated_bitrate = sample_rate * num_channels * sample_width * 8
            print(f"WAV file properties: sample_rate={sample_rate}, channels={num_channels}, sample_width={sample_width}, calculated_bitrate={calculated_bitrate}")

        start_request = StartRequest(
            session_id=session_id,
            media_type="audio/wav",
            payload=StartRequestPayload(
                bitrate=calculated_bitrate,
                analysis_channel="1.1",
                primary_source_id="phone_number",
                source_ids=SourceIds(
                    phone_number="+1234567890",
                    display_name="John Doe",
                    file_name=os.path.basename(config.file_path),
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

        stream_id = await read_start_response(ws)

        print("Beginning streaming audio...")

        # Read and stream WAV file in chunks
        # For audio/wav, send the actual WAV file (including header)
        # The LMS will parse the header and buffer to create 1-second snippets
        with open(config.file_path, "rb") as wav_file:
            chunk_size = 1024  # bytes - can be any size, LMS will buffer appropriately
            while True:
                chunk = wav_file.read(chunk_size)
                if not chunk:
                    break
                await ws.send(chunk)
                await asyncio.sleep(0.01)  # Small delay for real-time simulation

        print("Finished streaming audio")

        stop_request = StopRequest(
            stream_id=stream_id,
            payload=StopRequestPayload(reason="Normal"),
        )

        await ws.send(json.dumps(asdict(stop_request)))
        await read_stop_response(ws)


if __name__ == "__main__":
    asyncio.run(main())
