import asyncio
import json
from dataclasses import dataclass, asdict
import os
from dotenv import load_dotenv
import websockets
import wave

from .types import StartRequest, StopRequest, SourceIds, Metadata, Properties, StartRequestPayload, \
    StopRequestPayload


@dataclass
class Config:
    api_key: str
    server_address: str
    server_port: int
    server_path: str

    @classmethod
    def from_env(cls) -> 'Config':
        load_dotenv()
        return cls(
            api_key=os.environ['API_KEY'],
            server_address=os.environ['SERVER_ADDRESS'],
            server_port=int(os.environ.get('SERVER_PORT', '443')),
            server_path=os.environ['SERVER_PATH']
        )


async def read_start_response(ws) -> str:
    while True:
        message = await ws.recv()
        packet = json.loads(message)

        if packet.get('type') != 'response' or packet.get('subtype') != 'start':
            raise Exception(f"Received erroneous response: {message}")

        status = packet.get('status')
        payload = packet.get('payload', {})

        if status == 'success':
            stream_id = payload['stream_id']
            print(f"Server approved the start of the stream: {payload}")
            return stream_id
        elif status == 'fail':
            raise Exception(f"Failure while negotiating start: {payload}")
        else:
            raise Exception("Unknown status in start response")


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

    url = f"ws://{config.server_address}:{config.server_port}/{config.server_path}"
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

        start_request = StartRequest(
            session_id="session-123",
            media_type="audio/wav",
            payload=StartRequestPayload(
                bitrate=128000,
                analysis_channel="1.1",
                primary_source_id="phone_number",
                source_ids=SourceIds(
                    phone_number="+1234567890",
                    display_name="John Doe",
                    file_name="call-123-audio.wav",
                    email="johndoe@example.com",
                ),
                metadata=Metadata(),
                properties=Properties(
                    direction="inbound",
                    session_type="real_call",
                ),
            )
        )

        print("Sending start request")

        await ws.send(json.dumps(asdict(start_request)))

        stream_id = await read_start_response(ws)

        print("Beginning streaming audio...")

        # Read and stream WAV file in chunks
        with wave.open("audio.wav", "rb") as wav_file:
            chunk_size = 1024  # bytes
            while True:
                frames = wav_file.readframes(chunk_size // wav_file.getsampwidth())
                if not frames:
                    break
                await ws.send(frames)
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
