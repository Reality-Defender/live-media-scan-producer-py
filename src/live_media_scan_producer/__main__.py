import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from live_media_scan_producer.main import main, parse_args  # noqa: E402

asyncio.run(main(parse_args()))
