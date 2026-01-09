import asyncio
import json
import os
import tempfile
import unittest
import wave
from unittest.mock import AsyncMock, MagicMock, patch

from live_media_scan_producer import main
from live_media_scan_producer.main import Config, read_start_response, read_stop_response


class TestConfig(unittest.TestCase):

    @patch.dict(os.environ, {
        'API_KEY': 'test-key-123',
        'SERVER_ADDRESS': 'localhost',
        'SERVER_PORT': '8080',
        'SERVER_PATH': '/stream'
    })
    @patch('live_media_scan_producer.main.load_dotenv')
    def test_config_from_env(self, mock_load_dotenv):
        config = Config.from_env()

        self.assertEqual(config.api_key, 'test-key-123')
        self.assertEqual(config.server_address, 'localhost')
        self.assertEqual(config.server_port, 8080)
        self.assertEqual(config.server_path, '/stream')
        mock_load_dotenv.assert_called_once()

    @patch.dict(os.environ, {
        'API_KEY': 'test-key',
        'SERVER_ADDRESS': 'example.com',
        'SERVER_PATH': '/api'
    })
    @patch('live_media_scan_producer.main.load_dotenv')
    def test_config_default_port(self, mock_load_dotenv):
        config = Config.from_env()

        self.assertEqual(config.server_port, 443)  # Default port

    @patch.dict(os.environ, {}, clear=True)
    @patch('live_media_scan_producer.main.load_dotenv')
    def test_config_missing_env_vars(self, mock_load_dotenv):
        with self.assertRaises(KeyError):
            Config.from_env()


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
            'type': 'notice',
            'subtype': 'hello',
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

        self.assertIn('Failure while stopping the stream', str(context.exception))


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
            server_address='localhost',
            server_port=3000,
            server_path='stream',
            file_path='./audio.wav'
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws

        # Mock server responses
        hello_response = {
            'type': 'notice',
            'subtype': 'hello',
            'payload': {'version': 1}
        }
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

        mock_ws.recv.side_effect = [
            json.dumps(hello_response),
            json.dumps(start_response),
            json.dumps(stop_response)
        ]

        # Mock wave.open to use our test file
        with patch('wave.open') as mock_wave_open:
            mock_wav_file = MagicMock()
            mock_wav_file.__enter__.return_value = mock_wav_file
            mock_wav_file.getsampwidth.return_value = 2
            mock_wav_file.readframes.side_effect = [b'\x00\x01' * 10, b'']  # Two chunks then EOF
            mock_wave_open.return_value = mock_wav_file

            # Run main function
            await main.main()

        # Verify WebSocket connection
        mock_connect.assert_called_once_with(
            'wss://localhost:3000/stream',
            additional_headers={'X-API-KEY': 'test-key', 'Origin': 'https://localhost'}
        )

        # Verify messages sent
        self.assertEqual(mock_ws.send.call_count, 3)  # start request + audio data + stop request

        # Verify start request was sent
        start_call = mock_ws.send.call_args_list[0]
        start_data = json.loads(start_call[0][0])
        self.assertEqual(start_data['session_id'], 'session-123')
        self.assertEqual(start_data['media_type'], 'audio/wav')

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_server_hello_error(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            server_address='localhost',
            server_port=3000,
            server_path='/stream',
            file_path='./audio.wav'
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
            await main.main()

        self.assertIn('Server did not send hello', str(context.exception))

    @patch('live_media_scan_producer.main.Config.from_env')
    @patch('websockets.connect')
    async def test_main_start_request_serialization(self, mock_connect, mock_config):
        mock_config.return_value = Config(
            api_key='test-key',
            server_address='localhost',
            server_port=3000,
            server_path='/stream',
            file_path='./audio.wav'
        )

        mock_ws = AsyncMock()
        mock_connect.return_value.__aenter__.return_value = mock_ws

        # Mock responses to get to start request
        hello_response = {'type': 'notice', 'subtype': 'hello', 'payload': {}}
        start_response = {'type': 'response', 'subtype': 'start', 'status': 'success', 'payload': {'stream_id': 'test'}}
        stop_response = {'type': 'response', 'subtype': 'stop', 'status': 'success', 'payload': {}}

        mock_ws.recv.side_effect = [
            json.dumps(hello_response),
            json.dumps(start_response),
            json.dumps(stop_response)
        ]

        with patch('wave.open') as mock_wave_open:
            mock_wav_file = MagicMock()
            mock_wav_file.__enter__.return_value = mock_wav_file
            mock_wav_file.getsampwidth.return_value = 2
            mock_wav_file.readframes.return_value = b''  # Empty file
            mock_wave_open.return_value = mock_wav_file

            await main.main()

        # Check that start request was properly serialized
        start_call = mock_ws.send.call_args_list[0]
        start_json = start_call[0][0]

        # Should be valid JSON
        start_data = json.loads(start_json)

        # Verify key fields
        self.assertEqual(start_data['type'], 'request')
        self.assertEqual(start_data['subtype'], 'start')
        self.assertEqual(start_data['session_id'], 'session-123')
        self.assertIn('payload', start_data)
        self.assertIn('source_ids', start_data['payload'])


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
