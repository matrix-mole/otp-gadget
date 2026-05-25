"""Gunicorn production entrypoint.

Run via:

    gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker \\
             -w 1 -b 0.0.0.0:$PORT firmware.sim.wsgi:app

gevent + gevent-websocket is the long-standing Flask-SocketIO production
recipe (eventlet is now deprecated upstream). The worker handles gevent setup
before serving. This deliberately does NOT import `firmware.sim.__main__`
(interactive kill-port prompt + watchdog hot-reloader are dev-only and would
hang in a container).
"""

from firmware.sim.app import build_app

# `async_mode="gevent"` matches the GeventWebSocketWorker. One worker only:
# Flask-SocketIO needs sticky sessions, so scale by container size, not workers.
app = build_app(async_mode="gevent")
