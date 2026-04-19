"""Native PySide6/Qt UI — replaces the earlier pywebview+React path.

The web UI kept breaking inside WKWebView (see logs/voice_agent.log from the
previous run — zero HTTP requests, no `loaded` event). This package is a 1:1
port of the Companion design into a real Qt widget tree that binds to the
AgentEvent bus directly, without HTTP/WebSocket hops.
"""
