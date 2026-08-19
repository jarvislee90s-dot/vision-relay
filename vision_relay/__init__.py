"""vision-relay: a transparent HTTP proxy at the harness boundary that gives text-only models vision.

Standalone resident HTTP server on 127.0.0.1:8787 that intercepts images in
Anthropic / Responses / Chat requests, transcribes them via a VLM, and forwards
text to the real upstream. Design: docs/superpowers/specs/2026-08-13-vision-relay-design.md.
"""

__version__ = "0.1.0"
