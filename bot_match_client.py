#!/usr/bin/env python3
"""FlipSight bot-match connector. Python 3.9+, standard library only, Windows/macOS/Linux.

Credentials stay in FS_BOT_TOKEN. Engine modes:
  --engine CMD   a long-running JSON-lines engine, one process per game: read a request line,
                 write one {"move": index} line, repeat (do not wait for end of input)
  --nboard CMD   a long-running engine that speaks the NBoard protocol (one engine per leg)
  --edax PATH    Edax 4.6 executable over xboard: Edax runs its own game clock (the connector sends both
                 clocks before every search) and keeps its game and hash table through the leg
                 --edax-protocol nboard keeps the older NBoard connection (per-turn time set by the connector)
  --egaroucid PATH  Egaroucid for Console 7.8.2; native setboard/settime/go commands
                 --egaroucid-level N (1..60) plays a fixed level instead (no settime)
  --engine-hash-mb MB sets the Edax/Egaroucid hash table (largest power-of-two size not above MB) and
  --engine-ponder on lets Edax (xboard) and Egaroucid (-ponder) think in the opponent's time; both report the
  actual setting. With other engines both options only report the setting.
The connector listens to the event stream (/flipsight_api/bot-stream) and reacts to opponent moves
immediately. Since v1.5.0 (FlipSight bot API v2: agent_poll is removed on
2026-11-22 00:00 UTC+9) the state comes only from the stream and the connector never calls agent_poll: right after
hello the server sends this bot's state (type "state": the bot and its ongoing games), and every match event carries
this bot's own view (view "agent"), so the engine starts and its move is sent at once. The environment goes with
agent_environment at start and when the engine reports new values. When an update looks missed the connector
reconnects the stream (every connection sends the full state again): its own move not echoed within 2s, the state
missing or an event without a view, and a board waiting on the opponent with no news of that match for the opponent's
whole clock plus 5s (once per match; a quiet board while the opponent thinks is normal: the 1.5.0 published at 11:33
UTC+9 on 2026-11-20 reconnected after 2-4s of quiet near the end of a game, fixed the same day), at most 5
voluntary reconnects in 10 minutes. A stream that stays connected but sends nothing (not even the 10s ping) for 15s is closed and opened
again (v1.4.9). The server itself re-sends updates that got lost on the way to the stream. --no-stream (polling only)
is gone. Stream drops, reconnects, silences, late events and turns skipped for lack of time are logged on stderr.
A move that hits a temporary error (HTTP 429 or 5xx, timeout, connection error) is sent again within 0.2-0.5s while
the game clock allows, honouring Retry-After; it is dropped only when the server state has moved on.
v1.5.1: a move POST waits at most a third of that leg's clock (1.5-8s), and after a timeout both the HTTPS connection
and the stream are opened again (a changed network path leaves the old ones dead). A move refused with HTTP 409 (the
server moved on, often because the first try arrived) is dropped on its own while the other boards go on, and the
stream is reconnected only if that match has no news within 1s. Reconnects asked for are merged: none while one is
under way or within 3s of the last one.
v1.5.2: --save-games DIR keeps every finished game: the server's game record (format 1, one JSON line per game, the
same object as the record of a finished match event and each line of agent_games) goes to DIR/games-YYYY-MM.ndjson.
Games that ended while the connector was stopped or the stream was down, and games against people whose record was
not ready yet, are filled with agent_games in a background thread on its own connection (from the newest saved game;
an empty folder starts from now). A game that changes later (a technical void) is written again; the last line wins.
v1.5.3: the engine's evaluation goes with each move in bot matches (agent_move "eval", discs from the mover's side,
within the game's playable squares; never in games against people): "eval" in a JSON reply, the NBoard score, the
score of Edax's own xboard search line for the move it plays, and the value Egaroucid prints after the move when it
is started with -showvalue. Moves and engine time are unchanged.
v1.5.4: the server's last line before it closes the stream (type "closing", 2026-11-22) is read. overflow (lines piled
up and were dropped) and server_shutdown reconnect at once like a resync; the state comes again after hello, and tries
that fail while the server restarts use the usual backoff. replaced means another connector with this bot's token
opened a new stream: a warning is logged and the connector waits 30-60s before connecting again, so two copies no
longer close each other every few seconds; from the 3rd time in 10 minutes the wait doubles each time up to 7.5-15
minutes, and a connection that stays up 10 minutes clears the count. Unknown reasons reconnect as any other close.
v1.6.0: the stream request says Accept-Encoding: gzip. A compressed stream (Content-Encoding: gzip, the server flushes
every line) is read with read1, inflated and split into lines here, so each line is still handled the moment it
arrives; without that header the stream is read as before, and the 15s silence rule is the same. The stream is opened
with ?state=1&moves=1: a server that knows moves=1 says "moves": true in hello and, during standard 8x8 bot matches,
sends each new move as a type "move" line (board, position in that board's history, coordinates a1..h8 or pass, turn,
revision and clocks) instead of the whole match. The connector plays those moves on the last known board by the rules,
rebuilds that match's view (history, board, legal moves, discs, turn, revision, clocks) and goes on exactly as with a
full match event. Moves already known are skipped; a gap, an unknown match or board, a move that is not legal on the
known board or a game that is not standard 8x8 reconnects the stream to resync (within the usual limits), never a
guess. The state, the first and the last event of a match, other changes, games against people and variants still
come as full events, and a server without moves=1 keeps sending full events.
Such a stream (2026-11-28) also leaves legal_moves out of every event, so the connector computes them for every game
type by the server's rules (game_legal_moves: 10x10 grand and octagon, octagon's blocked squares, classic reversi's
centre opening and 32-disc limit); a server value, when one comes, is still used as before.
"""
import argparse
import email.utils
import glob
import gzip
import concurrent.futures
import http.client
import io
import json
import math
import os
import platform
import queue
import random
import shlex
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

VERSION = '1.6.0'
WINDOWS = os.name == 'nt'
DIRECTIONS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
# Temporary errors during a game: the first FAST_RETRIES tries wait 0.2-0.5s, later ones back off 2, 4, ... 30s.
FAST_RETRIES = 4
# Stream liveness and extra safety polls (v1.4.9, see PollPlanner). The server pings every 10s, so this long without
# any line means the stream is stuck even when its connection looks open; it is also the stream's socket timeout.
STREAM_SILENCE_SEC = 15
ECHO_WAIT_SEC = 1.0              # the stream echoes this bot's own move within about 0.1s
LOW_CLOCK_SEC = 30               # below this on this bot's clock, poll every min(10, max(1, remaining / 4))s
LOW_CLOCK_POLLS_PER_MATCH = 12   # v1.5.0 (60 before): after this many only the regular safety poll is left for that match
EVENT_LATE_MS = 2000             # a stream line this much later than the server sent it is logged
# v1.4.10: safety polls while the stream is up (the stream carries every state) and how long to wait for the state
# the server sends after hello on a ?state=1 stream before polling instead.
# v1.5.0 (bot API v2, no agent_poll): with the stream, resyncing means reconnecting it. Voluntary reconnects stay within
# RESYNC_BUDGET per RESYNC_WINDOW_SEC (the server allows 10 connections in 10 minutes); the server re-sends lost updates.
RESYNC_BUDGET = 5
RESYNC_WINDOW_SEC = 600
ECHO_WAIT_STREAM_SEC = 2.0
# v1.5.0 (fixed 2026-11-20 UTC+9): a board waiting on the opponent is quiet while the opponent thinks; that alone is no
# reason to reconnect (the 11:33 build reconnected after 2-4s of quiet near the end of a 1-minute game, up to 3 times per match). Only once the
# opponent's whole clock has run out plus OPP_CLOCK_GRACE_SEC without any news of that match (the server settles a
# timeout within about a second and sends it) is an update surely lost; then reconnect once per match.
OPP_CLOCK_GRACE_SEC = 5.0
OPP_CLOCK_RESYNCS_PER_MATCH = 1
# v1.5.1 (2026-11-20, bot 32 lost matches 74915 and 74928 when its network path changed for a few seconds):
#  - a move refused with HTTP 409 (the server moved on, often because the first try of the same move did arrive) is
#    dropped on its own: the round goes on with the other boards, the error backoff is not raised, and the stream is
#    reconnected only if no news of that match came within CONFLICT_NEWS_SEC;
#  - reconnects asked for are merged: none while one is under way or within RESYNC_MERGE_SEC of the last one;
#  - a move POST waits at most a third of that leg's clock (MOVE_POST_TIMEOUT_MIN..MAX); after a timeout the HTTPS
#    connection is new (API.post closes it) and the stream is reconnected too, as both ran on the dead path.
CONFLICT_NEWS_SEC = 1.0
RESYNC_MERGE_SEC = 3.0
# v1.5.4: the server sends {"type":"closing","reason":...} as the last line before it closes the stream (2026-11-22).
# "overflow" (64 unsent lines, dropped) and "server_shutdown" reconnect at once like a resync. "replaced" means another
# connector with this bot's token opened a stream and the server closed this one: reconnecting at once would close that
# one in turn, and two copies would keep closing each other (and count against the 10 connections in 10 minutes). So
# wait REPLACED_WAIT_SEC to twice that; from the REPLACED_REPEAT-th time within REPLACED_WINDOW_SEC the wait doubles each
# time, at most REPLACED_WAIT_MAX_SEC. The connector never stops for good: when the other copy goes away it is back
# within that wait. A connection that stays up REPLACED_CLEAR_SEC clears the count. Unknown reasons reconnect as before.
REPLACED_WAIT_SEC = 30.0
REPLACED_REPEAT = 3
REPLACED_WINDOW_SEC = 600
REPLACED_WAIT_MAX_SEC = 900.0
REPLACED_CLEAR_SEC = 600
# v1.6.0 (server 2026-11-28): a gzip stream is read in pieces of at most STREAM_READ_BYTES (read1 returns whatever has
# arrived, it never waits for that much), and one inflated line may not exceed STREAM_LINE_MAX: a broken stream cannot
# fill the memory (it is dropped and connected again like any other failed connection).
STREAM_READ_BYTES = 65536
STREAM_LINE_MAX = 16 * 1024 * 1024
MOVE_POST_TIMEOUT_MIN, MOVE_POST_TIMEOUT_MAX = 1.5, 8.0
ENV_REPORT_EVERY_SEC = 30
SAFETY_POLL_GAME_SEC = 60   # v1.5.0 (bot API v2): 60s; the waiting-leg polls still cover a quiet board after 10s
SAFETY_POLL_IDLE_SEC = 60
STATE_WAIT_SEC = 3.0
# v1.5.2 --save-games: agent_games pages (100 records stay well under the 1 MB response limit even uncompressed), the
# server's limit of one agent_games call per bot every 5s, and how long after a finished game without a record (a game
# against a person whose record was not ready yet) to fill again.
GAMES_FILL_MAX = 100
GAMES_FILL_GAP_SEC = 5.5
GAMES_LATE_RECORD_SEC = 15.0
GAMES_FILL_TRIES = 3


def log(message):
    """One diagnostic line on stderr with the local time (lines added in v1.4.9; older messages keep their form).
    Callers never pass the token or a response body."""
    now = time.time()
    print('%s.%03d %s' % (time.strftime('%H:%M:%S', time.localtime(now)), int(now * 1000) % 1000, message),
          file=sys.stderr, flush=True)


class RuntimeLimit(Exception):
    pass


class APIError(RuntimeError):
    """A failed API call. status is the HTTP status (None for network errors). transient marks errors worth sending
    the same request again right away: HTTP 429 and 5xx, timeouts and connection errors. retry_after is the server's
    Retry-After in seconds, if any."""

    def __init__(self, message, status=None, transient=False, retry_after=None):
        super().__init__(message)
        self.status, self.transient, self.retry_after = status, transient, retry_after


def retry_after_seconds(headers):
    """Retry-After as seconds (delta or HTTP date), capped at 120s; None when absent or unreadable."""
    value = headers.get('Retry-After') if headers is not None else None
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = email.utils.parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds):
        return None
    return min(120.0, max(0.0, seconds))


def http_error(status, headers):
    return APIError('HTTP %d' % status, status, status == 429 or status >= 500, retry_after_seconds(headers))


def network_error(error):
    return APIError('%s: %s' % (type(error).__name__, error), None, True)


def describe_error(error, token=''):
    """One log line for an error: type, message and HTTP status, never the token or a response body."""
    text = ' '.join(str(error).split())
    if token:
        text = text.replace(token, '[token]')
    if not isinstance(error, APIError):
        text = '%s: %s' % (type(error).__name__, text) if text else type(error).__name__
    if getattr(error, 'retry_after', None) is not None:
        text += ', Retry-After %.1fs' % error.retry_after
    return text[:300]


def retry_delay(tries, retry_after=None, clock_left=None):
    """Seconds to wait before sending the same request again, or None when the game clock has run out.
    The first FAST_RETRIES tries wait 0.2-0.5s, later ones back off as before, and Retry-After is followed.
    With a game clock (clock_left seconds) no wait reaches it: waiting that long would lose on time for sure,
    so the clock wins over Retry-After and the wait shrinks so one more try still lands in time."""
    if tries <= FAST_RETRIES:
        base = random.uniform(.2, .5)
    else:
        base = min(30, 2 ** min(tries - FAST_RETRIES, 5)) + random.random()
    delay = base if retry_after is None else max(base, retry_after)
    if clock_left is not None:
        if clock_left < .1:
            return None
        if delay >= clock_left:
            delay = min(base, clock_left / 2)
    return delay


def has_running_match(payload):
    try:
        return any(isinstance(m, dict) and m.get('status') == 'running' for m in payload.get('matches', []))
    except AttributeError:
        return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('redirect refused')


def check_https(url, what):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(what + ' must be a credential-free HTTPS URL without query or fragment')
    return parsed


class API:
    """POSTs to the bot API. Keeps one HTTPS connection open (keep-alive) so a move does not pay a new TCP and TLS
    handshake every time (measured about 20-34 ms per request from a home PC). Behind an HTTPS proxy it uses urllib,
    which reads the proxy settings, and opens a connection per request as before."""

    def __init__(self, url, token):
        parts = check_https(url, 'API')
        if not token or '\r' in token or '\n' in token:
            raise ValueError('FS_BOT_TOKEN is missing or invalid')
        self.url, self.token = url, token
        self.opener = urllib.request.build_opener(NoRedirect())
        self.host, self.port, self.path = parts.hostname, parts.port or 443, parts.path or '/'
        self.keep_alive = not urllib.request.getproxies().get('https')
        self.context = ssl.create_default_context()
        self.connection = None
        self.last_reused = False
        self.lock = threading.Lock()

    def close(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass
            self.connection = None

    def post(self, body, headers, timeout=8.0):
        """One POST on the kept connection, waiting at most timeout seconds for each read (v1.5.1: per call).
        Returns (status, body, response headers)."""
        for attempt in (0, 1):
            reused = self.connection is not None
            if not reused:
                self.connection = http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=self.context)
            self.connection.timeout = timeout
            if self.connection.sock is not None:
                self.connection.sock.settimeout(timeout)
            try:
                self.connection.request('POST', self.path, body=body, headers=headers)
                response = self.connection.getresponse()
                data = response.read(1024 * 1024 + 1)
                if not response.isclosed() or response.will_close:
                    self.close()   # unread rest or server closes: start fresh next time
                self.last_reused = reused
                return response.status, data, response.headers
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close()
                # The server closed an idle kept connection: send once more on a new one. A move cannot apply
                # twice (the server checks the expected revision); other errors and timeouts are not retried.
                if not reused or attempt:
                    raise
            except Exception:
                self.close()
                raise
        raise RuntimeError('unreachable')

    def call(self, action, timeout=8.0, **fields):
        body = self.send(action, timeout, 'application/json', fields)[0]
        data = json.loads(body)
        if not isinstance(data, dict) or data.get('ok') is not True:
            raise RuntimeError('API rejected request')
        return data

    def games(self, timeout=20.0, **fields):
        """v1.5.2: agent_games. The answer is NDJSON, one game record per line (gzip when the server compresses it)."""
        body, headers = self.send('agent_games', timeout, 'application/x-ndjson', fields, gzip_ok=True)
        if (headers.get('Content-Encoding') or '').lower() == 'gzip':
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as packed:
                body = packed.read(16 * 1024 * 1024 + 1)
            if len(body) > 16 * 1024 * 1024:
                raise RuntimeError('response too large')
        records = []
        for line in body.splitlines():
            if line.strip():
                record = json.loads(line)
                if isinstance(record, dict):
                    records.append(record)
        return records

    def send(self, action, timeout, accept, fields, gzip_ok=False):
        """One authenticated POST; returns (body, response headers). Errors as in call()."""
        body = json.dumps(dict(action=action, **fields)).encode()
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json', 'Accept': accept,
                   'User-Agent': 'Python-urllib/%d.%d' % sys.version_info[:2]}
        if gzip_ok:
            headers['Accept-Encoding'] = 'gzip'
        response_headers = {}
        if self.keep_alive:
            try:
                with self.lock:
                    status, body, response_headers = self.post(body, headers, timeout)
            except (OSError, http.client.HTTPException) as error:
                raise network_error(error) from None
            # Never log response bodies: a proxy could echo the authorization header.
            if status in (401, 403):
                raise PermissionError('authentication denied')
            if 300 <= status < 400:
                raise RuntimeError('redirect refused')
            if status >= 400:
                raise http_error(status, response_headers)
        else:
            request = urllib.request.Request(self.url, data=body, headers=headers, method='POST')
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    body = response.read(1024 * 1024 + 1)
                    response_headers = response.headers
            except urllib.error.HTTPError as error:
                # Never log response bodies: a proxy could echo the authorization header.
                if error.code in (401, 403):
                    raise PermissionError('authentication denied') from None
                raise http_error(error.code, error.headers) from None
            except (OSError, http.client.HTTPException) as error:
                raise network_error(error) from None
            self.last_reused = False
        if len(body) > 1024 * 1024:
            raise RuntimeError('response too large')
        return body, response_headers


def game_record(match):
    """v1.5.2: the server's game record carried by the last event of a finished match (format 1), or None."""
    record = match.get('record') if isinstance(match, dict) else None
    if (isinstance(record, dict) and record.get('format') == 1 and record.get('kind') in ('bot', 'human')
            and type(record.get('id')) is int and type(record.get('updated_ms')) is int):
        return record
    return None


class GameSaver:
    """v1.5.2 --save-games: one JSON line per finished game in DIR/games-YYYY-MM.ndjson (month of updated_ms, local time).

    A game is keyed by kind and id. A record already saved with the same or a newer updated_ms is skipped; a newer one
    (a game changed later, such as a technical void) is written again, so the last line of a game wins. Lines of the two
    newest files are read at start. Used by the main loop and the filling thread."""

    def __init__(self, folder):
        self.folder = folder
        self.lock = threading.Lock()
        self.saved = {}
        self.newest = 0
        os.makedirs(folder, exist_ok=True)
        files = sorted(glob.glob(os.path.join(glob.escape(folder), 'games-*.ndjson')))
        if files:
            # A crash in the middle of a write leaves the last line without its newline: end it so the next record
            # starts on its own line (the cut line is skipped below and its game comes again from agent_games).
            with open(files[-1], 'rb+') as last:
                last.seek(0, os.SEEK_END)
                if last.tell():
                    last.seek(-1, os.SEEK_END)
                    if last.read(1) != b'\n':
                        last.write(b'\n')
        for path in files[-2:]:
            with open(path, encoding='utf-8') as lines:
                for line in lines:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue   # a line cut short by a crash: the record comes again from agent_games
                    if isinstance(record, dict) and type(record.get('updated_ms')) is int:
                        key = (record.get('kind'), record.get('id'))
                        self.saved[key] = max(self.saved.get(key, 0), record['updated_ms'])
                        self.newest = max(self.newest, record['updated_ms'])

    def save(self, record):
        """Write the record unless this version is saved already. Returns True when a line was written."""
        key, updated = (record['kind'], record['id']), record['updated_ms']
        line = json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n'
        path = os.path.join(self.folder, time.strftime('games-%Y-%m.ndjson', time.localtime(updated / 1000)))
        with self.lock:
            if self.saved.get(key, -1) >= updated:
                return False
            with open(path, 'a', encoding='utf-8', newline='\n') as out:
                out.write(line)
            self.saved[key] = updated
            self.newest = max(self.newest, updated)
        return True


class GameFiller(threading.Thread):
    """v1.5.2 --save-games: fills games the stream did not bring (the connector was stopped or the stream was down, or a
    game against a person ended before its record was ready) with agent_games, from the newest saved game. Runs on its
    own HTTPS connection so a fill never delays a move, and keeps the server's limit of one call per 5s."""

    def __init__(self, url, token, saver, start_ms):
        super().__init__(name='game-filler', daemon=True)
        self.api = API(url, token)
        self.saver, self.start_ms = saver, start_ms
        self.lock = threading.Lock()
        self.due = None
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.last_call = -1e9

    def request(self, delay=0.0):
        with self.lock:
            at = time.monotonic() + delay
            self.due = at if self.due is None else min(self.due, at)
        self.wake.set()

    def run(self):
        while not self.stopping.is_set():
            with self.lock:
                due = self.due
            if due is None or time.monotonic() < due:
                self.wake.wait(1.0 if due is None else max(0.05, due - time.monotonic()))
                self.wake.clear()
                continue
            with self.lock:
                self.due = None
            try:
                self.fill()
            except PermissionError:
                log('Game records: bot credential rejected; filling stops.')
                return
            except Exception as error:   # never let saving games stop the connector
                log('Game records: fill failed (%s).' % describe_error(error, self.api.token))
        self.api.close()

    def fill(self):
        since = self.saver.newest or self.start_ms
        written, tries = 0, 0
        while not self.stopping.is_set():
            gap = self.last_call + GAMES_FILL_GAP_SEC - time.monotonic()
            if gap > 0 and self.stopping.wait(gap):
                return
            self.last_call = time.monotonic()
            try:
                records = self.api.games(since=since, max=GAMES_FILL_MAX)
            except PermissionError:
                raise
            except Exception as error:
                tries += 1
                if tries >= GAMES_FILL_TRIES:
                    log('Game records: agent_games failed %d times (%s); trying again at the next reconnect or finished game.'
                        % (tries, describe_error(error, self.api.token)))
                    return
                continue
            new = 0
            for record in records:
                record = game_record({'record': record})
                if record is not None and self.saver.save(record):
                    new += 1
            written += new
            newest = max([r['updated_ms'] for r in records if isinstance(r, dict) and type(r.get('updated_ms')) is int] or [since])
            # since is inclusive (the last saved game comes again): stop when a page brings nothing new or is not full.
            if new == 0 or len(records) < GAMES_FILL_MAX or newest <= since:
                break
            since = newest
        if written:
            log('Game records: filled %d game(s) with agent_games.' % written)


class WaitingForState(Exception):
    """v1.5.0: the stream has not sent the state yet (no poll to fall back on)."""


def closing_reason(value):
    """v1.5.4: the reason of the server's closing line as short plain text for the log ('?' when missing or odd)."""
    text = value if isinstance(value, str) else ''
    return ''.join(c for c in text[:40] if c.isascii() and (c.isalnum() or c in '_-.')) or '?'


def stream_lines(response):
    """v1.6.0: the lines of a stream response. Without Content-Encoding the response itself is read line by line, as
    before. With gzip see gzip_lines. Another encoding (never asked for) fails the connection."""
    encoding = (response.headers.get('Content-Encoding') or '').strip().lower()
    if encoding in ('', 'identity'):
        return response
    if encoding != 'gzip':
        raise ValueError('unsupported stream encoding')
    return gzip_lines(response)


def gzip_lines(response):
    """v1.6.0: lines of a gzip stream as they arrive. The server compresses every line and flushes it at once
    (Z_SYNC_FLUSH), so read1 (one read: whatever has arrived, never waiting for more) brings whole lines; they are
    inflated and split here and each is yielded at once. The socket timeout (STREAM_SILENCE_SEC) still ends a silent
    read. A line longer than STREAM_LINE_MAX or a broken gzip stream fails the connection. A chunked answer cut off
    without its last chunk ends like a plain stream does (the line reader ends quietly there too)."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    pending = bytearray()
    while not inflater.eof:
        try:
            data = response.read1(STREAM_READ_BYTES)
        except http.client.IncompleteRead:
            data = b''
        if not data:
            pending += inflater.flush()
            break   # the server closed the connection (or a reconnect shut the socket down)
        while data:
            pending += inflater.decompress(data, STREAM_LINE_MAX)
            data = inflater.unconsumed_tail
            start = 0
            while True:
                end = pending.find(b'\n', start)
                if end < 0:
                    break
                yield bytes(pending[start:end])
                start = end + 1
            del pending[:start]
            if len(pending) > STREAM_LINE_MAX:
                raise ValueError('stream line too long')
    if pending:
        yield bytes(pending)   # a last line without its newline, as a plain stream gives it


class EventStream(threading.Thread):
    """Reads NDJSON events, hands hello and match events to the main loop (events queue) and wakes it.

    v1.4.9: last_line is the local time of the latest line (pings too) and ping the latest ping as (server_now_ms,
    local time). A connection silent for STREAM_SILENCE_SEC ends by its socket timeout, or by mark_silent() from the
    main loop, and the reader connects again. Drops, failed tries and reconnects are logged (never the token).

    v1.5.4: a "closing" line ends the connection at once. overflow and server_shutdown reconnect after 0.2s (as a
    resync), replaced waits replaced_wait() seconds, and any other reason goes the usual way (backoff).

    v1.6.0: asks for gzip (stream_lines reads either form) and hands "move" lines to the main loop like match events.
    The connect line says when the stream is compressed and when hello announced move events."""

    def __init__(self, url, token, wake, events=None):
        super().__init__(daemon=True)
        self.url, self.token, self.wake, self.events = url, token, wake, events
        self.opener = urllib.request.build_opener(NoRedirect())
        self.healthy = False
        self.denied = False
        self.stopping = threading.Event()
        self.last_line = time.monotonic()
        self.ping = None
        self.response = None
        self.silenced = False   # mark_silent() closed the current connection
        self.resync = None      # v1.5.0: reason of a requested reconnect (request_resync), reconnects at once
        self.replaced = []      # v1.5.4: local times of the recent "replaced" closes (within REPLACED_WINDOW_SEC)
        self.replaced_level = 0   # v1.5.4: doublings of the wait after replaced; 0 until REPLACED_REPEAT in the window

    def replaced_wait(self, now):
        """v1.5.4: count a "replaced" close at local time now and return the seconds to wait before connecting again:
        REPLACED_WAIT_SEC to twice that, doubled once more for each close from the REPLACED_REPEAT-th one within
        REPLACED_WINDOW_SEC on (the doubling stays until a connection holds REPLACED_CLEAR_SEC), at most
        REPLACED_WAIT_MAX_SEC. The random part keeps two copies of the connector from coming back together."""
        self.replaced = [t for t in self.replaced if now - t < REPLACED_WINDOW_SEC] + [now]
        if self.replaced_level or len(self.replaced) >= REPLACED_REPEAT:
            self.replaced_level += 1
        base = min(REPLACED_WAIT_MAX_SEC / 2, REPLACED_WAIT_SEC * 2 ** min(self.replaced_level, 30))
        return base * (1 + random.random())

    def mark_silent(self):
        """The main loop saw no line for STREAM_SILENCE_SEC on a connection that looks open: count the stream as down
        (fast polling) and close the connection so the reader connects again."""
        if not self.healthy:
            return
        self.healthy, self.silenced = False, True
        log('Event stream silent for %.1fs (no event or ping); closing it and polling every 1s until it reconnects.'
            % (time.monotonic() - self.last_line))
        response = self.response
        try:
            # http.client response -> socket file -> socket. Shutting the socket down ends the blocked read in the
            # reader thread on every platform; the socket timeout ends it too if this does not work.
            sock = response.fp.raw._sock if response is not None else None
            if sock is not None:
                socket.socket.shutdown(sock, socket.SHUT_RDWR)
        except (AttributeError, OSError, ValueError, TypeError):
            pass

    def request_resync(self, reason):
        """v1.5.0: reconnect to get the full state again (bot API v2 has no poll). The reader reconnects after 0.2s."""
        self.resync = reason
        log('Reconnecting the event stream to resync (%s).' % reason)
        self._shutdown()

    def _shutdown(self):
        response = self.response
        try:
            sock = response.fp.raw._sock if response is not None else None
            if sock is not None:
                socket.socket.shutdown(sock, socket.SHUT_RDWR)
        except (AttributeError, OSError, ValueError, TypeError):
            pass

    def run(self):
        backoff = 1.0
        ever_connected, down_since = False, None
        while not self.stopping.is_set():
            connected, reason = False, 'closed by the server'
            closing, connected_at = None, None   # v1.5.4: reason of the server's closing line; local time of hello
            self.silenced = False
            try:
                # v1.6.0: gzip is welcome; stream_lines reads a compressed and a plain answer alike.
                request = urllib.request.Request(self.url, headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/x-ndjson',
                                                                    'Accept-Encoding': 'gzip'})
                # The server sends a ping every 10s, so STREAM_SILENCE_SEC without data means the stream is stuck.
                with self.opener.open(request, timeout=STREAM_SILENCE_SEC) as response:
                    self.response = response
                    packed = (response.headers.get('Content-Encoding') or '').strip().lower() == 'gzip'
                    for raw in stream_lines(response):
                        self.last_line = time.monotonic()
                        if self.stopping.is_set():
                            return
                        line = raw.strip()
                        if not line:
                            continue
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            continue
                        if event.get('type') == 'hello':
                            if not connected:
                                # v1.6.0: say so when the stream is compressed or carries move events (nothing added otherwise).
                                details = [name for name, on in (('gzip', packed), ('move events', event.get('moves') is True)) if on]
                                details = ' (%s)' % ', '.join(details) if details else ''
                                if ever_connected:
                                    log('Event stream reconnected after %.1fs%s.' % (time.monotonic() - (down_since or self.last_line), details))
                                else:
                                    log('Event stream connected%s.' % details)
                                connected, ever_connected, down_since = True, True, None
                                connected_at = self.last_line
                            self.healthy, backoff = True, 1.0
                            if self.events is not None:
                                # A new connection may have missed events: the state that follows on a ?state=1
                                # stream (state_follows), or else one poll, catches up.
                                event['_received'] = self.last_line
                                self.events.put(event)
                            self.wake.set()
                        elif event.get('type') in ('match', 'state', 'move'):   # v1.6.0: move lines too
                            if self.events is not None:
                                event['_received'] = self.last_line   # for --timing-log and late-event lines
                                self.events.put(event)
                            self.wake.set()
                        elif event.get('type') == 'ping' and type(event.get('server_now_ms')) in (int, float):
                            self.ping = (event['server_now_ms'], self.last_line)
                        elif event.get('type') == 'closing':
                            # v1.5.4: the server closes the connection right after this line; do not wait for it.
                            closing = closing_reason(event.get('reason'))
                            break
            except urllib.error.HTTPError as error:
                if error.code in (401, 403):
                    self.denied = True
                    self.wake.set()
                    return
                reason = 'HTTP %d' % error.code
            except (socket.timeout, TimeoutError):
                reason = 'no data for %ds' % STREAM_SILENCE_SEC
            except Exception as error:
                reason = describe_error(error, self.token)
            finally:
                self.response = None
            if self.stopping.is_set():
                return
            if self.silenced:
                reason = 'silent for %ds, closed by the connector' % STREAM_SILENCE_SEC
            was_healthy, self.healthy = self.healthy, False
            if was_healthy:
                self.wake.set()   # fall back to fast polling right away
            now = time.monotonic()
            if connected_at is not None and now - connected_at >= REPLACED_CLEAR_SEC:
                self.replaced, self.replaced_level = [], 0   # v1.5.4: this connection held, so start counting again
            if closing == 'replaced':
                # v1.5.4: another copy with this token holds the stream now (and gets the state and plays the games).
                # Connecting at once would close it in turn; wait, longer when it keeps happening.
                self.resync, down_since = None, now
                wait = self.replaced_wait(now)
                count = len(self.replaced)
                log("Another connector with this bot's token opened a new stream; this one was closed. Run each bot "
                    "token in one place only. Connecting again in %.0fs (%d time%s in the last %d minutes)."
                    % (wait, count, '' if count == 1 else 's', REPLACED_WINDOW_SEC // 60))
                self.stopping.wait(wait)
                continue
            if closing in ('overflow', 'server_shutdown'):
                # v1.5.4: as a resync. After overflow the new connection's state brings what was dropped; while the
                # server restarts, failed tries back off as usual.
                self.resync, down_since = None, now
                log('Server closing the event stream (%s); reconnecting.' % closing)
                self.stopping.wait(0.2)
                continue
            if closing is not None:
                reason = 'closed by the server: %s' % closing   # v1.5.4: unknown reason, reconnect as before
            if self.resync is not None:
                self.resync = None
                down_since = time.monotonic()
                self.stopping.wait(0.2)   # requested reconnect: no backoff, no failure line
                continue
            wait = backoff + random.random()
            if connected:
                down_since = time.monotonic()
                log('Event stream disconnected (%s); reconnecting in %.1fs, polling every 1s meanwhile.' % (reason, wait))
            else:
                log('Event stream connection failed (%s); next try in %.1fs.' % (reason, wait))
            self.stopping.wait(wait)
            backoff = min(30.0, backoff * 2)


def match_revision(match):
    state = match.get('state') if isinstance(match, dict) else None
    revision = state.get('revision') if isinstance(state, dict) else None
    return revision if type(revision) is int else -1


def match_order(match):
    """Order of two states of the same match: a game against a person (negative id) can start a new round in the same
    room, whose move count (revision) starts again."""
    if isinstance(match, dict) and type(match.get('id')) is int and match['id'] < 0:
        round_no = match.get('round_no')
        return (str(match.get('room_created_at') or ''), round_no if type(round_no) is int else 0, match_revision(match))
    return ('', 0, match_revision(match))


def fold_match(base, match):
    """Put this bot's view of one match (from a stream event or an agent_accept answer) into the last known state.
    A late, older state never replaces a newer one. Returns False when there is nothing to fold into."""
    matches = base.get('matches') if isinstance(base, dict) else None
    if not isinstance(matches, list) or not isinstance(match, dict) or type(match.get('id')) is not int or match['id'] == 0:
        return False
    for index, old in enumerate(matches):
        if isinstance(old, dict) and old.get('id') == match['id']:
            if match_order(match) >= match_order(old):
                matches[index] = match
            return True
    matches.append(match)
    return True


def apply_stream_event(base, event):
    """Fold a stream event into the last agent_poll result. Returns False when a poll is needed instead.

    Only events with this bot's own view (view "agent", the same value agent_poll returns) are used. Since v1.4.10 the
    stream (opened with ?state=1) also sends invitations, games against people and new matches with this bot's view,
    so a match the last poll did not list is added. Events without the view (an older server, or a server that could
    not build the view) still poll."""
    if base is None or event.get('type') != 'match' or event.get('view') != 'agent':
        return False
    return fold_match(base, event.get('match'))


def move_square(text):
    """v1.6.0: square index of a move event coordinate (a1..h8, row-major from a1 = 0, as the history and board of full
    events), -1 for "pass", None for anything else."""
    if not isinstance(text, str):
        return None
    text = text.lower()
    if text == 'pass':
        return -1
    if len(text) != 2 or text[0] not in 'abcdefgh' or text[1] not in '12345678':
        return None
    return (int(text[1]) - 1) * 8 + 'abcdefgh'.index(text[0])


def apply_move_event(base, event):
    """v1.6.0: this bot's view of a match after a stream "move" event (a stream opened with moves=1, standard 8x8 bot
    matches only): the new moves of one board (leg) from position index of its history, and that board's turn, revision,
    clocks_ms and turn_started_ms after them, which mean the same as in a full match and replace them.

    Returns (match, None) with the new view, built from the last known one: the moves are played on the known board by
    the rules (colour = side to move before the move, participant = the one playing that colour on that board, -1 for a
    pass), and board, history, legal_moves, discs and placed follow from them. Fields the event does not carry (the
    match's own revision, totals, at_ms of the new entries) stay as they were; "auto" is not set (the event does not
    say). The known view is never changed in place (running searches still hold its lists).
    Returns (None, None) when every move is known already (a duplicate), and (None, reason) when only a resync can
    tell: no state yet, an unknown match or board, a gap (index past the known history), a known move that differs, a
    move that is not legal on the known board, a turn that does not follow, a game that is not a running standard 8x8
    bot match, or an event without the expected fields. Nothing is guessed."""
    match_id, leg_index, index, moves = (event.get(k) for k in ('match_id', 'leg', 'index', 'moves'))
    turn, revision, clocks, started = (event.get(k) for k in ('turn', 'revision', 'clocks_ms', 'turn_started_ms'))
    if (event.get('view') != 'agent' or type(match_id) is not int or type(leg_index) is not int or type(index) is not int
            or index < 0 or not isinstance(moves, list) or not moves or type(turn) is not int or turn not in (1, 2)
            or type(revision) is not int or not isinstance(clocks, list) or len(clocks) != 2
            or any(type(c) not in (int, float) for c in clocks) or type(started) not in (int, float)):
        return None, 'a move event without the expected fields'
    where = 'match %s leg %s' % (match_id, leg_index)
    squares = [move_square(m) for m in moves]
    if None in squares:
        return None, '%s: unreadable move in a move event' % where
    matches = base.get('matches') if isinstance(base, dict) else None
    match = next((m for m in matches if isinstance(m, dict) and m.get('id') == match_id), None) if isinstance(matches, list) else None
    if match is None:
        return None, 'move event for unknown match %s' % match_id
    state = match.get('state')
    if match_id <= 0 or match.get('status') != 'running' or not isinstance(state, dict) or state.get('version') != 1:
        return None, '%s: move event for a match that is not a running bot match' % where
    if (state.get('game_mode', match.get('game_mode', 'std')) != 'std' or state.get('board_size', 8) != 8
            or state.get('blocked_squares')):
        return None, '%s: move event for a game that is not standard 8x8' % where
    legs = state.get('legs')
    leg = legs[leg_index] if isinstance(legs, list) and 0 <= leg_index < len(legs) else None
    if not isinstance(leg, dict):
        return None, '%s: move event for an unknown board' % where
    history, board, color = leg.get('history'), leg.get('board'), leg.get('turn')
    if (leg.get('status') not in ('running', 'playing', 'active') or not isinstance(history, list)
            or not isinstance(board, list) or len(board) != 64 or any(type(x) is not int or x not in (0, 1, 2) for x in board)
            or type(color) is not int or color not in (1, 2)):
        return None, '%s: move event for a board that is not in play' % where
    known = len(history)
    if index > known:
        return None, '%s: moves missing (event from entry %d, %d known)' % (where, index, known)
    overlap = known - index
    for offset in range(min(overlap, len(squares))):
        entry = history[index + offset]
        if not isinstance(entry, dict) or entry.get('move') != squares[offset]:
            return None, '%s: move event differs from the known history at entry %d' % (where, index + offset)
    new = squares[overlap:]
    if not new:
        return None, None   # all known already (a repeated line)
    if type(leg.get('revision')) is int and revision < leg['revision']:
        return None, '%s: move event revision %d below the known %d' % (where, revision, leg['revision'])
    black = leg.get('black_participant')
    if type(black) is not int or black not in (0, 1):
        black = leg_index   # as current_turns: participant 0 (bot_a) is black on board 0 and white on board 1
    placed = leg.get('placed')
    placed = dict(placed) if isinstance(placed, dict) and all(type(placed.get(k)) is int for k in ('1', '2')) else None
    board, added = list(board), []
    for offset, square in enumerate(new, overlap):
        legal = legal_moves(board, color)
        if square == -1 and legal:
            return None, '%s: pass at entry %d while moves exist on the known board' % (where, index + offset)
        if square != -1:
            if square not in legal:
                return None, '%s: move %s at entry %d is not legal on the known board' % (where, moves[offset], index + offset)
            board = play_square(board, color, square)
            if placed is not None:
                placed[str(color)] += 1
        added.append({'color': color, 'participant': black if color == 1 else 1 - black, 'move': square})
        color = 3 - color
    if color != turn:
        return None, '%s: move event turn %d does not follow from its moves' % (where, turn)
    fresh = dict(leg, board=board, turn=turn, revision=revision, clocks_ms=list(clocks), turn_started_ms=started,
                 history=history + added, legal_moves=legal_moves(board, turn), discs=[board.count(1), board.count(2)])
    if placed is not None:
        fresh['placed'] = placed
    legs = list(legs)
    legs[leg_index] = fresh
    return dict(match, state=dict(state, legs=legs)), None


def merge_state(base, state):
    """A full state (the stream's type "state", the same fields as an agent_poll answer) replaces the last known one,
    except that a match the old state already holds in a newer form keeps that form (an event may have overtaken it)."""
    fresh = {k: v for k, v in state.items() if k not in ('type', 'view', '_received')}
    if isinstance(base, dict) and isinstance(base.get('matches'), list) and isinstance(fresh.get('matches'), list):
        newer = {m['id']: m for m in base['matches'] if isinstance(m, dict) and type(m.get('id')) is int}
        fresh['matches'] = [newer[m['id']] if isinstance(m, dict) and m.get('id') in newer and match_order(newer[m['id']]) > match_order(m)
                            else m for m in fresh['matches']]
    return fresh


def valid_state(state):
    """A stream state usable in place of an agent_poll answer."""
    return (isinstance(state, dict) and state.get('ok') is True and isinstance(state.get('matches'), list)
            and isinstance(state.get('bot'), dict) and type(state.get('server_now_ms')) in (int, float))


def low_clock_interval(remaining_s):
    """Safety poll interval for a leg waiting on the opponent with remaining_s on this bot's clock. Under LOW_CLOCK_SEC
    as in v1.4.9; from v1.4.10 also above it (10s for the 1-minute bot-match clocks, at most 30s for long clocks),
    because the regular safety poll during a game is 30s."""
    if remaining_s < LOW_CLOCK_SEC:
        return min(10.0, max(1.0, remaining_s / 4))
    # v1.5.0: 10s up to a minute on the clock (bot matches), then the regular safety poll. Games against people with long
    # clocks would otherwise check every 10-30s while the person thinks, over the v2 limit of 30 polls in 10 minutes.
    return 10.0 if remaining_s < 60 else float(SAFETY_POLL_GAME_SEC)


def low_clock_due_after(clock_ms):
    """Seconds after the last state of a match at which a poll is due, for a leg of it waiting on the opponent while
    this bot's clock there read clock_ms. A missed opponent move would have started that clock right after the state,
    so the remaining time counts down from it: due at the first x with x >= low_clock_interval(clock - x)
    (x - interval only grows, so bisection finds it). Every stream event of the match is a new state, so in a running
    bot match (two legs, events every few seconds) these polls come only when the match has been quiet."""
    clock = max(0, clock_ms) / 1000
    low, high = 0.0, float(SAFETY_POLL_GAME_SEC)
    for _ in range(30):
        middle = (low + high) / 2
        if middle >= low_clock_interval(clock - middle):
            high = middle
        else:
            low = middle
    return high


def opponent_clock_resync_after(opp_clock_ms):
    """v1.5.0: seconds of quiet after the last state of a match at which a leg waiting on the opponent asks for a resync:
    the opponent's clock at that state (its time when its turn began, so this is late rather than early) plus
    OPP_CLOCK_GRACE_SEC. By then the opponent has moved or lost on time, and either is sent on the stream."""
    return max(0, opp_clock_ms) / 1000 + OPP_CLOCK_GRACE_SEC


def low_clock_counted(clock_ms):
    """Whether a poll for this clock counts against LOW_CLOCK_POLLS_PER_MATCH (the v1.4.9 low-clock polls: the clock
    may fall under LOW_CLOCK_SEC within 10s). Polls above that are at least 10s apart and are not counted."""
    return clock_ms < (LOW_CLOCK_SEC + 10) * 1000


def waiting_clocks(payload, my_id, opponent=False):
    """(match id, leg, this bot's clock in ms) for every active leg of a running game where the opponent is to move
    (with opponent=True the opponent's clock instead, v1.5.0). Never raises: the regular checks in current_turns()
    report bad states."""
    result = []
    if not isinstance(payload, dict):
        return result
    for match in payload.get('matches') or []:
        if not isinstance(match, dict) or match.get('status') != 'running' or type(match.get('id')) is not int:
            continue
        participant = 0 if str(match.get('bot_a')) == str(my_id) else 1 if str(match.get('bot_b')) == str(my_id) else None
        state = match.get('state')
        if participant is None or not isinstance(state, dict) or state.get('status') not in ('running', 'playing', 'active'):
            continue
        legs = state.get('legs')
        for leg_index, leg in enumerate(legs[:2] if isinstance(legs, list) else []):
            color = 1 if participant == leg_index else 2   # as in current_turns
            if not isinstance(leg, dict) or leg.get('status') not in ('running', 'playing', 'active') or leg.get('turn') != 3 - color:
                continue
            clocks = leg.get('clocks_ms')
            side = 2 - color if opponent else color - 1
            if isinstance(clocks, list) and len(clocks) == 2 and type(clocks[side]) in (int, float):
                result.append((match['id'], leg_index, int(clocks[side])))
    return result


class PollPlanner:
    """Safety polls added in v1.4.9 on top of the regular schedule, kept out of the main loop so they can be tested with
    a fake clock. The stream can stop delivering while its connection stays open (match 66566 on 2026-11-19: about 8s,
    with 6s on the clock), and the regular 10s poll then comes too late. While the stream is up:
      - echo: the stream echoes this bot's own move (leg revision + 1) within about 0.1s. Without that echo, or a poll
        started after the move, within ECHO_WAIT_SEC, poll once (at most one poll per own move; games against people
        only on a ?state=1 stream, v1.4.10, because older servers sent only a wake-up for them);
      - waiting leg: in a leg waiting on the opponent, this bot's clock may have been running since the last state of
        that match arrived (by poll, event or stream state). Counting that time as used, poll low_clock_interval()
        after the last state: under LOW_CLOCK_SEC every min(10, max(1, remaining / 4))s, at most
        LOW_CLOCK_POLLS_PER_MATCH times per match (12 from v1.5.0, then only the regular poll); from v1.4.10 also
        above it: 10s while under a minute (1.4.10: min(30, max(10, remaining / 12))s), from v1.5.0 the regular 60s
        poll for longer clocks.
    All times are local monotonic seconds."""

    def __init__(self):
        self.echo = {}        # turn key -> (local time the move was accepted, local time a poll is due without echo)
        self.state_at = {}    # match id -> local time the latest state of that match arrived
        self.low_polls = {}   # match id -> low-clock polls made
        self.capped = set()   # match ids that used up their low-clock polls
        self.human_echo = False   # the stream sends this bot's view of games against people (?state=1, v1.4.10)
        self.echo_wait = ECHO_WAIT_SEC   # v1.5.0: ECHO_WAIT_STREAM_SEC with the stream (a resync is a reconnect)

    def note_poll(self, matches, started_at, answered_at):
        """A poll sent at started_at answered at answered_at with the given matches."""
        ids = {m.get('id') for m in matches if isinstance(m, dict)}
        for match_id in ids:
            self.state_at[match_id] = answered_at
        for key in [k for k, (posted, _) in self.echo.items() if posted <= started_at]:
            del self.echo[key]   # the poll answered with the state after this move
        for table in (self.state_at, self.low_polls):
            for match_id in [m for m in table if m not in ids]:
                del table[match_id]
        self.capped &= ids

    def note_event(self, match, received_at):
        """A stream event (view "agent") whose match replaced the last known state."""
        match_id = match.get('id')
        self.state_at[match_id] = max(self.state_at.get(match_id, received_at), received_at)
        state = match.get('state') if isinstance(match.get('state'), dict) else {}
        legs = state.get('legs') if isinstance(state.get('legs'), list) else []
        for key in [k for k in self.echo if k[0] == match_id]:
            leg = legs[key[1]] if key[1] < len(legs) and isinstance(legs[key[1]], dict) else {}
            revision = leg.get('revision')
            if match.get('status') != 'running' or (type(revision) is int and revision > key[2]):
                del self.echo[key]

    def note_posted(self, key, accepted_at):
        # Games against people (negative id) are echoed only on a ?state=1 stream (human_echo, v1.4.10); before, every
        # room event already polled.
        if key[0] > 0 or self.human_echo:
            self.echo[key] = (accepted_at, accepted_at + self.echo_wait)

    def overdue_echoes(self, now):
        """Turn keys whose echo is overdue (each one is returned once)."""
        late = [k for k, (_, due) in self.echo.items() if due <= now]
        for key in late:
            del self.echo[key]
        return late

    def next_echo_at(self):
        return min((due for _, due in self.echo.values()), default=None)

    def low_clock_plan(self, waiting, fallback_at):
        """{match id: local time its next waiting-leg poll is due} for the waiting legs (see waiting_clocks). A match
        that used up its low-clock polls is polled every 10s after its last state (the v1.4.9 regular poll)."""
        due = {}
        for match_id, _leg, clock_ms in waiting:
            if match_id in self.capped:
                continue   # v1.5.0: a match that used up its low-clock polls is left to the regular safety poll
            after = low_clock_due_after(clock_ms)
            at = self.state_at.get(match_id, fallback_at) + after
            due[match_id] = min(due.get(match_id, at), at)
        return due

    def counted_matches(self, waiting, match_ids):
        """The matches among match_ids whose poll counts against LOW_CLOCK_POLLS_PER_MATCH (see low_clock_counted)."""
        return [m for m in match_ids if m not in self.capped
                and any(w[0] == m and low_clock_counted(w[2]) for w in waiting)]

    def count_low_poll(self, match_ids):
        """Count one low-clock poll for each match; returns the ones that have just used up their polls."""
        used_up = []
        for match_id in match_ids:
            self.low_polls[match_id] = self.low_polls.get(match_id, 0) + 1
            if self.low_polls[match_id] >= LOW_CLOCK_POLLS_PER_MATCH:
                self.capped.add(match_id)
                used_up.append(match_id)
        return used_up


def stream_lag_ms(server_ms, received_at, poll_clock):
    """How long after the server sent a stream line (its server_now_ms) it arrived, against the clock of the last poll
    (server_now_ms of the poll, local time in the middle of it). None without both values."""
    if poll_clock is None or type(server_ms) not in (int, float) or type(received_at) not in (int, float):
        return None
    return poll_clock[0] + (received_at - poll_clock[1]) * 1000 - server_ms


def legal_moves(board, color):
    """Legal squares for color (1 black, 2 white); board is row-major from a1 = 0."""
    result = []
    for square, cell in enumerate(board):
        if cell:
            continue
        row, col = divmod(square, 8)
        for dr, dc in DIRECTIONS:
            r, c, captured = row + dr, col + dc, 0
            while 0 <= r < 8 and 0 <= c < 8 and board[r * 8 + c] == 3 - color:
                captured += 1
                r, c = r + dr, c + dc
            if captured and 0 <= r < 8 and 0 <= c < 8 and board[r * 8 + c] == color:
                result.append(square)
                break
    return result


# v1.6.0 (2026-11-28): rules of the server's fsbm_legal_moves (othello_engine.lib.php oce_variant_geometry, oce_variant_play).
OCTAGON_BLOCKED = frozenset(r * 10 + c for r, c in ((0, 0), (0, 1), (1, 0), (0, 8), (0, 9), (1, 9),
                                                     (8, 0), (9, 0), (9, 1), (8, 9), (9, 8), (9, 9)))
REVERSI_CENTER = (27, 28, 35, 36)   # d4 e4 d5 e5
REVERSI_DISCS = 32                  # discs per player in classic reversi


def game_legal_moves(board, color, mode='std', n=8, blocked=(), placed=None):
    """v1.6.0: legal squares for color (1 black, 2 white) in any game type, for streams that leave legal_moves out.
    std, anti and grand: a move must flip (grand is 10x10). octagon: 10x10 and its 12 blocked squares are never played
    and stop a line like an empty square. reversi (classic): no move once that colour has placed its 32 discs; while any
    of the four centre squares is empty, only those empty centre squares (placed without flipping)."""
    if mode == 'reversi':
        if isinstance(placed, dict) and type(placed.get(str(color))) is int and placed[str(color)] >= REVERSI_DISCS:
            return []
        if any(board[i] == 0 for i in REVERSI_CENTER):
            return [i for i in REVERSI_CENTER if board[i] == 0]
    shut = set(blocked or ()) | (OCTAGON_BLOCKED if mode == 'octagon' else set())
    result = []
    for square, cell in enumerate(board):
        if cell or square in shut:
            continue
        row, col = divmod(square, n)
        for dr, dc in DIRECTIONS:
            r, c, captured = row + dr, col + dc, 0
            while 0 <= r < n and 0 <= c < n and board[r * n + c] == 3 - color:
                captured += 1
                r, c = r + dr, c + dc
            if captured and 0 <= r < n and 0 <= c < n and board[r * n + c] == color:
                result.append(square)
                break
    return result


def start_process(command, env):
    kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, shell=False)
    if WINDOWS:
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    return subprocess.Popen(command, **kwargs)


def kill_tree(process):
    """Stop the engine and anything it started (search helpers hold CPU slots)."""
    if process.poll() is None:
        try:
            if WINDOWS:
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                if WINDOWS:
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    for stream in (process.stdin, process.stdout):
        try:
            if stream:
                stream.close()
        except OSError:
            pass


def line_reader(process, sink):
    """Thread: forward stdout lines to a queue (portable replacement for select on pipes)."""
    try:
        for raw in iter(process.stdout.readline, b''):
            sink.put(raw)
    except (OSError, ValueError):
        pass
    sink.put(None)


def engine_env():
    # Engine code must not inherit the connector credential.
    return {k: v for k, v in os.environ.items() if k != 'FS_BOT_TOKEN'}


class PersistentJsonEngine:
    """--engine JSON-lines protocol. Each game (room/leg) owns its process and search memory."""
    def __init__(self, command, report_environment=None):
        self.command = command
        self.report_environment = report_environment
        self.process = None
        self.lines = None
        self.lock = threading.Lock()

    def stop(self):
        if self.process is not None:
            kill_tree(self.process)
            self.process = None
            self.lines = None

    def move(self, request, deadline, cancel):
        # A superseded search must finish cleanup before this leg starts again.
        while not self.lock.acquire(timeout=.05):
            if cancel.is_set() or time.monotonic() >= deadline:
                raise TimeoutError('search cancelled while waiting for engine')
        try:
            if cancel.is_set() or time.monotonic() >= deadline:
                raise TimeoutError('search cancelled before launch')
            retried = False
            def launch_and_send():
                if self.process is None or self.process.poll() is not None:
                    self.stop()
                    self.process = start_process(self.command, engine_env())
                    self.lines = queue.Queue()
                    threading.Thread(target=line_reader, args=(self.process, self.lines), daemon=True).start()
                # Keys starting with "_" are the connector's own (for the Edax adapter), not part of the JSON protocol.
                self.process.stdin.write((json.dumps({k: v for k, v in request.items() if not k.startswith('_')}) + '\n').encode())
                self.process.stdin.flush()
            try:
                launch_and_send()
            except (BrokenPipeError, OSError):
                # The engine answered the previous move and was just exiting: start it again and resend.
                self.stop(); retried = True; launch_and_send()
            while True:
                if cancel.is_set():
                    raise RuntimeError('search superseded')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('engine response timeout')
                try:
                    raw = self.lines.get(timeout=min(remaining, .1))
                except queue.Empty:
                    continue
                if raw is None and not retried and time.monotonic() < deadline - .2:
                    # It exited without answering (for example an engine that handles one line and quits): restart it and resend the same request once.
                    self.stop(); retried = True; launch_and_send(); continue
                if raw is None or len(raw) > 65536:
                    raise RuntimeError('engine ended or response too large')
                answer = json.loads(raw)
                move = answer.get('move') if isinstance(answer, dict) else None
                if isinstance(move, str):
                    move = square_index(move, request.get('board_size', 8))
                if type(move) is not int or not -1 <= move < request.get('board_size', 8)**2:
                    raise ValueError('engine move must be an index or coordinate within the board')
                if self.report_environment:
                    self.report_environment(answer.get('environment'))
                # Only bounded numeric diagnostics, never arbitrary engine text or credentials.
                timing = {key: answer[key] for key in ('used_ms', 'soft_ms', 'normal_ms', 'hard_ms', 'over_ms', 'stop_reason')
                          if type(answer.get(key)) is int}
                if timing:
                    timing.update({key: request[key] for key in ('match_id', 'leg', 'revision', 'remaining_ms')
                                   if type(request.get(key)) is int})
                    print('Engine time: ' + json.dumps(timing), file=sys.stderr, flush=True)
                # v1.5.3: optional "eval" (discs, the mover's side) goes with agent_move; anything else is ignored.
                evaluation = engine_eval(answer.get('eval'))
                return move if evaluation is None else (move, evaluation)
        except BaseException:
            self.stop()
            raise
        finally:
            self.lock.release()


def engine_eval(value, limit=100):
    """v1.5.3: an engine's evaluation of its move in discs from the mover's side, kept to 2 decimals; else None.
    The game's own limit (eval_limit) is checked when the move is sent."""
    if type(value) not in (int, float) or not math.isfinite(value) or not -limit <= value <= limit:
        return None
    value = round(float(value), 2)
    return 0.0 if value == 0 else value


def eval_limit(request):
    """Playable squares of the game, the site's eval range: 64 on 8x8, 100 on 10x10, 88 on octagon (12 blocked squares)."""
    n = request.get('board_size', 8)
    return n * n - len(request.get('blocked_squares') or [])


def nboard_eval(reply):
    """The score of an NBoard reply "f5/1.5/0.3" (the mover's side, discs), or None."""
    parts = reply.split()[0].split('/') if reply.split() else []
    if len(parts) < 2:
        return None
    try:
        return engine_eval(float(parts[1]))
    except ValueError:
        return None


def square_index(text, n=8):
    """Coordinates are case-insensitive; a10 and a0 both mean the tenth row on 10x10 boards."""
    if not isinstance(text,str):raise ValueError('coordinate must be text')
    text = text.lower()
    if text in ('pa', 'pass', '--', 'ps'):return -1
    if n not in (8,10) or len(text) not in (2,3) or text[0] not in 'abcdefghij' or text[1:] not in tuple(str(i) for i in range(1,11))+('0',):
        raise ValueError('engine move must be a coordinate or pass')
    col=ord(text[0])-ord('a');row=9 if text[1:]=='0' else int(text[1:])-1
    if col>=n or row>=n:raise ValueError('coordinate outside board')
    return row*n+col


def ggf_position(board, color):
    """Position-only GGF game for NBoard 'set game'. Rows a1..h1, a2..h8; * black, O white, - empty."""
    cells = ''.join('-*O'[v] for v in board)
    return '(;GM[Othello]PC[FlipSight]PB[]PW[]RE[?]TI[0:00]TY[8]BO[8 %s %s];)' % (cells, '*' if color == 1 else 'O')


class NBoardEngine:
    """One long-running NBoard engine. Thinking is stopped by restarting the process."""

    def __init__(self, command, depth):
        self.command, self.depth = command, depth
        self.process, self.lines, self.ping = None, None, 0
        self.lock = threading.Lock()

    def _start(self):
        self.process = start_process(self.command, engine_env())
        self.lines = queue.Queue()
        threading.Thread(target=line_reader, args=(self.process, self.lines), daemon=True).start()
        self._send('nboard 2')
        if self.depth:
            self._send('set depth %d' % self.depth)

    def _send(self, text):
        self.process.stdin.write((text + '\n').encode())
        self.process.stdin.flush()

    def _read_until(self, accept, deadline, cancel):
        while True:
            if cancel.is_set():
                raise RuntimeError('search superseded')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('engine response timeout')
            try:
                raw = self.lines.get(timeout=min(remaining, .1))
            except queue.Empty:
                continue
            if raw is None:
                raise RuntimeError('engine exited')
            result = accept(raw.decode('utf-8', 'replace').strip())
            if result is not None:
                return result

    def stop(self):
        if self.process is not None:
            kill_tree(self.process)
            self.process = None

    def move(self, request, deadline, cancel):
        if request.get('game_mode','std')!='std':
            raise ValueError('NBoard adapter only supports standard Othello; use a variant-aware JSON adapter')
        if not request['legal_moves']:
            return -1   # NBoard engines are not asked to pass; the server needs an explicit -1
        with self.lock:
            try:
                if self.process is None or self.process.poll() is not None:
                    self._start()
                # ping/pong drops any output left over from an earlier, abandoned search.
                self.ping += 1
                tag = 'pong %d' % self.ping
                self._send('ping %d' % self.ping)
                self._read_until(lambda t: True if t == tag else None, deadline, cancel)
                self._send('set game ' + ggf_position(request['board'], request['color']))
                self._send('go')
                reply = self._read_until(lambda t: t[3:].strip() if t.startswith('===') else None, deadline, cancel)
                # NBoard may append score and elapsed time (=== f5/1.5/0.3). v1.5.3: the score goes with the move as eval.
                move = square_index(reply.split()[0].split('/', 1)[0])
                evaluation = nboard_eval(reply)
                return move if evaluation is None else (move, evaluation)
            except BaseException:
                self.stop()   # the only portable way to stop a search in NBoard
                raise


def standard_request(request):
    if (request.get('game_mode', 'std') != 'std' or request.get('board_size', 8) != 8
            or len(request['board']) != 64 or request.get('blocked_squares')):
        raise ValueError('this engine supports standard 8x8 Othello only')


def native_executable(path):
    path = os.path.expanduser(path)
    found = path if os.path.isfile(path) else shutil.which(path)
    if not found:
        raise ValueError('engine executable not found')
    return os.path.abspath(found)


class EdaxNBoardEngine(NBoardEngine):
    """Edax 4.6 over NBoard (--edax-protocol nboard, before v1.4.8): the connector gives every turn its time and
    ping stops the search, which returns the last completed result."""

    def __init__(self, command, depth=0):
        super().__init__(command, depth or 60)

    def _start(self):
        # Edax 4.6 implements NBoard 1. Generic --nboard keeps its existing handshake.
        self.process = start_process(self.command, engine_env())
        self.lines = queue.Queue()
        threading.Thread(target=line_reader, args=(self.process, self.lines), daemon=True).start()
        self._send('nboard 1')
        self._send('set depth %d' % self.depth)

    def move(self, request, deadline, cancel):
        standard_request(request)
        if not request['legal_moves']:
            return -1
        with self.lock:
            try:
                if cancel.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError('search cancelled before launch')
                if self.process is None or self.process.poll() is not None:
                    self._start()
                self.ping += 1
                self._send('ping %d' % self.ping)
                tag = 'pong %d' % self.ping
                self._read_until(lambda t: True if t == tag else None, deadline, cancel)
                # Spend a little more than an even share; recalculate from the server clock
                # on every move, including late endgames and random opening positions.
                moves_left = max(1, (request['board'].count(0) + 1) // 2)
                allocated = 1.5 * max(0, request['remaining_ms'] - 750) / 1000 / moves_left
                now = time.monotonic()
                stop_reserve = min(.2, max(0, deadline - now) * .25)
                soft_deadline = min(deadline - stop_reserve, now + allocated)
                if soft_deadline <= time.monotonic():
                    raise TimeoutError('not enough time to start Edax search')
                self._send('set game ' + ggf_position(request['board'], request['color']))
                self._send('go')
                accept = lambda t: t[3:].strip() if t.startswith('===') else None
                try:
                    reply = self._read_until(accept, soft_deadline, cancel)
                except TimeoutError:
                    # Edax's input thread calls play_stop() on ping; the thinking thread
                    # emits === before the queued pong, without discarding its hash table.
                    self.ping += 1
                    self._send('ping %d' % self.ping)
                    reply = self._read_until(accept, deadline, cancel)
                move = square_index(reply.split()[0].split('/', 1)[0])
                if move not in request['legal_moves']:
                    raise ValueError('Edax returned an illegal move')
                evaluation = nboard_eval(reply)   # v1.5.3
                return move if evaluation is None else (move, evaluation)
            except BaseException:
                self.stop()
                raise


def play_square(board, color, square):
    """The 8x8 board after color plays square (row-major from a1 = 0)."""
    result = list(board)
    row, col = divmod(square, 8)
    for dr, dc in DIRECTIONS:
        r, c, line = row + dr, col + dc, []
        while 0 <= r < 8 and 0 <= c < 8 and result[r * 8 + c] == 3 - color:
            line.append(r * 8 + c)
            r, c = r + dr, c + dc
        if line and 0 <= r < 8 and 0 <= c < 8 and result[r * 8 + c] == color:
            for flipped in line:
                result[flipped] = color
    result[square] = color
    return result


def xboard_square(square):
    return '@@@@' if square == -1 else 'abcdefgh'[square % 8] + str(square // 8 + 1)


START_BOARD = [2 if i in (27, 36) else 1 if i in (28, 35) else 0 for i in range(64)]   # d4, e5 White; e4, d5 Black


def edax_line_eval(line, move):
    """v1.5.3: the evaluation in Edax's xboard search line "depth score time nodes [@sel%] pv" (score in hundredths
    of a disc from the side to move), or None. The line counts only when its PV starts with this move in lowercase,
    Edax's own colour in every leg (edax_xboard_fen); a line left from pondering another position starts otherwise."""
    parts = line.split()
    if len(parts) < 5 or not parts[0].isdigit():
        return None
    try:
        score = int(parts[1])
    except ValueError:
        return None
    pv = parts[5:] if parts[4].startswith('@') else parts[4:]
    first = pv[0].strip('()') if pv else ''
    if first != xboard_square(move):
        return None
    return engine_eval(score / 100)


def edax_xboard_fen(board, me):
    """Edax's xboard FEN with Edax to move. Edax stones are always the lowercase side 'p' with 'b' to move, so Edax
    plays its internal first colour in every leg: the clock slot that "time" fills after "new" and "force" (see EdaxEngine).
    Rows run 8..1 and a..h, as in Edax's board_from_FEN; colours do not matter to Edax's search or book."""
    rows = []
    for r in range(7, -1, -1):
        text, empty = '', 0
        for value in board[r * 8:r * 8 + 8]:
            if not value:
                empty += 1
                continue
            if empty:
                text, empty = text + str(empty), 0
            text += 'p' if value == me else 'P'
        rows.append(text + (str(empty) if empty else ''))
    return '/'.join(rows) + ' b'


class EdaxOutOfStep(Exception):
    """Edax's own game no longer matches the server (it rejected a move or answered for another position)."""


# Clock kept back from the time sent to Edax for network and connector delays (as in thinking_budget). Edax keeps
# its own margin on top: 1s above 10s left, 10s above 60s left.
EDAX_CLOCK_RESERVE_MS = 750


class EdaxEngine(NBoardEngine):
    """Edax 4.6 over xboard, one process per leg (v1.4.8). Edax runs its own clock: before every search the connector
    sends "time" (this bot's site clock less EDAX_CLOCK_RESERVE_MS) and "otim" (the opponent's), in centiseconds.

    The leg starts with new, level (the site's game time), force, easy/hard, setboard and go. After that one opponent
    move is sent as "usermove" (a pass as @@@@) and Edax answers by itself, keeping its game, hash table and, with
    ponder on, its search in the opponent's time. Several plies at once (for example around a pass) go in force mode
    and end with go; a position that does not follow from Edax's game by the server history is set again with setboard.

    Clock slots: "time" fills Edax's own slot (edax_turn), or after "force" the slot it had before (last_edax_turn).
    "new" leaves edax_turn on Edax's internal first colour and setboard always gives Edax that colour (edax_xboard_fen),
    so "force" is never sent twice in a row and every "time" lands on Edax's own clock.

    Safety: if no move comes by the connector's per-turn limit, "?" makes Edax play its best move at once; if that
    also does not arrive the process is stopped, as before.
    """

    def __init__(self, command, depth=0, ponder=False):
        super().__init__(command, depth)
        self.ponder = ponder
        self.known = None   # (board, colour to move, history entries) of Edax's own game; None: set the board

    def stop(self):
        super().stop()
        self.known = None

    def _send(self, *lines):
        self.process.stdin.write(''.join(line + '\n' for line in lines).encode())
        self.process.stdin.flush()

    def _start(self, request):
        self.process = start_process(self.command, engine_env())
        self.lines = queue.Queue()
        self.known = None
        threading.Thread(target=line_reader, args=(self.process, self.lines), daemon=True).start()
        # Without "level" Edax keeps its fixed-depth default; level 0 M:SS 0 is time per game (the site has no increment).
        seconds = max(1, -(-(request.get('_time_limit_ms') or request['remaining_ms']) // 1000))
        self._send('xboard', 'protover 2', 'new', 'level 0 %d:%02d 0' % divmod(seconds, 60),
                   *(['sd %d' % self.depth] if self.depth else []))

    def _plies(self, request):
        """Moves from Edax's own game to the requested position by the server history, or None to set the board."""
        if self.known is None:
            return None
        board, color, count = self.known
        history = request.get('history')
        if not isinstance(history, list) or len(history) <= count:
            return None
        plies = []
        for entry in history[count:]:
            move = entry.get('move') if isinstance(entry, dict) else None
            if type(move) is not int or entry.get('color') != color:
                return None
            legal = legal_moves(board, color)
            if move == -1:
                if legal:
                    return None
            elif move in legal:
                board = play_square(board, color, move)
            else:
                return None
            plies.append(move)
            color = 3 - color
        return plies if board == request['board'] and color == request['color'] else None

    def move(self, request, deadline, cancel):
        standard_request(request)
        legal = request['legal_moves']
        if not legal:
            return -1   # the history replay gives Edax this pass with the next opponent move
        with self.lock:
            started = time.monotonic()
            for attempt in (0, 1):
                try:
                    if cancel.is_set() or time.monotonic() >= deadline:
                        raise TimeoutError('search cancelled before launch')
                    if self.process is None or self.process.poll() is not None:
                        self.stop()
                        self._start(request)
                    # ping/pong also drops any output left from earlier searches or pondering.
                    self.ping += 1
                    tag = 'pong %d' % self.ping
                    self._send('ping %d' % self.ping)
                    self._read_until(lambda t: True if t == tag else None, deadline, cancel)
                    mine = request['remaining_ms'] - EDAX_CLOCK_RESERVE_MS - int((time.monotonic() - started) * 1000)
                    clocks = ('time %d' % max(0, mine // 10), 'otim %d' % (request.get('_opponent_ms', 0) // 10))
                    plies = self._plies(request)
                    if plies is not None and len(plies) == 1:
                        commands = clocks + ('usermove ' + xboard_square(plies[0]),)   # Edax moves on its own
                    elif plies:
                        commands = ('force',) + tuple('usermove ' + xboard_square(m) for m in plies) + clocks + ('go',)
                    else:
                        # setboard also stops any pondering that "hard" starts in force mode.
                        commands = ('force', 'hard' if self.ponder else 'easy',
                                    'setboard ' + edax_xboard_fen(request['board'], request['color'])) + clocks + ('go',)
                    self._send(*commands)
                    lines = []   # v1.5.3: Edax's search lines after these commands; the last one before "move" is this search's
                    def accept(text):
                        if text.startswith('Illegal move'):
                            raise EdaxOutOfStep('Edax rejected a move')
                        if text.startswith('move '):
                            return text[5:].strip()
                        if text[:1].isdigit():
                            lines.append(text)
                        return None
                    soft_deadline = deadline - min(.2, max(0, deadline - time.monotonic()) * .25)
                    try:
                        reply = self._read_until(accept, soft_deadline, cancel)
                    except TimeoutError:
                        self._send('?')   # play now: Edax stops and plays its best move so far
                        reply = self._read_until(accept, deadline, cancel)
                    reply = reply[2:] if reply[:2].lower() == 'p@' else reply
                    move = -1 if reply == '@@@@' else square_index(reply)
                    if move not in legal:
                        raise EdaxOutOfStep('Edax answered for another position')
                    self.known = (play_square(request['board'], request['color'], move), 3 - request['color'],
                                  len(request.get('history') or []) + 1)
                    # The first move from the start position is Edax's fixed opening (f5 or its mirror) without a search line.
                    evaluation = edax_line_eval(lines[-1], move) if lines and request['board'] != START_BOARD else None
                    return move if evaluation is None else (move, evaluation)
                except EdaxOutOfStep:
                    # Start again from the server position once; a second mismatch is an engine failure.
                    self.stop()
                    if attempt:
                        raise RuntimeError('Edax does not follow the game')
                except BaseException:
                    self.stop()
                    raise


class EgaroucidEngine(NBoardEngine):
    """Native console protocol, one persistent process per leg; no GTP conversion."""

    def __init__(self, command, level=0):
        super().__init__(command, 0)
        # Level mode (1..60): the process was started with "-level N" and never receives
        # settime, because one settime switches Egaroucid to time mode and ignores the level.
        self.level = level

    def _start(self):
        self.process = start_process(self.command, engine_env())
        self.lines = queue.Queue()
        threading.Thread(target=line_reader, args=(self.process, self.lines), daemon=True).start()
        self._send('version')

    def _read_until(self, accept, deadline, cancel):
        def checked(text):
            if text.startswith('[ERROR]'):
                raise RuntimeError('Egaroucid rejected a console command')
            return accept(text)
        return super()._read_until(checked, deadline, cancel)

    def move(self, request, deadline, cancel):
        standard_request(request)
        legal = request['legal_moves']
        if not legal:
            return -1
        with self.lock:
            try:
                started = time.monotonic()
                if cancel.is_set() or started >= deadline:
                    raise TimeoutError('search cancelled before launch')
                if self.process is None or self.process.poll() is not None:
                    self._start()
                    self._read_until(lambda t: True if t.startswith('Egaroucid') else None, deadline, cancel)
                    self._read_until(lambda t: True if not t else None, deadline, cancel)
                board = ''.join('-XO'[v] for v in request['board'])
                color = 'X' if request['color'] == 1 else 'O'
                # setboard resets engine clocks, so settime must follow it. The native
                # console accepts integer seconds: round down, after startup and reserve.
                left_ms = request['remaining_ms'] - 750 - int((time.monotonic() - started) * 1000)
                if time.monotonic() >= deadline or cancel.is_set():
                    raise TimeoutError('search cancelled after initialization')
                self._send('setboard %s %s' % (board, color))
                if not self.level:
                    self._send('settime %s %d' % (color, max(0, left_ms // 1000)))
                self._send('go')
                def accept(text):
                    # v1.5.3: -showvalue adds the value after the move ("f5 2": discs, the mover's side).
                    word, _, value = text.partition(' ')
                    if len(word) == 2 and word[0].lower() in 'abcdefgh' and word[1] in '12345678':
                        move = square_index(word)
                    elif word.lower() in ('ps', 'pa', 'pass'):
                        move = -1
                    else:
                        return None
                    try:
                        return move, engine_eval(float(value)) if value.strip() else None
                    except ValueError:
                        return move, None
                try:
                    move, evaluation = self._read_until(accept, deadline, cancel)
                except TimeoutError:
                    # Native console has no asynchronous stop command. Never reuse an
                    # unfinished search: stop it and submit a legal emergency move.
                    self.stop()
                    if cancel.is_set():
                        raise RuntimeError('search superseded')
                    print('Egaroucid reached the per-turn safety cap; using a legal fallback move.', file=sys.stderr)
                    return next((m for m in legal if m in (0, 7, 56, 63)), legal[0])
                if move not in legal:
                    raise ValueError('Egaroucid returned an illegal move')
                return move if evaluation is None else (move, evaluation)
            except BaseException:
                self.stop()
                raise


def detect_cpu(cpu_name):
    if cpu_name:
        return cpu_name[:120]
    cpu = platform.processor() or platform.machine()
    system = platform.system()
    if system == 'Windows':
        # platform.processor() is commonly a Family/Model/Stepping identifier.
        # Read the product name once at startup, without an extra package/process.
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                value, _ = winreg.QueryValueEx(key, 'ProcessorNameString')
            if isinstance(value, str) and value.strip():
                return ' '.join(value.split())[:120]
        except (ImportError, OSError):
            pass
        # Some Windows environments restrict registry access. Keep a bounded,
        # noninteractive fallback; failure must never stop the bot connector.
        try:
            value = subprocess.check_output([
                'powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                "$ErrorActionPreference='Stop'; "
                "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                "Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name"
            ], encoding='utf-8', errors='replace', timeout=2, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()
            if value:
                cpu = value
        except (OSError, subprocess.SubprocessError):
            pass
    elif system == 'Linux':
        # 시작할 때 한 번만 읽는다. 아키텍처 대신 커널이 보고한 CPU 제품명을 우선한다.
        try:
            with open('/proc/cpuinfo', encoding='utf-8', errors='replace') as info:
                lines = info.read(65536).splitlines()
            for line in lines:
                key, separator, value = line.partition(':')
                if separator and key.strip().lower() == 'model name' and value.strip():
                    return ' '.join(value.split())[:120]
        except OSError:
            pass
        # ARM이나 제한된 /proc 환경에서는 lscpu가 아는 모델명을 한 번만 확인한다.
        try:
            value = subprocess.check_output([
                'lscpu', '--json'
            ], encoding='utf-8', errors='replace', timeout=1, stderr=subprocess.DEVNULL,
                env=dict(os.environ, LC_ALL='C'))
            data = json.loads(value)
            rows = data.get('lscpu', []) if isinstance(data, dict) else []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                value = row.get('data')
                if str(row.get('field', '')).strip().rstrip(':') == 'Model name' and isinstance(value, str) and value.strip():
                    return ' '.join(value.split())[:120]
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    elif system == 'Darwin':
        try:
            value = subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string'], text=True, timeout=1).strip()
            if value:
                cpu = value
        except (OSError, subprocess.SubprocessError):
            pass
    return ' '.join(cpu.split())[:120]


def current_turns(payload, my_id):
    turns = {}
    now = payload.get('server_now_ms')
    for match in payload.get('matches', []):
        if match.get('status') != 'running':
            continue
        participant = 0 if str(match.get('bot_a')) == str(my_id) else 1 if str(match.get('bot_b')) == str(my_id) else None
        if participant is None:
            continue
        state = match.get('state') or {}
        if state.get('version') != 1:
            raise ValueError('unsupported match state version')
        if state.get('status') not in ('running', 'playing', 'active'):
            continue
        for leg_index, leg in enumerate(state.get('legs', [])[:2]):
            # Participant 0 (bot_a) is black in leg 0 and white in leg 1. Human matches have one leg.
            color = 1 if participant == leg_index else 2
            if leg.get('status') not in ('running', 'playing', 'active') or leg.get('turn') != color:
                continue
            board = leg.get('board')
            mode=state.get('game_mode',match.get('game_mode','std'));n=state.get('board_size',10 if mode in ('grand','octagon') else 8)
            if mode not in ('std','anti','grand','octagon','reversi') or n != (10 if mode in ('grand','octagon') else 8):
                raise ValueError('unsupported game geometry')
            if not isinstance(board, list) or len(board) != n*n or any(type(x) is not int or x not in (0, 1, 2) for x in board):
                raise ValueError('invalid board received')
            clocks = leg.get('clocks_ms', [])
            if len(clocks) != 2:
                raise ValueError('invalid clocks received')
            remaining = max(0, int(clocks[color - 1]))
            limit = state.get('time_limit_sec', match.get('time_limit_sec'))
            if type(now) in (int, float) and type(leg.get('turn_started_ms')) in (int, float):
                remaining = max(0, remaining - max(0, int(now - leg['turn_started_ms'])))
            key = (int(match['id']), leg_index, int(leg['revision']), match.get('round_no'), match.get('room_created_at'))
            legal=leg.get('legal_moves')
            if legal is None:   # v1.6.0: move-event streams leave legal_moves out (all game types); also older servers
                legal=game_legal_moves(board,color,mode,n,state.get('blocked_squares',[]),leg.get('placed'))
            if not isinstance(legal,list) or any(type(v) is not int or not 0<=v<n*n or board[v]!=0 for v in legal):
                raise ValueError('invalid legal moves received')
            turns[key] = {'board': board, 'color': color, 'legal_moves': legal, 'game_mode':mode,'board_size':n,
                'blocked_squares':state.get('blocked_squares',[]),'placed':leg.get('placed',{}),'remaining_ms': remaining,
                'match_id': key[0], 'leg': key[1], 'revision': key[2], 'history': leg.get('history', []),
                'round_no': match.get('round_no'), 'room_created_at': match.get('room_created_at'),
                # For the Edax xboard adapter only (never sent to JSON engines): the opponent's clock and the game time.
                '_opponent_ms': max(0, int(clocks[2 - color])),
                '_time_limit_ms': max(0, int(limit)) * 1000 if type(limit) in (int, float) else None}
    return turns


def thinking_budget(remaining_ms, max_think_seconds):
    """Zero disables the optional move cap; the actual game clock still bounds the job."""
    available = max(0, remaining_ms - 750) / 1000
    return min(max_think_seconds, available) if max_think_seconds else available


def incoming_challenge(payload, my_id, selected=None, use_games_now=True):
    """Pending pairs already reserve capacity; accept within the configured total load. v1.5.0: with the stream the
    bot row (games_now) is only as fresh as the last connection, so the load is counted from the known matches."""
    bot = payload.get('bot') or {}
    limit = bot.get('max_games', 2)
    limit = max(1, min(8, limit if type(limit) is int else 2))
    load = bot.get('games_now') if use_games_now else None
    if type(load) is not int:
        load = sum(m.get('status') in ('pending', 'running') for m in payload.get('matches', []))
    if load > limit:
        return None
    matches = [m for m in payload.get('matches', [])
               if int(m.get('id', 0)) > 0 and str(my_id) in (str(m.get('bot_a')), str(m.get('bot_b')))]
    for match in matches:
        if (match.get('status') == 'pending' and str(match.get('bot_b')) == str(my_id)
                and int(match['id']) > 0
                and (selected is None or str(match['id']) == str(selected))):
            return int(match['id'])
    return None


def resolve_engine_threads(args, parser):
    if args.engine_threads is not None:
        return args.engine_threads
    if args.edax or args.egaroucid:
        return 4 if (os.cpu_count() or 1) > 4 else 1
    # Custom and generic NBoard engines cannot expose their allocation automatically.
    # Never guess a thread count for them or silently report a default.
    if sys.stdin is None or not sys.stdin.isatty():
        parser.error('--engine-threads is required with --engine/--nboard in non-interactive mode')
    while True:
        try:
            value = input('Engine threads per game: ').strip()
        except (EOFError, KeyboardInterrupt):
            parser.error('thread input cancelled; set --engine-threads to the actual engine thread count')
        if value.isascii() and value.isdecimal() and len(value) <= 4 and 1 <= int(value) <= 1024:
            return int(value)
        print('Enter a whole number from 1 to 1024 matching the engine setting.', file=sys.stderr)


MAX_SEARCHES = 8   # the site's highest max_games


def merge_engine_environment(environment, reported):
    """Only bounded allocation fields from a JSON engine; never publish arbitrary engine text."""
    if not isinstance(reported, dict):
        return
    threads = reported.get('engine_threads')
    if type(threads) is int and 1 <= threads <= 1024:
        environment['engine_threads'] = threads
    hash_mb = reported.get('hash_mb')
    if type(hash_mb) in (int, float) and math.isfinite(hash_mb) and 0 <= hash_mb <= 1048576:
        environment['hash_mb'] = hash_mb
    if type(reported.get('ponder')) is bool:
        environment['ponder'] = reported['ponder']


# Edax 4.6 (64-bit) bounds its table to 2**10..2**30 entries; each main entry plus the two 1/16-size
# side tables is 27 bytes. Egaroucid Console -hash N (0..29) allocates 2**N entries of 32 bytes.
EDAX_HASH_BITS = (10, 30)
EGAROUCID_HASH_LEVELS = (0, 29)


def adapter_hash_setting(args, parser):
    """--engine-hash-mb with --edax/--egaroucid sets the engine table to the largest power-of-two
    size not above MB. Returns (engine arguments, actual MB), or (None, None) when it does not apply."""
    mb = args.engine_hash_mb
    if mb is None or not (args.edax or args.egaroucid):
        return None, None
    if args.edax:
        low, high = EDAX_HASH_BITS
        bits = int(math.floor(math.log2(mb / 27))) + 20 if mb > 0 else low - 1
        if bits < low:
            parser.error('--engine-hash-mb is below the smallest Edax table (%.3f MB)' % (27 * 2.0 ** (low - 20)))
        bits = min(bits, high)
        return ['-h', str(bits)], 27 * 2.0 ** (bits - 20)
    low, high = EGAROUCID_HASH_LEVELS
    level = int(math.floor(math.log2(mb))) + 15 if mb > 0 else low - 1
    if level < low:
        parser.error('--engine-hash-mb is below the smallest Egaroucid table (%.5f MB)' % (2.0 ** (low - 15)))
    level = min(level, high)
    return ['-hash', str(level)], 2.0 ** (level - 15)


def configured_hash_mb(args):
    """Configured hash-table capacity, not process RAM. Unknown engines remain unreported.

    Edax 4.6 uses 24-byte entries in the main table plus two 1/16-size tables.
    Egaroucid Console's fixed -hash 20 uses 2**20 32-byte entries.
    An explicit report supports custom builds; it does not change engine settings.
    """
    if args.engine_hash_mb is not None:
        return args.engine_hash_mb
    if args.egaroucid:
        return 32.0
    if not args.edax:
        return None
    bits = 21
    try:
        with open('edax.ini', encoding='utf-8-sig') as stream:
            text = stream.read(65537)
        if len(text) > 65536:
            return None
        for line in text.splitlines():
            words = line.split()
            if words and words[0] == 'set':
                words = words[1:]
            if not words:
                continue
            if words[0] in ('o', 'option-file'):
                return None  # Do not guess through nested option files.
            if words[0] in ('h', 'hash-table-size'):
                value = words[1:]
                if value and value[0] == '=':
                    value = value[1:]
                bits = int(value[0])
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, ValueError, IndexError):
        return None
    # Values above 25 depend on whether the actual engine binary is 32 or 64 bit.
    return 27.0 * 2 ** (bits - 20) if 10 <= bits <= 25 else None


def configured_ponder(args):
    """Egaroucid Console ponders with -ponder and Edax over xboard with -ponder on and "hard" (--engine-ponder on).
    Edax 4.6 never ponders in NBoard mode (its NBoard loop does not start a ponder search), so --edax-protocol nboard
    reports off. Custom engines can optionally report their own setting without changing it.
    """
    if args.edax:
        return args.edax_protocol == 'xboard' and args.engine_ponder == 'on'
    if args.egaroucid:
        return args.engine_ponder == 'on'
    return None if args.engine_ponder is None else args.engine_ponder == 'on'


def main():
    parser = argparse.ArgumentParser(description='FlipSight bot connector')
    parser.add_argument('--api', default='https://othelloclub.com/flipsight/bot_match_api.php')
    parser.add_argument('--stream-url', help='Event stream URL; default is /flipsight_api/bot-stream on the API host')
    parser.add_argument('--no-stream', action='store_true', help=argparse.SUPPRESS)   # removed in v1.5.0 (no agent_poll)
    engines = parser.add_mutually_exclusive_group(required=True)
    engines.add_argument('--engine', help='Local command, one process per game: repeatedly read one JSON request line and write one {"move": index} line')
    engines.add_argument('--nboard', help='Local command of an engine that speaks the NBoard protocol (kept running)')
    engines.add_argument('--edax', metavar='PATH', help='Edax 4.6 executable over xboard: Edax runs its own game clock and keeps its game and hash table through the leg (standard 8x8 only)')
    engines.add_argument('--egaroucid', metavar='PATH', help='Egaroucid for Console 7.8.2 executable; native console with game time control (standard 8x8 only)')
    parser.add_argument('--egaroucid-level', type=int, default=0, metavar='N', help='With --egaroucid: play Egaroucid level N (1..60) instead of game time control; 0 keeps time control. The per-turn cap still applies')
    parser.add_argument('--edax-eval', metavar='PATH', help='With --edax: eval.dat path; default is data/eval.dat beside the executable or its parent')
    parser.add_argument('--edax-protocol', choices=('xboard', 'nboard'), default='xboard', help='With --edax: xboard (default since v1.4.8) or nboard, the connection used before v1.4.8 (the connector sets each turn\'s time; no pondering)')
    # Since 2026-11-13 --engine always keeps one process per game. The old flag is accepted and ignored so existing commands keep working.
    parser.add_argument('--engine-persistent', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--nboard-depth', type=int, default=0, help='Send "set depth N" to the NBoard engine, or "sd N" to Edax over xboard (0 = engine default)')
    parser.add_argument('--engine-name', help='Reported engine name, without commands or paths')
    parser.add_argument('--engine-threads', type=int, metavar='N', help='Actual engine threads per game, shown on the bot record. Required for unattended --engine/--nboard; prompted on a terminal if omitted. With --edax/--egaroucid it is also passed to the engine (default 4 when the computer has more than 4 logical CPUs, otherwise 1)')
    parser.add_argument('--engine-hash-mb', type=float, metavar='MB', help='Hash-table capacity per engine in MB. With --edax/--egaroucid it sets the table to the largest power-of-two size not above MB (Edax -h, Egaroucid -hash) and reports that size; without it Edax keeps edax.ini and Egaroucid uses 32 MB. For other engines it is report only (0..1048576)')
    parser.add_argument('--engine-ponder', choices=('on', 'off'), help='With --edax or --egaroucid, on lets the engine keep searching during the opponent\'s time (Edax -ponder on and "hard", Egaroucid -ponder); default off. Edax cannot ponder with --edax-protocol nboard. For other engines it only reports the setting')
    parser.add_argument('--cpu-name', help='Optional reported CPU model')
    parser.add_argument('--max-think-seconds', type=float, default=10, help='Optional per-turn cap; default 10s, 0 uses the remaining game clock')
    parser.add_argument('--once-match', type=int, help='Only process this match; exit when finished, aborted, expired or rejected')
    parser.add_argument('--max-runtime', type=float, help='Stop after this many seconds (engine cleanup follows)')
    parser.add_argument('--save-games', metavar='DIR', help='Save every finished game (the server game record, one JSON line '
                        'per game) in DIR/games-YYYY-MM.ndjson. Games that ended while the connector was stopped or the stream '
                        'was down are filled with agent_games from the newest saved game; an empty folder starts from now. '
                        'Off by default: nothing is saved without it. A game takes about 4 KB (about 6 KB on 10x10), so 1,400 '
                        'games a day come to about 6 MB a day and 180 MB a month; delete old month files yourself')
    parser.add_argument('--timing-log', metavar='PATH', help='Append one line per submitted move with where the time went '
                        '(update seen, waiting for a search slot, engine, submitting); off by default')
    args = parser.parse_args()
    if args.edax_eval and not args.edax:
        parser.error('--edax-eval requires --edax')
    if args.edax_protocol != 'xboard' and not args.edax:
        parser.error('--edax-protocol requires --edax')
    if args.nboard_depth and not (args.nboard or args.edax):
        parser.error('--nboard-depth requires --nboard or --edax')
    if args.egaroucid_level and not args.egaroucid:
        parser.error('--egaroucid-level requires --egaroucid')
    if not 0 <= args.egaroucid_level <= 60:
        parser.error('--egaroucid-level must be 0..60')
    for value in (args.engine_name, args.cpu_name):
        if value is not None and len(value) > 120:
            parser.error('reported names must be at most 120 characters')
    if args.engine_threads is not None and not 1 <= args.engine_threads <= 1024:
        parser.error('--engine-threads must be 1..1024')
    if args.engine_hash_mb is not None and (not math.isfinite(args.engine_hash_mb) or not 0 <= args.engine_hash_mb <= 1048576):
        parser.error('--engine-hash-mb must be finite and 0..1048576')
    if args.engine_ponder == 'on' and args.edax and args.edax_protocol == 'nboard':
        parser.error('--engine-ponder on is not available with --edax-protocol nboard: Edax 4.6 does not ponder in NBoard mode')
    engine_name = args.engine_name or ('Edax' if args.edax else 'Egaroucid' if args.egaroucid else None)
    # Resolve before creating engines or contacting the site; all modes must report a known allocation.
    threads = resolve_engine_threads(args, parser)
    args.engine_threads = threads
    environment = {'cpu': detect_cpu(args.cpu_name), 'engine': engine_name, 'os': platform.system(), 'example_engine': False}
    if threads:
        environment['engine_threads'] = threads
    hash_args, hash_mb = adapter_hash_setting(args, parser)
    if hash_mb is None:
        hash_mb = configured_hash_mb(args)
    if hash_mb is not None:
        environment['hash_mb'] = hash_mb
    ponder = configured_ponder(args)
    if ponder is not None:
        environment['ponder'] = ponder
    try:
        if args.edax:
            executable = native_executable(args.edax)
            folder = os.path.dirname(executable)
            candidates = [args.edax_eval] if args.edax_eval else [os.path.join(folder, 'data', 'eval.dat'), os.path.join(os.path.dirname(folder), 'data', 'eval.dat')]
            evaluation = next((os.path.abspath(os.path.expanduser(p)) for p in candidates if os.path.isfile(os.path.expanduser(p))), None)
            if not evaluation:
                raise ValueError('Edax eval.dat not found; specify --edax-eval PATH')
            xboard = args.edax_protocol == 'xboard'
            command = [executable, '-xboard' if xboard else '-nboard', '-eval-file', evaluation, '-n-tasks', str(threads),
                       '-ponder', 'on' if xboard and args.engine_ponder == 'on' else 'off'] + (hash_args or [])
        elif args.egaroucid:
            command = [native_executable(args.egaroucid), '-quiet', '-noboard', '-showvalue', '-thread', str(threads)] + (hash_args or ['-hash', '20'])
            if args.engine_ponder == 'on':
                command.append('-ponder')
            if args.egaroucid_level:
                command += ['-level', str(args.egaroucid_level)]
        else:
            command = shlex.split(args.engine or args.nboard, posix=not WINDOWS)
    except ValueError as error:
        parser.error(str(error))
    # The bundled example engine marks the bot as practising: the site leaves it out of automatic matches
    # and does not rate bot-vs-bot games it plays. Restart with your own engine to be matched and rated.
    if args.engine and any(os.path.basename(part.strip('\'"')) == 'bot_match_example_engine.py' for part in command):
        environment['practice'] = True
        environment['example_engine'] = True
        environment['engine'] = engine_name or 'bot_match_example_engine.py'
    if not command or not math.isfinite(args.max_think_seconds) or not 0 <= args.max_think_seconds <= 300:
        parser.error('invalid engine command or thinking limit')
    if not 0 <= args.nboard_depth <= 60:
        parser.error('--nboard-depth must be 0..60')
    if args.once_match is not None and args.once_match == 0:
        parser.error('--once-match must be a non-zero ID')
    if args.max_runtime is not None and (not math.isfinite(args.max_runtime) or args.max_runtime <= 0):
        parser.error('--max-runtime must be finite and positive')
    if args.no_stream:
        parser.error('--no-stream was removed in v1.5.0: agent_poll is removed on 2026-11-22 00:00 (UTC+9) and the state '
                     'comes only from the event stream')
    token = os.environ.get('FS_BOT_TOKEN', '')
    try:
        api = API(args.api, token)
        stream_url = None
        if True:   # v1.5.0: the stream is the only source of state (no agent_poll)
            api_url = urllib.parse.urlsplit(args.api)
            stream_url = args.stream_url or '%s://%s/flipsight_api/bot-stream' % (api_url.scheme, api_url.netloc)
            # The token goes to this URL too: only the API host itself is accepted.
            if check_https(stream_url, 'stream URL').netloc != api_url.netloc:
                raise ValueError('stream URL must be on the API host')
            # v1.4.10: ask for the state after hello and for this bot's view in every event. A server that does not
            # know the parameter ignores it (its hello has no state_follows) and the connector polls as before.
            # v1.6.0: moves=1 asks for move events on standard boards (apply_move_event); a server that does not know
            # it says nothing of moves in hello and keeps sending full match events, which work as before.
            stream_url += '?state=1&moves=1'
    except ValueError as error:
        parser.error(str(error))

    # v1.5.2: --save-games. The folder is checked (and its newest saved game read) before anything is started.
    saver, filler = None, None
    if args.save_games:
        try:
            saver = GameSaver(args.save_games)
        except OSError as error:
            parser.error('cannot use --save-games folder: %s' % error)
        filler = GameFiller(args.api, token, saver, int(time.time() * 1000))
        filler.start()

    wake = threading.Event()
    events = queue.SimpleQueue()
    stream = None
    if stream_url:
        stream = EventStream(stream_url, token, wake, events)
        stream.start()
    engines_by_game = {}
    engine_lock = threading.Lock()
    environment_lock = threading.Lock()
    def report_engine_environment(reported):
        with environment_lock:
            merge_engine_environment(environment, reported)
    def reported_environment():
        with environment_lock:
            return dict(environment)
    # Games thinking at the same time follow "Max simultaneous games" in bot management (the bot's max_games),
    # counted like the site: both boards of a bot match are one game and think together in one slot. So with
    # max games 2 a bot match plus a human game run 3 engines. Set threads x (max games + 1) to fit your CPU.
    searches = {'limit': 2, 'games': {}}   # game (match id) -> searches running
    searches_changed = threading.Condition()
    timings = {}   # --timing-log: turn key -> local monotonic stamps
    def stamp(tkey, name):
        entry = timings.get(tkey)
        if entry is not None:
            entry[name] = time.monotonic()
    def run_engine(request, deadline, cancel, queued_at, tkey=None):
        # A game over the limit waits for a free slot (the other board of a bot match already
        # holds its game's slot). Charge that wait to its actual clock, and keep persistent
        # state separate for every room/leg.
        game = request['match_id']
        games = searches['games']
        with searches_changed:
            while game not in games and len(games) >= searches['limit']:
                left = deadline - time.monotonic()
                if cancel.is_set() or left <= 0:
                    raise TimeoutError('queued search cancelled or expired')
                searches_changed.wait(min(left, .2))
            games[game] = games.get(game, 0) + 1
        try:
            stamp(tkey, 'start')
            move = search(request, deadline, cancel, queued_at)
            stamp(tkey, 'end')
            return move
        finally:
            with searches_changed:
                if games[game] > 1:
                    games[game] -= 1
                else:
                    del games[game]
                searches_changed.notify_all()
    def search(request, deadline, cancel, queued_at):
        elapsed = int((time.monotonic() - queued_at) * 1000)
        request = dict(request, remaining_ms=max(0, request['remaining_ms'] - elapsed),
                       budget_ms=max(0, int((deadline - time.monotonic()) * 1000)))
        if cancel.is_set() or request['budget_ms'] <= 0:
            raise TimeoutError('queued search cancelled or expired')
        if args.engine_threads:
            request['engine_threads'] = args.engine_threads   # engines may size their search to this
        factory = (lambda: EdaxEngine(command, args.nboard_depth, args.engine_ponder == 'on') if args.edax_protocol == 'xboard'
                   else EdaxNBoardEngine(command, args.nboard_depth)) if args.edax else (
            (lambda: EgaroucidEngine(command, args.egaroucid_level)) if args.egaroucid else (
            (lambda: NBoardEngine(command, args.nboard_depth)) if args.nboard else (
            (lambda: PersistentJsonEngine(command, report_engine_environment)))))
        key = (request['match_id'], request['leg'], request.get('round_no'), request.get('room_created_at'))
        with engine_lock:
            if key not in engines_by_game:
                engines_by_game[key] = factory()
            engine = engines_by_game[key]
        return engine.move(request, deadline, cancel)

    jobs, submitted, failed = {}, set(), set()
    # Moves the server has not received yet: turn key -> {'at': next try, 'tries': n} for temporary errors,
    # the local time each turn's clock runs out (never wait past it), and every turn whose send failed once.
    move_retry, clock_end, undelivered = {}, {}, set()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_SEARCHES + 1)   # + the second board of a bot match
    failures = 0
    poll_failures, poll_not_before = 0, 0.0   # a failed poll while a move waits is retried on its own schedule
    # The last agent_poll result, kept current with view "agent" stream events, and the server clock behind it
    # (server_now_ms and the local time it was taken) so remaining clocks are computed for "now".
    base, server_clock = None, None
    # v1.5.0: with the stream the state comes on connect; nothing to ask for before it (or STATE_WAIT_SEC) is due.
    last_poll, poll_every, poll_now = None, 0.0, stream is None
    # v1.4.9: extra safety polls (PollPlanner), whether the stream counts as up (set each round), and the clock of the
    # last poll (server_now_ms, local time in the middle of it) for late-event lines.
    planner, stream_ok, poll_clock, last_ping = PollPlanner(), False, None, None
    # v1.4.10: local time of the latest hello and, after a hello that announced a state, when to stop waiting for it.
    hello_at, state_due = None, None
    # v1.5.0: with the stream there is no agent_poll at all. Reconnects asked for (times, for the budget), low-clock
    # resyncs per match, the environment last reported with agent_environment and when to look again.
    resync_times, opp_resyncs, env_sent, env_next = [], {}, None, 0.0
    # v1.5.1: match id -> local time a move of it was refused with 409; reasons to reconnect raised outside the check
    # below (a move POST that timed out); local time of the last reconnect asked for (merging, RESYNC_MERGE_SEC).
    conflicts, resync_asks, last_resync = {}, [], -1e9
    if stream is not None:
        planner.echo_wait = ECHO_WAIT_STREAM_SEC
    seen = {}   # --timing-log: match id -> (how the latest state arrived: event or poll, local time)
    timing_file = None
    if args.timing_log:
        try:
            timing_file = open(args.timing_log, 'a', encoding='ascii', buffering=1)
        except OSError as error:
            parser.error('cannot open --timing-log: %s' % error)
    once_human_seen = False
    limit = {'hit': False}
    timer = None
    if args.max_runtime is not None:
        def stop_runtime():
            limit['hit'] = True
            wake.set()
        timer = threading.Timer(args.max_runtime, stop_runtime)
        timer.daemon = True
        timer.start()
    previous_term = None
    if hasattr(signal, 'SIGTERM'):
        def stop_signal(signum, frame):
            raise KeyboardInterrupt()
        try:
            previous_term = signal.signal(signal.SIGTERM, stop_signal)
        except (ValueError, OSError):
            previous_term = None
    try:
        while True:
            start = time.monotonic()
            wake.clear()   # clear before polling: anything that fires during this round wakes the next wait
            if limit['hit']:
                raise RuntimeLimit()
            if stream is not None and stream.denied:
                print('Bot credential rejected by the event stream. Stopping.', file=sys.stderr)
                return 2
            try:
                late = []   # (lag ms, match id) of stream events that arrived more than EVENT_LATE_MS after they were sent
                move_problems = []   # v1.6.0: move events that need a resync; a state later in this round answers them
                while True:
                    try:
                        event = events.get_nowait()
                    except queue.Empty:
                        break
                    received = event.get('_received', time.monotonic())
                    if event.get('type') == 'hello':
                        # v1.4.10: a ?state=1 stream announces the state that follows; wait for it instead of polling.
                        if event.get('state_follows') is True:
                            planner.human_echo = True
                            hello_at, state_due = received, received + STATE_WAIT_SEC
                        else:
                            planner.human_echo = False
                            state_due = None
                            poll_now = True
                        continue
                    if event.get('type') == 'state':
                        # The whole agent_poll answer, taken right after the stream connected: use it as a poll.
                        state_due = None
                        if not valid_state(event):
                            poll_now = True
                            continue
                        base = merge_state(base, event)
                        poll_now = False
                        del move_problems[:]   # v1.6.0: the fresh state covers what those move events missed
                        if filler is not None:
                            filler.request()   # v1.5.2: after (re)connecting, fill games that ended while the stream was down
                        if last_poll is not None:   # the first poll at start still goes out: it reports the environment
                            last_poll = received
                        server_clock = (event['server_now_ms'], received)
                        poll_clock = server_clock
                        planner.note_poll(base['matches'], min(hello_at or received, received), received)
                        for state_match in base['matches']:
                            if isinstance(state_match, dict) and state_match.get('status') == 'running':
                                seen[state_match.get('id')] = ('state', received)
                        continue
                    if event.get('type') == 'move':
                        # v1.6.0: new moves of one standard board. Rebuild that match's view from the last known one and
                        # go on below exactly as with a full match event (same server_now_ms and receive time). Moves
                        # already known are skipped; anything that does not fit resyncs (reconnect, full state again).
                        rebuilt, problem = apply_move_event(base, event)
                        if rebuilt is None:
                            if problem is not None and problem not in move_problems:
                                move_problems.append(problem)
                            continue
                        event = {'type': 'match', 'view': 'agent', 'match': rebuilt,
                                 'server_now_ms': event.get('server_now_ms'), '_received': received}
                    if event.get('type') == 'match':
                        lag = stream_lag_ms(event.get('server_now_ms'), event.get('_received'), poll_clock)
                        if lag is not None and lag > EVENT_LATE_MS:
                            late.append((lag, (event.get('match') or {}).get('id')))
                    if apply_stream_event(base, event):
                        match = event['match']
                        seen[match['id']] = ('event', received)
                        if saver is not None and match.get('status') in ('finished', 'aborted'):
                            record = game_record(match)
                            try:
                                if record is not None:
                                    saver.save(record)
                                else:
                                    filler.request(GAMES_LATE_RECORD_SEC)   # record not ready (or an older server): fill later
                            except OSError as error:
                                log('Game records: cannot save match %s (%s).' % (match.get('id'), error))
                        if any(m is match for m in base['matches']):
                            planner.note_event(match, received)
                        if stream is None and match.get('status') == 'pending' and str(match.get('bot_b')) == str((base.get('bot') or {}).get('id')):
                            poll_now = True   # an invitation: accept on fresh slot counts (bot.games_now), as before v1.4.10
                        now_ms = event.get('server_now_ms')
                        if type(now_ms) in (int, float) and server_clock is not None and now_ms >= server_clock[0]:
                            server_clock = (now_ms, time.monotonic())
                    else:
                        poll_now = True
                resync_asks.extend(p for p in move_problems if p not in resync_asks)   # v1.6.0
                if late:
                    log('%d stream event(s) arrived late, up to %.1fs after the server sent them (match %s).'
                        % (len(late), max(lag for lag, _ in late) / 1000, ', '.join(sorted({str(m) for _, m in late}))))
                ping = stream.ping if stream is not None else None
                if ping is not None and ping is not last_ping:
                    last_ping = ping
                    lag = stream_lag_ms(ping[0], ping[1], poll_clock)
                    if lag is not None and lag > EVENT_LATE_MS:
                        log('Stream ping arrived %.1fs after the server sent it.' % (lag / 1000))
                now = time.monotonic()
                stream_ok = stream is not None and stream.healthy and now - stream.last_line < STREAM_SILENCE_SEC
                overdue = planner.overdue_echoes(now)
                if overdue and stream_ok:
                    for key in overdue:
                        log('No stream echo of the move for match %s leg %s revision %s within %.1fs; %s.'
                            % (key[0], key[1], key[2], planner.echo_wait, 'polling now' if stream is None else 'resyncing'))
                    if stream is None:
                        poll_now = True
                if state_due is not None and now >= state_due:
                    log('No state from the event stream within %.0fs of connecting; %s.' % (STATE_WAIT_SEC, 'polling' if stream is None else 'resyncing'))
                    state_due = None
                    poll_now = True
                if stream is not None:
                    # v1.5.0 (bot API v2): no agent_poll. Report the environment with agent_environment at start and when the
                    # engine reports new values, and resync by reconnecting the stream instead of polling.
                    current_env = reported_environment()
                    if current_env != env_sent and now >= env_next:
                        env_next = now + ENV_REPORT_EVERY_SEC
                        try:
                            reply = api.call('agent_environment', environment=current_env)
                            env_sent = current_env
                            if base is not None and isinstance(reply.get('bot'), dict):
                                base['bot'] = reply['bot']
                        except APIError as error:
                            if not error.transient:
                                raise
                            log('Environment report failed (%s); trying again in %ds.' % (describe_error(error, api.token), ENV_REPORT_EVERY_SEC))
                    reasons = []
                    if poll_now:
                        reasons.append('state needed')
                        poll_now = False
                    if overdue and stream_ok:
                        reasons.append('no echo of the move')
                    for match_id, at in list(conflicts.items()):
                        if planner.state_at.get(match_id, -1e9) > at:
                            del conflicts[match_id]   # the stream brought that match after the refused move
                        elif now - at >= CONFLICT_NEWS_SEC:
                            del conflicts[match_id]
                            reasons.append('no news of match %s %.0fs after a refused move' % (match_id, CONFLICT_NEWS_SEC))
                    reasons.extend(resync_asks)
                    del resync_asks[:]
                    if base is not None and stream_ok:
                        for match_id, _leg, opp_ms in waiting_clocks(base, (base.get('bot') or {}).get('id'), opponent=True):
                            count, asked = opp_resyncs.get(match_id, (0, 0.0))
                            since = max(planner.state_at.get(match_id, now), asked)
                            if count < OPP_CLOCK_RESYNCS_PER_MATCH and now - since >= opponent_clock_resync_after(opp_ms):
                                opp_resyncs[match_id] = (count + 1, now)   # counted even when the budget skips it
                                reasons.append("match %s: no news for %.0fs, past the opponent's clock" % (match_id, now - since))
                                break
                        live = {m.get('id') for m in base['matches'] if isinstance(m, dict)}
                        for match_id in [m for m in opp_resyncs if m not in live]:
                            del opp_resyncs[match_id]
                    if reasons:
                        resync_times[:] = [t for t in resync_times if now - t < RESYNC_WINDOW_SEC]
                        if stream.resync is not None or not stream.healthy:
                            pass   # v1.5.1: a reconnect is under way; its state answers these reasons too
                        elif now - last_resync < RESYNC_MERGE_SEC:
                            poll_now = True   # v1.5.1: merged into the reconnect just made; asked again after the window
                        elif len(resync_times) < RESYNC_BUDGET:
                            resync_times.append(now)
                            last_resync = now
                            stream.request_resync(', '.join(reasons))
                        else:
                            log('Resync skipped (%s): %d reconnects in the last %d minutes already.'
                                % (', '.join(reasons), RESYNC_BUDGET, RESYNC_WINDOW_SEC // 60))
                    if base is None:
                        raise WaitingForState()
                payload = base
                if server_clock is not None:
                    payload['server_now_ms'] = server_clock[0] + int((time.monotonic() - server_clock[1]) * 1000)
                challenge = incoming_challenge(payload, payload['bot']['id'], args.once_match, use_games_now=stream is None)
                if args.once_match is not None:
                    payload['matches'] = [m for m in payload.get('matches', []) if str(m.get('id')) == str(args.once_match)]
                    if args.once_match < 0:
                        if once_human_seen and not payload['matches']:
                            return 0
                        once_human_seen = once_human_seen or bool(payload['matches'])
                    if any(m.get('status') in ('finished', 'aborted', 'expired', 'rejected') for m in payload['matches']):
                        print('Selected match ended; connector stopped.', file=sys.stderr)
                        return 0
                my_id = payload['bot']['id']
                max_games = payload['bot'].get('max_games')
                wanted = max(1, min(MAX_SEARCHES, max_games if type(max_games) is int else 2))
                with searches_changed:
                    if searches['limit'] != wanted:
                        searches['limit'] = wanted
                        searches_changed.notify_all()
                turns = current_turns(payload, my_id)
                for key, (future, cancel) in list(jobs.items()):
                    if key not in turns:
                        if key in undelivered:
                            print('Server state moved on for match %s leg %s (revision %s); dropping the outdated move.'
                                  % key[:3], file=sys.stderr)
                        cancel.set()
                        future.cancel()
                        del jobs[key]
                        timings.pop(key, None)
                for key in [k for k in set(move_retry) | set(clock_end) | undelivered if k not in jobs]:
                    move_retry.pop(key, None)
                    clock_end.pop(key, None)
                    undelivered.discard(key)
                active_games = {(int(m['id']), i, m.get('round_no'), m.get('room_created_at'))
                                for m in payload.get('matches', []) if m.get('status') == 'running'
                                for i, _ in enumerate((m.get('state') or {}).get('legs', [])[:2])}
                with engine_lock:
                    retired = [engines_by_game.pop(k) for k in list(engines_by_game) if k not in active_games]
                for engine in retired:
                    engine.stop()
                submitted.intersection_update(turns)
                failed.intersection_update(turns)
                if challenge is not None:
                    accepted = api.call('agent_accept', match_id=challenge)
                    # v1.4.10: the answer carries this bot's view of the started match; use it instead of polling.
                    started = accepted.get('match')
                    if isinstance(started, dict) and started.get('status') == 'running' and fold_match(base, started):
                        if any(m is started for m in base['matches']):
                            planner.note_event(started, time.monotonic())
                    else:
                        poll_now = True
                    wake.set()  # Start on the new boards at once.
                busy = False
                for key, request in turns.items():
                    if key in submitted or key in failed:
                        continue
                    if key not in jobs:
                        cancel = threading.Event()
                        budget = thinking_budget(request['remaining_ms'], args.max_think_seconds)
                        if budget <= 0:
                            log('No time left for match %s leg %s revision %s (%d ms on the clock, 750 ms kept back); '
                                'not starting a search.' % (key[0], key[1], key[2], request['remaining_ms']))
                            failed.add(key)
                            continue
                        request['budget_ms'] = int(budget * 1000)
                        queued_at = time.monotonic()
                        if timing_file is not None:
                            how, at = seen.get(key[0], ('unknown', queued_at))
                            timings[key] = {'src': how, 'seen': at, 'queued': queued_at}
                        future = executor.submit(run_engine, request, queued_at + budget, cancel, queued_at, key)
                        future.add_done_callback(lambda _f: wake.set())   # submit as soon as the engine answers
                        jobs[key] = (future, cancel)
                        clock_end[key] = queued_at + request['remaining_ms'] / 1000
                    future, _ = jobs[key]
                    if not future.done():
                        busy = True
                        continue
                    try:
                        move = future.result()
                    except Exception:
                        print('Engine failed for match %s leg %s; awaiting timeout or manual recovery.' % key[:2], file=sys.stderr)
                        failed.add(key)
                        del jobs[key]
                        timings.pop(key, None)
                        continue
                    retry = move_retry.get(key)
                    if retry is not None and time.monotonic() < retry['at']:
                        busy = True
                        continue
                    # If the response is lost, the next poll observes the revision. Otherwise retry
                    # the exact move with the same expected revision; never compute twice.
                    # Human matches (negative id) also need the room's round and creation time.
                    extra = {'round_no': key[3], 'room_created_at': key[4]} if key[0] < 0 else {}
                    # v1.5.3: an engine answer with an eval comes as (move, eval). Only bot matches keep it on the site.
                    evaluation = None
                    if isinstance(move, tuple):
                        move, evaluation = move
                    if evaluation is not None and key[0] > 0 and abs(evaluation) <= eval_limit(request):
                        extra['eval'] = evaluation
                    stamp(key, 'post')
                    # v1.5.1: wait at most a third of this leg's clock for the answer (1.5-8s), so a dead network path
                    # does not hold the other board for 8s.
                    left = clock_end.get(key, time.monotonic() + 24) - time.monotonic()
                    post_timeout = min(MOVE_POST_TIMEOUT_MAX, max(MOVE_POST_TIMEOUT_MIN, left / 3))
                    try:
                        api.call('agent_move', timeout=post_timeout, match_id=key[0], leg=key[1], revision=key[2], move=move, **extra)
                    except Exception as error:
                        undelivered.add(key)
                        if isinstance(error, APIError) and error.status == 409:
                            # v1.5.1: the server moved on (often the first try of this move did arrive): drop this
                            # move only and go on with the other boards; reconnect if the stream says nothing soon.
                            print('Move for match %s leg %s revision %s refused (HTTP 409, the server moved on); dropping it.'
                                  % key[:3], file=sys.stderr)
                            submitted.add(key)
                            del jobs[key]
                            timings.pop(key, None)
                            move_retry.pop(key, None)
                            clock_end.pop(key, None)
                            undelivered.discard(key)
                            conflicts[key[0]] = time.monotonic()
                            continue
                        if not (isinstance(error, APIError) and error.transient):
                            raise
                        if error.status is None and stream is not None and 'timed out' in str(error):
                            resync_asks.append('a move POST timed out')   # v1.5.1: the stream likely ran on the same path
                        # A temporary error (429, 5xx, timeout, connection): send the same move again almost at once,
                        # without waiting for a poll. The game and revision are unchanged as far as we know.
                        tries = (retry or {}).get('tries', 0) + 1
                        wait = retry_delay(tries, error.retry_after, clock_end.get(key, 0) - time.monotonic())
                        if wait is None:
                            print('Move for match %s leg %s revision %s not delivered (%s); the game clock runs out before '
                                  'another try.' % (key[0], key[1], key[2], describe_error(error, api.token)), file=sys.stderr)
                            failed.add(key)
                            del jobs[key]
                            timings.pop(key, None)
                            poll_now = True
                            continue
                        move_retry[key] = {'at': time.monotonic() + wait, 'tries': tries}
                        print('Move for match %s leg %s revision %s not delivered (%s); sending it again in %.2fs (try %d).'
                              % (key[0], key[1], key[2], describe_error(error, api.token), wait, tries + 1), file=sys.stderr)
                        busy = True
                        continue
                    stamp(key, 'posted')
                    if stream_ok:
                        planner.note_posted(key, time.monotonic())
                    if retry is not None:
                        print('Move for match %s leg %s revision %s delivered on try %d.' % (key[0], key[1], key[2], retry['tries'] + 1),
                              file=sys.stderr)
                    move_retry.pop(key, None)
                    clock_end.pop(key, None)
                    undelivered.discard(key)
                    submitted.add(key)
                    del jobs[key]
                    entry = timings.pop(key, None)
                    if timing_file is not None and entry is not None and all(k in entry for k in ('start', 'end', 'post', 'posted')):
                        ms = lambda a, b: int(round((entry[b] - entry[a]) * 1000))
                        try:
                            timing_file.write('timing at=%.3f match=%d leg=%d rev=%d src=%s seen_to_queue=%d slot_wait=%d engine=%d '
                                              'done_to_post=%d post=%d total=%d conn=%s\n' % (
                                time.time(), key[0], key[1], key[2], entry['src'], ms('seen', 'queued'), ms('queued', 'start'),
                                ms('start', 'end'), ms('end', 'post'), ms('post', 'posted'), ms('seen', 'posted'),
                                'reused' if api.last_reused else 'new'))
                        except OSError:
                            pass
                failures = 0
                active = any(m.get('status') == 'running' for m in payload.get('matches', []))
                stream_ok = stream is not None and stream.healthy
                if stream_ok and time.monotonic() - stream.last_line >= STREAM_SILENCE_SEC:
                    stream.mark_silent()   # open but silent: down until the reader connects again
                    stream_ok = False
                if stream is not None:
                    poll_every = 30.0   # v1.5.0: no polls with the stream; wake at least this often to look around
                elif stream_ok:
                    poll_every = SAFETY_POLL_GAME_SEC if active else SAFETY_POLL_IDLE_SEC
                else:
                    poll_every = 1 if active or busy else 5
                now = time.monotonic()
                delay = poll_every - (now - (last_poll or now)) if stream is None else poll_every
                if poll_now or delay <= 0:
                    delay = max(delay, poll_not_before - now)   # a due poll may be held back after a failure
                if move_retry:
                    delay = min(delay, min(r['at'] for r in move_retry.values()) - now)
                if stream is not None and stream_ok:
                    # v1.5.0: wake for a due echo, for a board past the opponent's clock and to notice silence.
                    extra = [planner.next_echo_at()]
                    for match_id, _leg, opp_ms in waiting_clocks(payload, my_id, opponent=True):
                        count, asked = opp_resyncs.get(match_id, (0, 0.0))
                        if count < OPP_CLOCK_RESYNCS_PER_MATCH:
                            at = max(planner.state_at.get(match_id, now), asked) + opponent_clock_resync_after(opp_ms)
                            extra.append(max(at, now + 0.2))
                    if reported_environment() != env_sent:
                        extra.append(env_next)
                    extra.extend(at + CONFLICT_NEWS_SEC for at in conflicts.values())   # v1.5.1
                    if resync_asks or poll_now:
                        extra.append(max(now + 0.2, last_resync + RESYNC_MERGE_SEC))
                    for at in extra:
                        if at is not None:
                            delay = min(delay, at - now)
                    delay = min(delay, stream.last_line + STREAM_SILENCE_SEC - now)
                elif stream_ok:
                    # v1.4.9: wake for a due echo or low-clock poll (never before a held-back poll) and to notice silence.
                    extra = list(planner.low_clock_plan(waiting_clocks(payload, my_id), last_poll).values())
                    extra.append(planner.next_echo_at())
                    for at in extra:
                        if at is not None:
                            delay = min(delay, max(at, poll_not_before) - now)
                    delay = min(delay, stream.last_line + STREAM_SILENCE_SEC - now)
                if state_due is not None:
                    delay = min(delay, state_due - now)   # poll if the announced state does not come
                start = time.monotonic()
            except WaitingForState:
                delay = 0.5   # v1.5.0: nothing to do before the stream sends the state
                if state_due is not None:
                    delay = min(delay, max(0.05, state_due - time.monotonic()))
            except PermissionError:
                print('Bot credential rejected. Stopping.', file=sys.stderr)
                return 2
            except (RuntimeError, ValueError, KeyError, TypeError, urllib.error.URLError, OSError) as error:
                failures += 1
                poll_now = True
                retry_after = getattr(error, 'retry_after', None)
                # During a game a temporary error, or a 409 (the server state may have moved on), is followed by a quick
                # poll: 0.2-0.5s for the first tries, then 2, 4, ... 30s. Idle errors back off as before.
                if isinstance(error, APIError) and (error.transient or error.status == 409) and has_running_match(base):
                    delay = retry_delay(failures, retry_after)
                else:
                    delay = min(30, 2 ** min(failures, 5)) + random.random()
                    if retry_after is not None:
                        delay = max(delay, retry_after)
                print('Connection/state error (%s); retrying in %.2fs without replaying an outdated move.'
                      % (describe_error(error, api.token), delay), file=sys.stderr)
            except (KeyboardInterrupt, RuntimeLimit):
                raise
            except Exception as error:
                # Never let an unexpected error (for example an http.client error while the server restarts,
                # or an engine that fails to stop) end the connector: a stopped connector loses its games by disconnect.
                failures += 1
                poll_now = True
                delay = min(30, 2 ** min(failures, 5)) + random.random()
                print('Unexpected error (%s); retrying in %.2fs.' % (describe_error(error, api.token), delay), file=sys.stderr)
            wake.wait(max(.05, delay - (time.monotonic() - start)))
    except RuntimeLimit:
        print('Runtime limit reached; stopping connector and engines.', file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 0
    finally:
        if timer is not None:
            timer.cancel()
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
        if stream is not None:
            stream.stopping.set()
        if filler is not None:
            filler.stopping.set()
            filler.wake.set()
        for future, cancel in jobs.values():
            cancel.set()
        executor.shutdown(wait=True, cancel_futures=True)
        for engine in engines_by_game.values():
            engine.stop()
        if timing_file is not None:
            timing_file.close()


if __name__ == '__main__':
    sys.exit(main())
