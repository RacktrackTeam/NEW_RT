"""Every test runs with outside calls off: the port agents' web search, the specifications' online lookup (hard rule 2),
and every model hub (Hugging Face, torch hub, Ultralytics): src/local_models.py points their caches at models/ and turns
their lookups off, and any HTTP request that is not to this machine goes to a closed port and fails."""
import os
import pathlib
import sys

os.environ["SWITCH_MAPPER_WEB_SEARCH"] = "0"
os.environ["RACKTRACK_ONLINE_LOOKUPS"] = "0"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
    os.environ[_k] = "http://127.0.0.1:9"
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engine"))
import local_models  # noqa: E402,F401
