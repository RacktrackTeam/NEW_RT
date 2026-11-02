"""python3.11 -m appliance_mapper photo.jpg [--vendor F5 --model r5800] [--device load_balancer] [--out DIR]"""
from appliance_mapper.pipeline import main

main()

# The result is written. Exit now: letting Python tear down the OCR runtime and PyTorch threads
# aborts on macOS ("recursive_mutex lock failed", exit code 134) after a good result, which a caller
# would read as a failure. PORT_AGENT_NORMAL_EXIT=1 keeps the normal exit (for tracing tools).
import os as _os
import sys as _sys
if not _os.environ.get("PORT_AGENT_NORMAL_EXIT"):
    _sys.stdout.flush()
    _sys.stderr.flush()
    _os._exit(0)
