#!/usr/bin/env python3

from gevent import monkey
monkey.patch_all()

import json
import os
import sys
from pathlib import Path

import gevent
from gevent.queue import Queue, Empty
from gevent.subprocess import Popen, PIPE

import websocket

from bottle import (
    Bottle,
    abort,
    redirect,
    request,
    static_file,
)

from geventwebsocket import WebSocketServer


# ============================================================
# Paths / config
# ============================================================

ROOT = Path(__file__).resolve().parent

APP_DIR = ROOT / "app"
IMG_DIR = ROOT / "img"
DUN_DIR = ROOT / "dun"

IMG_DIR.mkdir(exist_ok=True)
DUN_DIR.mkdir(exist_ok=True)

PORT = int(
    os.environ.get(
        "PORT",
        "8080",
    )
)

CODEX_BIN = os.environ.get(
    "CODEX_BIN",
    "codex",
)

CODEX_MODEL = os.environ.get(
    "CODEX_MODEL",
)

# plot.py needs local PostgreSQL access.
CODEX_SANDBOX = os.environ.get(
    "CODEX_SANDBOX",
    "danger-full-access",
)

MELOYELO_URL = os.environ.get(
    "MELOYELO_URL",
    "ws://127.0.0.1:9009/audiows",
)


app = Bottle()


# ============================================================
# Single-browser application state
# ============================================================

app_socket = None
app_outbox = None

busy = False

current_question = ""
current_text = ""
current_status = "Ready"

latest_chart = None


# ============================================================
# Browser outbound queue
#
# Exactly ONE greenlet writes to the application websocket.
# Everyone else only puts messages onto app_outbox.
# ============================================================

def send_app(msg):
    q = app_outbox

    if q is not None:
        q.put(msg)


def set_status(text):
    global current_status

    current_status = text

    send_app({
        "type": "status",
        "text": text,
    })


def socket_writer(ws, outbox):
    try:
        while True:
            msg = outbox.get()

            ws.send(
                json.dumps(msg)
            )

    except Exception:
        pass


def send_snapshot(outbox):
    chart_url = None

    if latest_chart:
        chart_url = (
            "/app/chart/"
            + latest_chart
        )

    outbox.put({
        "type": "snapshot",
        "busy": busy,
        "question": current_question,
        "text": current_text,
        "status": current_status,
        "chart": chart_url,
    })


# ============================================================
# dun/ watcher
#
# plot.py creates:
#
#   img/foo.png
#   img/foo.json
#   dun/foo.png
#
# The dun marker is created LAST.
#
# Therefore seeing dun/foo.png means the chart and facts
# sidecar are complete.
# ============================================================

def watch_dun():
    global latest_chart

    seen = set()

    existing = [
        p
        for p in DUN_DIR.iterdir()
        if p.is_file()
    ]

    for marker in existing:
        seen.add(marker.name)

    # Preserve the newest existing chart for reconnects
    # after app.py itself is restarted.

    valid = [
        p
        for p in existing
        if (IMG_DIR / p.name).is_file()
    ]

    if valid:
        newest = max(
            valid,
            key=lambda p: p.stat().st_mtime,
        )

        latest_chart = newest.name

    while True:
        try:
            for marker in DUN_DIR.iterdir():

                if not marker.is_file():
                    continue

                name = marker.name

                if name in seen:
                    continue

                image = IMG_DIR / name

                # Marker should guarantee this, but don't
                # announce garbage if somebody manually
                # drops a file into dun/.
                if not image.is_file():
                    continue

                seen.add(name)

                latest_chart = name

                send_app({
                    "type": "chart",
                    "url":
                        "/app/chart/"
                        + name,
                })

        except Exception as exc:
            print(
                "[dun watcher]",
                exc,
                file=sys.stderr,
            )

        gevent.sleep(0.05)


# ============================================================
# Codex app-server bridge
#
# Verified against Codex 0.154.0.
#
# Wire protocol deliberately does NOT add a "jsonrpc" member;
# Codex's generated request schemas are:
#
#   {id, method, params}
#
# Notifications are:
#
#   {method, params?}
# ============================================================

class CodexBridge:

    def __init__(self):
        self.next_id = 1

        self.pending = {}

        # Exactly one greenlet writes to Codex stdin too.
        self.outbox = Queue()

        self.thread_id = None
        self.active_turn_id = None

        self.proc = Popen(
            [
                CODEX_BIN,
                "app-server",
                "--listen",
                "stdio://",
            ],
            cwd=str(ROOT),
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
            text=True,
            bufsize=1,
        )

        gevent.spawn(
            self._write_stdin
        )

        gevent.spawn(
            self._read_stdout
        )

        gevent.spawn(
            self._read_stderr
        )

        self._initialize()


    # --------------------------------------------------------
    # Output to Codex
    # --------------------------------------------------------

    def _write_stdin(self):
        try:
            while True:
                msg = self.outbox.get()

                self.proc.stdin.write(
                    json.dumps(msg)
                    + "\n"
                )

                self.proc.stdin.flush()

        except Exception as exc:
            print(
                "[codex stdin]",
                exc,
                file=sys.stderr,
            )


    def _send(self, msg):
        self.outbox.put(msg)


    # --------------------------------------------------------
    # Request / notification
    # --------------------------------------------------------

    def request(
        self,
        method,
        params=None,
        timeout=60,
    ):
        request_id = self.next_id
        self.next_id += 1

        q = Queue(maxsize=1)

        self.pending[
            request_id
        ] = q

        msg = {
            "id": request_id,
            "method": method,
        }

        if params is not None:
            msg["params"] = params

        self._send(msg)

        try:
            reply = q.get(
                timeout=timeout
            )

        except Empty:
            self.pending.pop(
                request_id,
                None,
            )

            raise RuntimeError(
                f"Codex request timed out: {method}"
            )

        if "error" in reply:
            raise RuntimeError(
                f"Codex {method}: "
                f"{reply['error']}"
            )

        return reply.get(
            "result",
            {}
        )


    def notify(
        self,
        method,
        params=None,
    ):
        msg = {
            "method": method,
        }

        if params is not None:
            msg["params"] = params

        self._send(msg)


    # --------------------------------------------------------
    # Initialize
    #
    # 0.154.0:
    #
    # initialize
    #   ->
    # initialized
    # --------------------------------------------------------

    def _initialize(self):

        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "ivie",
                    "title":
                        "IViE ERCOT Analyst",
                    "version": "1.0",
                },

                "capabilities": {
                    "experimentalApi": False,
                    "requestAttestation": False,
                },
            },
        )

        print(
            "Codex:",
            result.get(
                "userAgent",
                "initialized",
            ),
        )

        # 0.154.0 Initialized notification
        # has no params field.
        self.notify(
            "initialized"
        )

        self._start_thread()


    # --------------------------------------------------------
    # Thread
    # --------------------------------------------------------

    def _start_thread(self):

        params = {
            "cwd": str(ROOT),

            "approvalPolicy":
                "never",

            "sandbox":
                CODEX_SANDBOX,

            "ephemeral":
                True,

            "serviceName":
                "ivie",
        }

        # Important:
        #
        # DO NOT add a "config" field here.
        #
        # Codex 0.154.0 has a known app-server bug where
        # per-thread config overrides can make turns hang.

        if CODEX_MODEL:
            params["model"] = (
                CODEX_MODEL
            )

        result = self.request(
            "thread/start",
            params,
        )

        self.thread_id = (
            result["thread"]["id"]
        )

        print(
            "Codex thread:",
            self.thread_id,
        )

        sources = result.get(
            "instructionSources",
            [],
        )

        if sources:
            print(
                "Codex instructions:"
            )

            for source in sources:
                print(
                    " ",
                    source,
                )


    # --------------------------------------------------------
    # Turn
    # --------------------------------------------------------

    def start_turn(self, text):
        global busy
        global current_question
        global current_text

        if busy:
            send_app({
                "type": "error",
                "text":
                    "Still answering the previous question.",
            })

            return

        busy = True

        current_question = text
        current_text = ""

        self.active_turn_id = None

        set_status(
            "Thinking…"
        )

        try:
            result = self.request(
                "turn/start",
                {
                    "threadId":
                        self.thread_id,

                    "input": [
                        {
                            "type": "text",
                            "text": text,

                            # Required by the 0.154.0
                            # UserInput text schema.
                            "text_elements": [],
                        }
                    ],
                },
            )

            self.active_turn_id = (
                result["turn"]["id"]
            )

        except Exception as exc:
            busy = False

            set_status(
                "Error"
            )

            send_app({
                "type": "error",
                "text": str(exc),
            })


    # --------------------------------------------------------
    # Read Codex stdout
    # --------------------------------------------------------

    def _read_stdout(self):

        for line in self.proc.stdout:

            line = line.strip()

            if not line:
                continue

            try:
                msg = json.loads(
                    line
                )

            except json.JSONDecodeError:
                print(
                    "[bad codex json]",
                    line,
                    file=sys.stderr,
                )

                continue


            # ----------------------------------------
            # Response to one of our requests
            # ----------------------------------------

            if (
                "id" in msg
                and
                "method" not in msg
            ):
                q = self.pending.pop(
                    msg["id"],
                    None,
                )

                if q:
                    q.put(msg)

                continue


            # ----------------------------------------
            # Server -> client REQUEST
            # ----------------------------------------

            if (
                "id" in msg
                and
                "method" in msg
            ):
                self._handle_server_request(
                    msg
                )

                continue


            # ----------------------------------------
            # Notification
            # ----------------------------------------

            if "method" in msg:
                self._handle_notification(
                    msg
                )

        self._server_died()


    # --------------------------------------------------------
    # Codex stderr
    # --------------------------------------------------------

    def _read_stderr(self):

        for line in self.proc.stderr:
            print(
                "[codex]",
                line.rstrip(),
                file=sys.stderr,
            )


    # --------------------------------------------------------
    # Requests FROM Codex
    #
    # With approvalPolicy=never and this demo's tool usage,
    # none are expected.
    #
    # Explicitly reject rather than leave Codex waiting forever.
    # --------------------------------------------------------

    def _handle_server_request(
        self,
        msg,
    ):

        method = msg.get(
            "method",
            "unknown",
        )

        print(
            "[codex request rejected]",
            method,
            file=sys.stderr,
        )

        self._send({
            "id": msg["id"],

            "error": {
                "code": -32601,
                "message":
                    (
                        "IViE does not handle "
                        f"server request {method}"
                    ),
            },
        })


    # --------------------------------------------------------
    # Notifications
    # --------------------------------------------------------

    def _handle_notification(
        self,
        msg,
    ):
        global busy
        global current_text

        method = msg.get(
            "method"
        )

        params = (
            msg.get(
                "params"
            )
            or {}
        )


        # Ignore notifications for other threads.
        thread_id = params.get(
            "threadId"
        )

        if (
            thread_id
            and
            self.thread_id
            and
            thread_id
            !=
            self.thread_id
        ):
            return


        # ----------------------------------------
        # Turn started
        # ----------------------------------------

        if method == "turn/started":

            turn = (
                params.get("turn")
                or {}
            )

            turn_id = turn.get("id")

            if turn_id:
                self.active_turn_id = (
                    turn_id
                )

            return


        # ----------------------------------------
        # Streaming assistant text
        #
        # 0.154.0:
        #
        # params:
        #   threadId
        #   turnId
        #   itemId
        #   delta
        # ----------------------------------------

        if (
            method
            ==
            "item/agentMessage/delta"
        ):

            delta = params.get(
                "delta",
                "",
            )

            if not delta:
                return

            current_text += delta

            send_app({
                "type": "delta",
                "text": delta,
            })

            set_status(
                "Responding…"
            )

            return


        # ----------------------------------------
        # Tool execution starting
        # ----------------------------------------

        if method == "item/started":

            item = (
                params.get("item")
                or {}
            )

            if (
                item.get("type")
                ==
                "commandExecution"
            ):
                set_status(
                    "Analyzing ERCOT data…"
                )

            return


        # ----------------------------------------
        # Fallback final agent message
        #
        # Normally deltas have already populated
        # current_text.
        # ----------------------------------------

        if method == "item/completed":

            item = (
                params.get("item")
                or {}
            )

            if (
                item.get("type")
                ==
                "agentMessage"
                and
                not current_text
            ):
                text = item.get(
                    "text",
                    "",
                )

                if text:
                    current_text = text

                    send_app({
                        "type": "delta",
                        "text": text,
                    })

            return


        # ----------------------------------------
        # Turn finished
        # ----------------------------------------

        if method == "turn/completed":

            turn = (
                params.get("turn")
                or {}
            )

            status = turn.get(
                "status",
                "completed",
            )

            error = turn.get(
                "error"
            )

            busy = False
            self.active_turn_id = None

            if error:
                set_status(
                    "Error"
                )

                send_app({
                    "type": "error",
                    "text": str(error),
                })

            else:
                current_status_value = (
                    "Ready"
                )

                set_status(
                    current_status_value
                )

                send_app({
                    "type": "done",
                    "status": status,
                })

            return


        # ----------------------------------------
        # App-server error notification
        # ----------------------------------------

        if method == "error":

            error = (
                params.get("error")
                or params
            )

            print(
                "[codex error]",
                error,
                file=sys.stderr,
            )

            send_app({
                "type": "error",
                "text": str(error),
            })

            return


        # ----------------------------------------
        # Warning
        # ----------------------------------------

        if method == "warning":

            message = (
                params.get("message")
                or str(params)
            )

            print(
                "[codex warning]",
                message,
                file=sys.stderr,
            )

            return


    # --------------------------------------------------------
    # Codex exited
    # --------------------------------------------------------

    def _server_died(self):
        global busy

        busy = False

        set_status(
            "Codex stopped"
        )

        send_app({
            "type": "error",
            "text":
                "Codex app-server exited.",
        })

        error_reply = {
            "error": {
                "message":
                    "Codex app-server exited"
            }
        }

        for q in (
            self.pending.values()
        ):
            q.put(
                error_reply
            )

        self.pending.clear()


# ============================================================
# Start Codex
# ============================================================

codex = CodexBridge()


# ============================================================
# Browser application WebSocket
#
# Exactly one browser is supported.
#
# New connection replaces old connection.
# ============================================================

@app.route("/app/ws")
def application_websocket():
    global app_socket
    global app_outbox

    ws = request.environ.get(
        "wsgi.websocket"
    )

    if not ws:
        abort(
            400,
            "Expected WebSocket",
        )


    # New queue for every browser connection.
    #
    # This automatically prevents stale queued messages
    # from an old connection leaking into a new one.

    outbox = Queue()


    old_socket = app_socket

    app_socket = ws
    app_outbox = outbox


    writer = gevent.spawn(
        socket_writer,
        ws,
        outbox,
    )


    if (
        old_socket
        and
        old_socket is not ws
    ):
        try:
            old_socket.close()
        except Exception:
            pass


    # Tell a newly connected/reconnected browser
    # where the application currently stands.

    send_snapshot(
        outbox
    )


    try:
        while True:

            raw = ws.receive()

            if raw is None:
                break


            if isinstance(
                raw,
                bytes,
            ):
                continue


            try:
                msg = json.loads(
                    raw
                )

            except json.JSONDecodeError:

                outbox.put({
                    "type": "error",
                    "text": "Invalid JSON",
                })

                continue


            msg_type = msg.get(
                "type"
            )


            # ------------------------------------
            # Ping
            # ------------------------------------

            if msg_type == "ping":

                outbox.put({
                    "type": "pong",
                })

                continue


            # ------------------------------------
            # User question
            # ------------------------------------

            if msg_type == "ask":

                text = str(
                    msg.get(
                        "text",
                        "",
                    )
                ).strip()


                if not text:

                    outbox.put({
                        "type": "error",
                        "text":
                            "Question is empty.",
                    })

                    continue


                if busy:

                    outbox.put({
                        "type": "error",
                        "text":
                            (
                                "Still answering "
                                "the previous question."
                            ),
                    })

                    continue


                # Do not block the websocket receiver
                # while turn/start waits for Codex.

                gevent.spawn(
                    codex.start_turn,
                    text,
                )

                continue


            # ------------------------------------
            # Unknown message
            # ------------------------------------

            outbox.put({
                "type": "error",
                "text":
                    (
                        "Unknown message type: "
                        + str(msg_type)
                    ),
            })


    except Exception as exc:

        print(
            "[app websocket]",
            exc,
            file=sys.stderr,
        )


    finally:

        # An OLD connection must never clear
        # globals belonging to a newer replacement.

        if app_socket is ws:
            app_socket = None
            app_outbox = None

        writer.kill()

        try:
            ws.close()
        except Exception:
            pass


# ============================================================
# MeloYelo proxy
#
# Browser:
#     /audiows
#
# Upstream:
#     ws://127.0.0.1:9009/audiows
#
# Browser -> text microchunks
# MeloYelo -> PCM binary
#
# Timing text frames are discarded.
# EOF is forwarded.
# ============================================================

@app.route("/audiows")
def audio_websocket():

    browser = request.environ.get(
        "wsgi.websocket"
    )

    if not browser:

        abort(
            400,
            "Expected WebSocket",
        )


    try:
        upstream = (
            websocket.create_connection(
                MELOYELO_URL,
                timeout=10,
            )
        )

        upstream.settimeout(
            None
        )

    except Exception as exc:

        print(
            "[MeloYelo connect]",
            exc,
            file=sys.stderr,
        )

        try:
            browser.close()
        except Exception:
            pass

        return


    def browser_to_tts():

        try:
            while True:

                data = (
                    browser.receive()
                )

                if data is None:
                    return

                # Browser sends text chunks.
                if isinstance(
                    data,
                    bytes,
                ):
                    continue

                upstream.send(
                    data
                )

        except Exception:
            pass


    def tts_to_browser():

        try:
            while True:

                data = (
                    upstream.recv()
                )

                if data is None:
                    return


                # MeloYelo text frames contain
                # timing data or EOF.
                #
                # We do not use timing data.

                if isinstance(
                    data,
                    str,
                ):

                    if (
                        data.strip()
                        ==
                        "EOF"
                    ):
                        browser.send(
                            "EOF"
                        )

                    continue


                # PCM binary.
                browser.send(
                    data
                )

        except Exception:
            pass


    a = gevent.spawn(
        browser_to_tts
    )

    b = gevent.spawn(
        tts_to_browser
    )


    # Stop the pair assoon as either side dies.

    gevent.wait(
        [a, b],
        count=1,
    )


    a.kill()
    b.kill()


    try:
        upstream.close()
    except Exception:
        pass


    try:
        browser.close()
    except Exception:
        pass


# ============================================================
# HTTP
# ============================================================

@app.get("/")
def landing():
    return static_file(
        "index.html",
        root=str(ROOT),
    )


@app.get("/app")
def app_redirect():
    redirect(
        "/app/"
    )


@app.get("/app/")
def app_index():
    return static_file(
        "index.html",
        root=str(APP_DIR),
    )


@app.get(
    "/app/chart/<filename>"
)
def chart(filename):

    # Intentionally no :path converter.
    # A chart is exactly one filename.

    return static_file(
        filename,
        root=str(IMG_DIR),
        mimetype="image/png",
    )


@app.get("/health")
def health():

    return {
        "ok": True,

        "browser":
            app_socket is not None,

        "busy":
            busy,

        "status":
            current_status,

        "chart":
            latest_chart,

        "codex_thread":
            codex.thread_id,

        "codex_alive":
            codex.proc.poll()
            is None,
    }


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    # Watcher runs independently from both browser and Codex.
    gevent.spawn(
        watch_dun
    )

    print()
    print(
        f"IViE:     http://localhost:{PORT}/app/"
    )

    print(
        f"App WS:   ws://localhost:{PORT}/app/ws"
    )

    print(
        f"TTS WS:   ws://localhost:{PORT}/audiows"
    )

    print(
        f"MeloYelo: {MELOYELO_URL}"
    )

    print(
        f"Codex:    {CODEX_BIN} app-server"
    )

    print(
        f"Sandbox:  {CODEX_SANDBOX}"
    )

    print()

    WebSocketServer(
        (
            "0.0.0.0",
            PORT,
        ),
        app,
    ).serve_forever()
