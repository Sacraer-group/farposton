"""Run the isolated demo on the container network."""
import os

from demo.local import environment, offline_calendar

os.environ.update(environment())
# The host port is restricted to loopback in compose.yaml; Docker traffic
# reaches the container from its bridge rather than 127.0.0.1.
os.environ.update(WEB_HOST="0.0.0.0", ALLOWED_IPS="")
offline_calendar()

from web.app import main  # noqa: E402

main()
