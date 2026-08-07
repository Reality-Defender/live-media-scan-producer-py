import asyncio
import json
import os
import tempfile
import unittest
import uuid
import wave
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from live_media_scan_producer import main
from live_media_scan_producer.main import (
    Config,
    SessionError,
    default_bitrate,
    detect_media_type,
    ensure_rate,
    is_allowed_media_type,
    media_type_base,
    parse_media_type_rate,
    read_start_response,
    read_stop_response,
    resolve_media_type,
    stream_audio,
)

ALLOWED_MEDIA = [
    "audio/basic",
    "audio/wav",
    "audio/L16",
    "audio/mpeg",
    "audio/mp3",
    "audio/aac",
    "audio/flac",
    "audio/ogg",
    "audio/opus",
    "audio/amr",
    "audio/x-m4a",
    "audio/m4a",
    "audio/mp4",
]


def hello_payload(allowed_media=None):
    return {
        'type': 'notice',
        'subtype': 'hello',
        'payload': {
            'version': 2,
            'allowed_media': allowed_media if allowed_media is not None else ALLOWED_MEDIA,
        },
    }


def configure_ws_recv(mock_ws, *messages):
    """Return scripted messages, then TimeoutError for stream pacing waits."""
    remaining = list(messages)

    async def _recv():
        if remaining:
            return remaining.pop(0)
        raise asyncio.TimeoutError()

    mock_ws.recv = AsyncMock(side_effect=_recv)


class TestMimeHelpers(unittest.TestCase):

    def test_media_type_base_strips_params_and_case(self):
        self.assertEqual(media_type_base("audio/L16;rate=8000"), "audio/l16")
        self.assertEqual(media_type_base("  AUDIO/WAV  "), "audio/wav")

    def test_parse_media_type_rate(self):
        self.assertEqual(parse_media_type_rate("audio/L16;rate=8000"), 8000)
        self.assertEqual(parse_media_type_rate("audio/L16; rate=16000 ; channels=1"), 16000)
        self.assertIsNone(parse_media_type_rate("audio/basic"))
        with self.assertRaises(ValueError):
            parse_media_type_rate("audio/L16;rate=abc")

    def test_detect_media_type_extensions(self):
        cases = {
            "a.wav": "audio/wav",
            "a.ulaw": "audio/basic",
            "a.l16": "audio/L16",
            "a.pcm": "audio/L16",
            "a.s16le": "audio/L16",
            "a.raw": "audio/L16",
            "a.mp3": "audio/mpeg",
            "a.aac": "audio/aac",
            "a.ogg": "audio/ogg",
            "a.opus": "audio/opus",
            "a.flac": "audio/flac",
            "a.amr": "audio/amr",
            "a.m4a": "audio/x-m4a",
            "a.mp4": "audio/mp4",
        }
        for path, expected in cases.items():
            self.assertEqual(detect_media_type(path), expected, path)

    def test_detect_media_type_unknown_extension(self):
        with self.assertRaises(ValueError) as ctx:
            detect_media_type("file.bin")
        self.assertIn("--mime-type", str(ctx.exception))

    def test_ensure_rate_appends_and_requires(self):
        self.assertEqual(ensure_rate("audio/L16", 8000), "audio/L16;rate=8000")
        self.assertEqual(ensure_rate("audio/L16;rate=16000", 8000), "audio/L16;rate=16000")
        self.assertEqual(ensure_rate("audio/basic", None), "audio/basic")
        with self.assertRaises(ValueError):
            ensure_rate("audio/L16", None)

    def test_resolve_media_type_override_and_rate(self):
        self.assertEqual(
            resolve_media_type("x.bin", mime_type_override="audio/mpeg"),
            "audio/mpeg",
        )
        self.assertEqual(
            resolve_media_type("x.pcm", rate=8000),
            "audio/L16;rate=8000",
        )
        self.assertEqual(
            resolve_media_type("x.bin", mime_type_override="audio/L16;rate=16000"),
            "audio/L16;rate=16000",
        )

    def test_default_bitrate_by_family(self):
        self.assertEqual(default_bitrate("audio/basic"), 64000)
        self.assertEqual(default_bitrate("audio/L16;rate=8000"), 128000)
        self.assertEqual(default_bitrate("audio/mpeg"), 128000)
        self.assertEqual(default_bitrate("audio/wav", wav_bitrate=256000), 256000)

    def test_is_allowed_media_type(self):
        self.assertTrue(is_allowed_media_type("audio/L16;rate=8000", ALLOWED_MEDIA))
        self.assertTrue(is_allowed_media_type("AUDIO/WAV", ALLOWED_MEDIA))
        self.assertFalse(is_allowed_media_type("video/mp4", ALLOWED_MEDIA))


class TestConfig(unittest.TestCase):

    @patch.dict(os.environ, {
        'API_KEY': 'test-key-123',
        'LMS_ENDPOINT': 'wss://localhost:8080/stream'
    })
    @patch('live_media_scan_producer.main.load_dotenv')
    def test_config_from_env(self, mock_load_dotenv):
        config = Config.from_env()

        self.assertEqual(config.api_key, 'test-key-123')
        self.assertEqual(config.lms_endpoint, 'wss://localhost:8080/stream')
        mock_load_dotenv.assert_called_once()

    @patch.dict(os.environ, {
        'API_KEY': 'test-key',
        'LMS_ENDPOINT': 'wss://example.com/ws'
    })
    @patch('live_media_scan_producer.main.load_dotenv')
    def test_config_lms_endpoint(self, mock_load_dotenv):
        config = Config.from_env()

        self.assertEqual(config.lms_endpoint, 'wss://example.com/ws')

    @patch.dict(os.environ, {
        'API_KEY': 'test-key',
        'SERVER_ADDRESS': 'legacy.example.com',
        'SERVER_PORT': '8443',
        'SERVER_PATH': 'ws'
    })
    @patch('live_media_scan_producer.main.load_dotenv')
    def test_config_legacy_fallback(self, mock_load_dotenv):
        config = Config.from_env()

        self.assertEqual(config.lms_endpoint, 'wss://legacy.example.com:8443/ws')

    @patch.dict(os.environ, {
        'API_KEY': 'test-key',
        'SERVER_ADDRESS': 'legacy.example.com',
    })
    @patch('live_media_scan_producer.main.load_dotenv')
    def test_config_legacy_fallback_default_port_and_path(self, mock_load_dotenv):
        config = Config.from_env()

        self.assertEqual(config.lms_endpoint, 'wss://legacy.example.com:443/ws')

    @patch.dict(os.environ, {}, clear=True)
    @patch('live_media_scan_producer.main.load_dotenv')
    def test_config_missing_env_vars(self, mock_load_dotenv):
        with self.assertRaises(KeyError):
            Config.from_env()

    @patch.dict(os.environ, {
        'API_KEY': 'test-key',
        'LMS_ENDPOINT': 'wss://example.com/ws',
    })
    @patch('live_media_scan_producer.main.load_dotenv')
    def test_config_mime_overrides_from_cli_args_only(self, mock_load_dotenv):
        config = Config.from_env(
            mime_type='audio/mpeg',
            bitrate=192000,
        )
        self.assertEqual(config.mime_type_override, 'audio/mpeg')
        self.assertEqual(config.bitrate, 192000)
        self.assertEqual(config.media_type, 'audio/mpeg')


class TestResponseReaders(unittest.IsolatedAsyncioTestCase):

    async def test_read_start_response_success(self):
        mock_ws = AsyncMock()
        success_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'success',
            'payload': {'stream_id': 'stream-123'}
        }
        mock_ws.recv.return_value = json.dumps(success_response)

        stream_id = await read_start_response(mock_ws)

        self.assertEqual(stream_id, 'stream-123')
        mock_ws.recv.assert_called_once()

    async def test_read_start_response_failure(self):
        mock_ws = AsyncMock()
        failure_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'fail',
            'payload': {'code': 'AUTH_ERROR', 'reason': 'Invalid API key'}
        }
        mock_ws.recv.return_value = json.dumps(failure_response)

        with self.assertRaises(Exception) as context:
            await read_start_response(mock_ws)

        self.assertIn('Failure while negotiating start', str(context.exception))

    async def test_read_start_response_wrong_type(self):
        mock_ws = AsyncMock()
        wrong_response = {
            'type': 'unexpected',
            'subtype': 'unknown',
            'payload': {}
        }
        mock_ws.recv.return_value = json.dumps(wrong_response)

        with self.assertRaises(Exception) as context:
            await read_start_response(mock_ws)

        self.assertIn('Received erroneous response', str(context.exception))

    async def test_read_start_response_unknown_status(self):
        mock_ws = AsyncMock()
        unknown_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'pending',
            'payload': {}
        }
        mock_ws.recv.return_value = json.dumps(unknown_response)

        with self.assertRaises(Exception) as context:
            await read_start_response(mock_ws)

        self.assertIn('Unknown status in start response', str(context.exception))

    async def test_read_stop_response_success(self):
        mock_ws = AsyncMock()
        success_response = {
            'type': 'response',
            'subtype': 'stop',
            'status': 'success',
            'payload': {'stream_id': 'stream-123', 'total_bytes': 1024}
        }
        mock_ws.recv.return_value = json.dumps(success_response)

        # Should not raise an exception
        await read_stop_response(mock_ws)

        mock_ws.recv.assert_called_once()

    async def test_read_stop_response_failure(self):
        mock_ws = AsyncMock()
        failure_response = {
            'type': 'response',
            'subtype': 'stop',
            'status': 'fail',
            'payload': {'code': 'STREAM_ERROR', 'reason': 'Stream not found'}
        }
        mock_ws.recv.return_value = json.dumps(failure_response)

        with self.assertRaises(Exception) as context:
            await read_stop_response(mock_ws)

        self.assertIsInstance(context.exception, SessionError)
        self.assertIn('Server rejected the stream', str(context.exception))
        self.assertIn('STREAM_ERROR', str(context.exception))
        self.assertIn('Stream not found', str(context.exception))

    async def test_read_stop_response_analysis_complete_notice(self):
        mock_ws = AsyncMock()
        notice_response = {
            'type': 'notice',
            'subtype': 'analysis_complete',
            'stream_id': 'stream-123'
        }
        mock_ws.recv.return_value = json.dumps(notice_response)

        # Should not raise an exception
        await read_stop_response(mock_ws)

        mock_ws.recv.assert_called_once()


class TestMainFunction(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Create a temporary WAV file for testing
        self.temp_wav_fd, self.temp_wav_path = tempfile.mkstemp(suffix='.wav')

        # Create a simple WAV file with test data
        with wave.open(self.temp_wav_path, 'wb') as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 2 bytes per sample
            wav_file.setframerate(8000)  # 8kHz
            # Write some sample data (silence)
            frames = b'\x00\x00' * 100  # 100 samples of silence
            wav_file.writeframes(frames)

        self.mock_args = MagicMock()
        self.mock_args.file_path = None
        self.mock_args.mime_type = None
        self.mock_args.rate = None
        self.mock_args.bitrate = None
        self.mock_args.test = True
        self.mock_args.debug = False

    def tearDown(self):
        # Clean up temporary file
        os.close(self.temp_wav_fd)
        os.unlink(self.temp_wav_path)

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_full_flow(self, mock_connect, mock_config):
        # Setup mocks
        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path=self.temp_wav_path
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws

        # Mock server responses
        start_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'success',
            'payload': {'stream_id': 'stream-456'}
        }
        stop_response = {
            'type': 'response',
            'subtype': 'stop',
            'status': 'success',
            'payload': {'stream_id': 'stream-456', 'total_bytes': 200}
        }

        configure_ws_recv(
            mock_ws,
            json.dumps(hello_payload()),
            json.dumps(start_response),
            json.dumps(stop_response),
        )

        # Mock wave.open to use our test file
        with patch('wave.open') as mock_wave_open:
            mock_wav_file = MagicMock()
            mock_wav_file.__enter__.return_value = mock_wav_file
            mock_wav_file.getframerate.return_value = 8000
            mock_wav_file.getnchannels.return_value = 1
            mock_wav_file.getsampwidth.return_value = 2
            mock_wav_file.readframes.side_effect = [b'\x00\x01' * 10, b'']  # Two chunks then EOF
            mock_wave_open.return_value = mock_wav_file

            await main._websocket_session(self.mock_args)

        mock_connect.assert_called_once_with(
            'wss://localhost:3000/stream',
            additional_headers={'X-API-KEY': 'test-key'}
        )

        # Verify messages sent
        self.assertEqual(mock_ws.send.call_count, 3)  # start request + audio data + stop request

        # Verify start request was sent
        start_call = mock_ws.send.call_args_list[0]
        start_data = json.loads(start_call[0][0])
        # Verify session_id is a valid UUID
        self.assertIsInstance(start_data['session_id'], str)
        uuid.UUID(start_data['session_id'])  # Will raise ValueError if not a valid UUID
        self.assertEqual(start_data['media_type'], 'audio/wav')

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_server_hello_error(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path=self.temp_wav_path
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws

        # Mock wrong hello response
        wrong_response = {
            'type': 'error',
            'message': 'Authentication failed'
        }
        mock_ws.recv.return_value = json.dumps(wrong_response)

        with self.assertRaises(Exception) as context:
            await main._websocket_session(self.mock_args)

        self.assertIn('Server did not send hello', str(context.exception))

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_rejects_media_type_not_in_allowed_media(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path=self.temp_wav_path,
            mime_type_override='audio/mpeg',
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws
        configure_ws_recv(
            mock_ws,
            json.dumps(hello_payload(allowed_media=['audio/basic', 'audio/wav'])),
        )

        with self.assertRaises(Exception) as context:
            await main._websocket_session(self.mock_args)

        self.assertIn('not in server allowed_media', str(context.exception))
        self.assertIn('audio/mpeg', str(context.exception))
        mock_ws.send.assert_not_called()

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_mp3_uses_default_bitrate(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path='/tmp/track.mp3',
            mime_type_override='audio/mpeg',
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws
        start_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'success',
            'payload': {'stream_id': 'stream-mp3'}
        }
        stop_response = {'type': 'response', 'subtype': 'stop', 'status': 'success', 'payload': {}}
        configure_ws_recv(
            mock_ws,
            json.dumps(hello_payload()),
            json.dumps(start_response),
            json.dumps(stop_response),
        )

        with patch('builtins.open', mock_open(read_data=b'\xff\xfb\x90\x00' * 64)), \
             patch('os.path.getsize', return_value=256):
            await main._websocket_session(self.mock_args)

        start_data = json.loads(mock_ws.send.call_args_list[0][0][0])
        self.assertEqual(start_data['media_type'], 'audio/mpeg')
        self.assertEqual(start_data['payload']['bitrate'], 128000)

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_bitrate_override(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path='/tmp/track.mp3',
            mime_type_override='audio/mpeg',
            bitrate=192000,
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws
        start_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'success',
            'payload': {'stream_id': 'stream-mp3'}
        }
        stop_response = {'type': 'response', 'subtype': 'stop', 'status': 'success', 'payload': {}}
        configure_ws_recv(
            mock_ws,
            json.dumps(hello_payload()),
            json.dumps(start_response),
            json.dumps(stop_response),
        )

        with patch('builtins.open', mock_open(read_data=b'\xff\xfb\x90\x00' * 64)), \
             patch('os.path.getsize', return_value=256):
            await main._websocket_session(self.mock_args)

        start_data = json.loads(mock_ws.send.call_args_list[0][0][0])
        self.assertEqual(start_data['payload']['bitrate'], 192000)

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_l16_includes_rate(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path='/tmp/raw.pcm',
            mime_type_override='audio/L16',
            sample_rate=8000,
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws
        start_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'success',
            'payload': {'stream_id': 'stream-l16'}
        }
        stop_response = {'type': 'response', 'subtype': 'stop', 'status': 'success', 'payload': {}}
        configure_ws_recv(
            mock_ws,
            json.dumps(hello_payload()),
            json.dumps(start_response),
            json.dumps(stop_response),
        )

        with patch('builtins.open', mock_open(read_data=b'\x00\x01' * 128)), \
             patch('os.path.getsize', return_value=256):
            await main._websocket_session(self.mock_args)

        start_data = json.loads(mock_ws.send.call_args_list[0][0][0])
        self.assertEqual(start_data['media_type'], 'audio/L16;rate=8000')
        self.assertEqual(start_data['payload']['bitrate'], 128000)

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_start_request_serialization(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path=self.temp_wav_path
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws

        # Mock responses to get to start request
        start_response = {'type': 'response', 'subtype': 'start', 'status': 'success', 'payload': {'stream_id': 'test'}}
        stop_response = {'type': 'response', 'subtype': 'stop', 'status': 'success', 'payload': {}}

        configure_ws_recv(
            mock_ws,
            json.dumps(hello_payload()),
            json.dumps(start_response),
            json.dumps(stop_response),
        )

        with patch('wave.open') as mock_wave_open:
            mock_wav_file = MagicMock()
            mock_wav_file.__enter__.return_value = mock_wav_file
            mock_wav_file.getframerate.return_value = 8000
            mock_wav_file.getnchannels.return_value = 1
            mock_wav_file.getsampwidth.return_value = 2
            mock_wav_file.readframes.return_value = b''  # Empty file
            mock_wave_open.return_value = mock_wav_file

            await main._websocket_session(self.mock_args)

        # Check that start request was properly serialized
        start_call = mock_ws.send.call_args_list[0]
        start_json = start_call[0][0]

        # Should be valid JSON
        start_data = json.loads(start_json)

        # Verify key fields
        self.assertEqual(start_data['type'], 'request')
        self.assertEqual(start_data['subtype'], 'start')
        # Verify session_id is a valid UUID
        self.assertIsInstance(start_data['session_id'], str)
        uuid.UUID(start_data['session_id'])  # Will raise ValueError if not a valid UUID
        self.assertIn('payload', start_data)
        self.assertIn('source_ids', start_data['payload'])

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_analysis_complete_triggers_stop_request(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path=self.temp_wav_path
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws

        start_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'success',
            'payload': {'stream_id': 'test-stream'}
        }
        analysis_complete_response = {
            'type': 'notice',
            'subtype': 'analysis_complete',
            'stream_id': 'test-stream'
        }
        stop_response = {'type': 'response', 'subtype': 'stop', 'status': 'success', 'payload': {}}

        configure_ws_recv(
            mock_ws,
            json.dumps(hello_payload()),
            json.dumps(start_response),
            json.dumps(analysis_complete_response),
            json.dumps(stop_response),
        )

        with patch('wave.open') as mock_wave_open:
            mock_wav_file = MagicMock()
            mock_wav_file.__enter__.return_value = mock_wav_file
            mock_wav_file.getframerate.return_value = 8000
            mock_wav_file.getnchannels.return_value = 1
            mock_wav_file.getsampwidth.return_value = 2
            mock_wav_file.readframes.return_value = b''  # Empty file
            mock_wave_open.return_value = mock_wav_file

            await main._websocket_session(self.mock_args)

        # Ensure a stop request was sent even after analysis_complete notice.
        stop_requests = []
        for call in mock_ws.send.call_args_list:
            payload = call[0][0]
            if isinstance(payload, str):
                data = json.loads(payload)
                if data.get('type') == 'request' and data.get('subtype') == 'stop':
                    stop_requests.append(data)

        self.assertEqual(len(stop_requests), 1)

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_no_analysis_complete_notice(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path=self.temp_wav_path
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws

        start_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'success',
            'payload': {'stream_id': 'test-stream'}
        }
        stop_response = {'type': 'response', 'subtype': 'stop', 'status': 'success', 'payload': {}}

        configure_ws_recv(
            mock_ws,
            json.dumps(hello_payload()),
            json.dumps(start_response),
            json.dumps(stop_response),
        )

        with patch('wave.open') as mock_wave_open:
            mock_wav_file = MagicMock()
            mock_wav_file.__enter__.return_value = mock_wav_file
            mock_wav_file.getframerate.return_value = 8000
            mock_wav_file.getnchannels.return_value = 1
            mock_wav_file.getsampwidth.return_value = 2
            mock_wav_file.readframes.return_value = b''  # Empty file
            mock_wave_open.return_value = mock_wav_file

            await main._websocket_session(self.mock_args)

        stop_requests = []
        for call in mock_ws.send.call_args_list:
            payload = call[0][0]
            if isinstance(payload, str):
                data = json.loads(payload)
                if data.get('type') == 'request' and data.get('subtype') == 'stop':
                    stop_requests.append(data)

        self.assertEqual(len(stop_requests), 1)

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_connection_closed_during_stream_raises_session_error(self, mock_connect, mock_config):
        from websockets.exceptions import ConnectionClosedOK
        from websockets.frames import Close

        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path=self.temp_wav_path,
            enable_result_retrieval=False,
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws
        start_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'success',
            'payload': {'stream_id': 'stream-closed'},
        }
        configure_ws_recv(
            mock_ws,
            json.dumps(hello_payload()),
            json.dumps(start_response),
        )
        closed = ConnectionClosedOK(Close(1000, ""), Close(1000, ""), rcvd_then_sent=True)
        mock_ws.send.side_effect = [
            None,  # start request JSON
            closed,  # first audio chunk
        ]

        with self.assertRaises(SessionError) as ctx:
            await main._websocket_session(self.mock_args)

        self.assertIn("while sending audio", str(ctx.exception))
        stop_requests = [
            json.loads(call[0][0])
            for call in mock_ws.send.call_args_list
            if isinstance(call[0][0], str)
            and json.loads(call[0][0]).get("subtype") == "stop"
        ]
        self.assertEqual(stop_requests, [])

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_stop_fail_during_stream_surfaces_reason(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            lms_endpoint='wss://localhost:3000/stream',
            file_path=self.temp_wav_path,
            enable_result_retrieval=False,
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws
        start_response = {
            'type': 'response',
            'subtype': 'start',
            'status': 'success',
            'payload': {'stream_id': 'stream-m4a'},
        }
        stop_fail = {
            'type': 'response',
            'subtype': 'stop',
            'status': 'fail',
            'payload': {
                'code': 'INVALID_REQUEST',
                'reason': 'MP4/M4A has mdat before moov (not faststart)',
            },
        }
        configure_ws_recv(
            mock_ws,
            json.dumps(hello_payload()),
            json.dumps(start_response),
            json.dumps(stop_fail),
        )

        with self.assertRaises(SessionError) as ctx:
            await main._websocket_session(self.mock_args)

        message = str(ctx.exception)
        self.assertIn('Server rejected the stream', message)
        self.assertIn('INVALID_REQUEST', message)
        self.assertIn('mdat before moov', message)
        stop_requests = [
            json.loads(call[0][0])
            for call in mock_ws.send.call_args_list
            if isinstance(call[0][0], str)
            and json.loads(call[0][0]).get('subtype') == 'stop'
        ]
        self.assertEqual(stop_requests, [])


class TestStreamAudioConnectionClosed(unittest.IsolatedAsyncioTestCase):

    async def test_connection_closed_while_sending_raises_session_error(self):
        from websockets.exceptions import ConnectionClosedOK
        from websockets.frames import Close

        mock_ws = AsyncMock()
        mock_ws.send.side_effect = ConnectionClosedOK(
            Close(1000, ""), Close(1000, ""), rcvd_then_sent=True,
        )
        audio_file = MagicMock()
        audio_file.read.return_value = b"\x00" * 64

        with self.assertRaises(SessionError) as ctx:
            await stream_audio(mock_ws, audio_file, chunk_size=64, sleep_per_chunk=0.0, pending_messages=[])

        message = str(ctx.exception)
        self.assertIn("Server closed the connection while sending audio", message)
        self.assertIn("code=1000", message)
        self.assertNotIn("fast-start", message)
        self.assertNotIn("likely rejected", message)

    async def test_connection_closed_during_recv_raises_session_error(self):
        from websockets.exceptions import ConnectionClosedOK
        from websockets.frames import Close

        mock_ws = AsyncMock()
        mock_ws.send.return_value = None
        mock_ws.recv.side_effect = ConnectionClosedOK(
            Close(1000, ""), Close(1000, ""), rcvd_then_sent=True,
        )
        audio_file = MagicMock()
        audio_file.read.return_value = b"\x00" * 64

        with self.assertRaises(SessionError) as ctx:
            await stream_audio(mock_ws, audio_file, chunk_size=64, sleep_per_chunk=1.0, pending_messages=[])

        self.assertIn("Server closed the connection during streaming", str(ctx.exception))

    async def test_stop_fail_during_streaming_raises_session_error(self):
        mock_ws = AsyncMock()
        mock_ws.send.return_value = None
        mock_ws.recv.return_value = json.dumps({
            'type': 'response',
            'subtype': 'stop',
            'status': 'fail',
            'payload': {
                'code': 'INVALID_REQUEST',
                'reason': 'MP4/M4A has mdat before moov (not faststart)',
            },
        })
        audio_file = MagicMock()
        audio_file.read.return_value = b"\x00" * 64

        with self.assertRaises(SessionError) as ctx:
            await stream_audio(mock_ws, audio_file, chunk_size=64, sleep_per_chunk=1.0, pending_messages=[])

        message = str(ctx.exception)
        self.assertIn("Server rejected the stream", message)
        self.assertIn("INVALID_REQUEST", message)
        self.assertIn("mdat before moov", message)

    async def test_error_notice_during_streaming_raises_session_error(self):
        mock_ws = AsyncMock()
        mock_ws.send.return_value = None
        mock_ws.recv.return_value = json.dumps({
            'type': 'notice',
            'subtype': 'error',
            'payload': {'message': 'Media chunk exceeds the maximum size.'},
        })
        audio_file = MagicMock()
        audio_file.read.return_value = b"\x00" * 64

        with self.assertRaises(SessionError) as ctx:
            await stream_audio(mock_ws, audio_file, chunk_size=64, sleep_per_chunk=1.0, pending_messages=[])

        self.assertIn("Server error notice: Media chunk exceeds the maximum size.", str(ctx.exception))


class TestWAVStreaming(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Create test WAV data
        self.test_frames = [b'\x00\x01' * 5, b'\x00\x02' * 5, b'']  # Two chunks then EOF

    @patch('wave.open')
    async def test_wav_streaming_chunks(self, mock_wave_open):
        mock_ws = AsyncMock()

        # Setup WAV file mock
        mock_wav_file = MagicMock()
        mock_wav_file.__enter__.return_value = mock_wav_file
        mock_wav_file.getsampwidth.return_value = 2
        mock_wav_file.readframes.side_effect = self.test_frames
        mock_wave_open.return_value = mock_wav_file

        # Simulate the streaming part of main()
        with wave.open("test.wav", "rb") as wav_file:
            chunk_size = 1024
            chunks_sent = 0
            while True:
                frames = wav_file.readframes(chunk_size // wav_file.getsampwidth())
                if not frames:
                    break
                await mock_ws.send(frames)
                chunks_sent += 1
                await asyncio.sleep(0.01)

        # Verify correct number of chunks sent
        self.assertEqual(chunks_sent, 2)
        self.assertEqual(mock_ws.send.call_count, 2)

        # Verify correct data was sent
        mock_ws.send.assert_any_call(b'\x00\x01' * 5)
        mock_ws.send.assert_any_call(b'\x00\x02' * 5)


if __name__ == '__main__':
    unittest.main()
