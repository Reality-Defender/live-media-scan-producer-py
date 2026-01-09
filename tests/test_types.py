import unittest
import json
from dataclasses import asdict

from live_media_scan_producer.types import StartRequest, StartRequestPayload, SourceIds, Metadata, Properties, \
    StopRequest, StopRequestPayload


class TestRoundtrip(unittest.TestCase):
    def test_roundtrip_start_request(self):
        start = StartRequest(
            session_id="sess-round",
            media_type="audio/wav",
            payload=StartRequestPayload(
                bitrate=8000,
                analysis_channel="both",
                primary_source_id="primary-rt",
                source_ids=SourceIds(
                    phone_number="+1111111111",
                    display_name="Test User",
                    file_name="test.wav",
                    email="test@test.com"
                ),
                metadata=Metadata(),
                properties=Properties(
                    direction="inbound",
                    session_type="call"
                )
            )
        )

        data = asdict(start)
        json_str = json.dumps(data)
        deserialized = json.loads(json_str)

        self.assertEqual(deserialized['session_id'], "sess-round")
        self.assertEqual(deserialized['payload']['bitrate'], 8000)

    def test_roundtrip_stop_request(self):
        stop = StopRequest(
            stream_id="stream-roundtrip",
            payload=StopRequestPayload(reason="Error")
        )

        data = asdict(stop)
        json_str = json.dumps(data)
        deserialized = json.loads(json_str)

        self.assertEqual(deserialized['stream_id'], "stream-roundtrip")
        self.assertEqual(deserialized['payload']['reason'], "Error")


if __name__ == '__main__':
    unittest.main()
