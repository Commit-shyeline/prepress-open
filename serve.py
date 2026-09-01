# -*- coding: utf-8 -*-
"""Serve the app with Waitress, for anything that is not one person looking at it.

`python -m prepress.app` starts Flask's development server, which is fine on a laptop and wrong the
moment real people arrive: it says so itself on every boot. This is the same app under a production
WSGI server.

    python serve.py --port 5099 --threads 8

Nothing here is shop-specific. The deployment decides who may use it through the environment:

    PREPRESS_ADMIN_TOKEN     the admin panel's token (unset = panel disabled)
    PREPRESS_SESSION_SECRET  set it and every working endpoint needs a signed bearer token
    PREPRESS_LOGIN_URL       where an unauthenticated visitor is sent ({next} = the page wanted)
    PREPRESS_BASE_PATH       the prefix this app is mounted under, when behind a proxy
    PREPRESS_MODELS_DIR      directory of .glb product models (unset = the 3D product list is empty)
    PREPRESS_DEMO_ARTWORK    a PDF to dress the demo flag with (unset = the bundled one)
"""
import argparse
import logging

from waitress import serve

from prepress.app import app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to bind (default loopback, for a proxy in front)")
    parser.add_argument("--port", type=int, default=5099)
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger(__name__).info("prepress-open on %s:%s, %s threads",
                                     args.host, args.port, args.threads)
    # A 512 MB upload over a slow uplink takes minutes; the default channel timeout would cut it.
    serve(app, host=args.host, port=args.port, threads=args.threads,
          channel_timeout=900, max_request_body_size=512 * 1024 * 1024)


if __name__ == "__main__":
    main()
