"""Twilio request authentication for pipecat's dev runner - its own banner
calls itself the "PIPECAT DEVELOPMENT RUNNER", and by default POST / and
WS /ws accept a request from anyone, not just Twilio. That's real financial
exposure, not a cosmetic gap: an open /ws spins up a full Anthropic/
Deepgram/ElevenLabs pipeline per connection.

Two layers, because Twilio doesn't sign the WebSocket upgrade the way it
signs webhooks (a documented Twilio limitation, not an oversight here):

1. POST / (the TwiML webhook): real signature verification via the twilio
   SDK's RequestValidator against TWILIO_AUTH_TOKEN - Twilio's own
   documented method (twilio.com/docs/usage/security). Replaces pipecat's
   own auto-generated (unauthenticated) handler for this route entirely.
2. WS /ws: can't be signature-verified the same way, so a signature-valid
   POST / mints a short-lived, one-time HMAC token (pipecat's own built-in
   --ws-auth token mechanism - see PIPECAT_WEBSOCKET_AUTH in bot.py) and
   hands it back as a query param on the wss:// URL in the TwiML. /ws then
   only ever accepts a connection that resulted from a verified POST /
   first, not a direct guess at the fixed /ws path.
"""

import os

from loguru import logger
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from twilio.request_validator import RequestValidator

from pipecat.runner.run import _generate_ws_token


def _twiml(proxy: str, token: str) -> str:
    # Path segment, not a ?token= query param: verified against a real call
    # that Twilio's <Stream> noun does not reliably carry a query string
    # through to the actual WebSocket connection it opens (the token never
    # reached _extract_ws_token, every real call's /ws got rejected) - the
    # path-based /ws/{token} route pipecat also registers doesn't have that
    # ambiguity.
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f'    <Stream url="wss://{proxy}/ws/{token}"></Stream>\n'
        "  </Connect>\n"
        "</Response>"
    )


def _public_url(request: Request) -> str:
    """Reconstructs the URL Twilio actually signed, not request.url as seen
    by the process itself - Railway's edge terminates TLS and proxies over
    plain HTTP internally, so request.url.scheme/host would otherwise read
    "http"/an internal address, and every signature check would fail even
    for genuine Twilio requests.
    """
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host", request.url.netloc
    )
    url = f"{proto}://{host}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    return url


class TwilioWebhookAuthMiddleware:
    """Pure ASGI middleware, not the `@app.middleware("http")` decorator -
    that variant only ever observes a request alongside the route it
    matched, it can't take over the response for a route pipecat's own
    main() already registered. This one intercepts POST / before Starlette
    routes it at all.
    """

    def __init__(self, app: ASGIApp, proxy: str):
        self._app = app
        self._proxy = proxy
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        # Fails closed: no auth token configured means nothing can be
        # verified, so every POST / is refused rather than silently let
        # through - see RequestValidator's own None-check below.
        self._validator = RequestValidator(auth_token) if auth_token else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        form = await request.form()
        signature = request.headers.get("x-twilio-signature", "")
        valid = bool(self._validator) and self._validator.validate(
            _public_url(request), dict(form), signature
        )
        if not valid:
            logger.warning(
                f"Rejected POST / with invalid/missing Twilio signature "
                f"(signature present: {bool(signature)})"
            )
            response = PlainTextResponse("Forbidden", status_code=403)
            await response(scope, receive, send)
            return

        token = _generate_ws_token()
        response = HTMLResponse(content=_twiml(self._proxy, token), media_type="application/xml")
        await response(scope, receive, send)
