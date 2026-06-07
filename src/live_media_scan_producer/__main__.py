import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from live_media_scan_producer.main import main, parse_args  # noqa: E402

main(parse_args())
