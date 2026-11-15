#!/usr/bin/env python3
"""FlipSight bot-match connector. Python 3.9+, standard library only, Windows/macOS/Linux.

Credentials stay in FS_BOT_TOKEN. Engine modes:
  --engine CMD   a long-running JSON-lines engine, one process per game: read a request line,
                 write one {"move": index} line, repeat (do not wait for end of input)
  --nboard CMD   a long-running engine that speaks the NBoard protocol (one engine per leg)
  --edax PATH    Edax 4.6 executable; NBoard setup and per-turn time allocation are automatic
  --egaroucid PATH  Egaroucid for Console 7.8.2; native setboard/settime/go commands
                 --egaroucid-level N (1..60) plays a fixed level instead (no settime)
The connector listens to the event stream (/flipsight_api/bot-stream) and reacts to opponent moves
immediately; agent_poll stays as the safety net (every 10s while the stream is up, 1s/5s otherwise).
"""
import argparse
import concurrent.futures
import json
import math
import os
import platform
import queue
import random
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

WINDOWS = os.name == 'nt'
DIRECTIONS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


class RuntimeLimit(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('redirect refused')


def check_https(url, what):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(what + ' must be a credential-free HTTPS URL without query or fragment')
    return parsed


class API:
    def __init__(self, url, token):
        check_https(url, 'API')
        if not token or '\r' in token or '\n' in token:
            raise ValueError('FS_BOT_TOKEN is missing or invalid')
        self.url, self.token = url, token
        self.opener = urllib.request.build_opener(NoRedirect())

    def call(self, action, **fields):
        request = urllib.request.Request(self.url, data=json.dumps(dict(action=action, **fields)).encode(),
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
        try:
            with self.opener.open(request, timeout=8) as response:
                body = response.read(1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            # Never log response bodies: a proxy could echo the authorization header.
            if error.code in (401, 403):
                raise PermissionError('authentication denied') from None
            raise RuntimeError('HTTP ' + str(error.code)) from None
        if len(body) > 1024 * 1024:
            raise RuntimeError('response too large')
        data = json.loads(body)
        if not isinstance(data, dict) or data.get('ok') is not True:
            raise RuntimeError('API rejected request')
        return data


class EventStream(threading.Thread):
    """Reads NDJSON events. Any 'match' event wakes the main loop, which then polls once."""

    def __init__(self, url, token, wake):
        super().__init__(daemon=True)
        self.url, self.token, self.wake = url, token, wake
        self.opener = urllib.request.build_opener(NoRedirect())
        self.healthy = False
        self.denied = False
        self.stopping = threading.Event()

    def run(self):
        backoff = 1.0
        while not self.stopping.is_set():
            try:
                request = urllib.request.Request(self.url, headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/x-ndjson'})
                # The server sends a ping every 10s, so a 30s read timeout means the stream is dead.
                with self.opener.open(request, timeout=30) as response:
                    for raw in response:
                        if self.stopping.is_set():
                            return
                        line = raw.strip()
                        if not line:
                            continue
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            continue
                        if event.get('type') == 'hello':
                            self.healthy, backoff = True, 1.0
                            self.wake.set()
                        elif event.get('type') == 'match':
                            self.wake.set()
            except urllib.error.HTTPError as error:
                if error.code in (401, 403):
                    self.denied = True
                    self.wake.set()
                    return
            except Exception:
                pass
            was_healthy, self.healthy = self.healthy, False
            if was_healthy:
                self.wake.set()   # fall back to fast polling right away
            self.stopping.wait(backoff + random.random())
            backoff = min(30.0, backoff * 2)


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
    def __init__(self, command):
        self.command = command
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
                self.process.stdin.write((json.dumps(request) + '\n').encode())
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
                # Only bounded numeric diagnostics, never arbitrary engine text or credentials.
                timing = {key: answer[key] for key in ('used_ms', 'soft_ms', 'normal_ms', 'hard_ms', 'over_ms', 'stop_reason')
                          if type(answer.get(key)) is int}
                if timing:
                    timing.update({key: request[key] for key in ('match_id', 'leg', 'revision', 'remaining_ms')
                                   if type(request.get(key)) is int})
                    print('Engine time: ' + json.dumps(timing), file=sys.stderr, flush=True)
                return move
        except BaseException:
            self.stop()
            raise
        finally:
            self.lock.release()


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
                return square_index(reply.split()[0].split('/', 1)[0])  # NBoard may append score and elapsed time.
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


class EdaxEngine(NBoardEngine):
    """Edax 4.6: ping stops its search and returns the last completed result."""

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
                return move
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
                    if len(text) == 2 and text[0].lower() in 'abcdefgh' and text[1] in '12345678':
                        return square_index(text)
                    if text.lower() in ('ps', 'pa', 'pass'):
                        return -1
                    return None
                try:
                    move = self._read_until(accept, deadline, cancel)
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
                return move
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
            if type(now) in (int, float) and type(leg.get('turn_started_ms')) in (int, float):
                remaining = max(0, remaining - max(0, int(now - leg['turn_started_ms'])))
            key = (int(match['id']), leg_index, int(leg['revision']), match.get('round_no'), match.get('room_created_at'))
            legal=leg.get('legal_moves')
            if legal is None and mode=='std':legal=legal_moves(board,color) # older server compatibility
            if not isinstance(legal,list) or any(type(v) is not int or not 0<=v<n*n or board[v]!=0 for v in legal):
                raise ValueError('invalid legal moves received')
            turns[key] = {'board': board, 'color': color, 'legal_moves': legal, 'game_mode':mode,'board_size':n,
                'blocked_squares':state.get('blocked_squares',[]),'placed':leg.get('placed',{}),'remaining_ms': remaining,
                'match_id': key[0], 'leg': key[1], 'revision': key[2], 'history': leg.get('history', []),
                'round_no': match.get('round_no'), 'room_created_at': match.get('room_created_at')}
    return turns


def thinking_budget(remaining_ms, max_think_seconds):
    """Zero disables the optional move cap; the actual game clock still bounds the job."""
    available = max(0, remaining_ms - 750) / 1000
    return min(max_think_seconds, available) if max_think_seconds else available


def incoming_challenge(payload, my_id, selected=None):
    """Pending pairs already reserve capacity; accept within the configured total load."""
    bot = payload.get('bot') or {}
    limit = bot.get('max_games', 2)
    limit = max(1, min(8, limit if type(limit) is int else 2))
    load = bot.get('games_now')
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


MAX_SEARCHES = 8   # the site's highest max_games


def main():
    parser = argparse.ArgumentParser(description='FlipSight bot connector')
    parser.add_argument('--api', default='https://othelloclub.com/flipsight/bot_match_api.php')
    parser.add_argument('--stream-url', help='Event stream URL; default is /flipsight_api/bot-stream on the API host')
    parser.add_argument('--no-stream', action='store_true', help='Do not use the event stream; poll only')
    engines = parser.add_mutually_exclusive_group(required=True)
    engines.add_argument('--engine', help='Local command, one process per game: repeatedly read one JSON request line and write one {"move": index} line')
    engines.add_argument('--nboard', help='Local command of an engine that speaks the NBoard protocol (kept running)')
    engines.add_argument('--edax', metavar='PATH', help='Edax 4.6 executable; automatic NBoard setup and time allocation (standard 8x8 only)')
    engines.add_argument('--egaroucid', metavar='PATH', help='Egaroucid for Console 7.8.2 executable; native console with game time control (standard 8x8 only)')
    parser.add_argument('--egaroucid-level', type=int, default=0, metavar='N', help='With --egaroucid: play Egaroucid level N (1..60) instead of game time control; 0 keeps time control. The per-turn cap still applies')
    parser.add_argument('--edax-eval', metavar='PATH', help='With --edax: eval.dat path; default is data/eval.dat beside the executable or its parent')
    # Since 2026-11-13 --engine always keeps one process per game. The old flag is accepted and ignored so existing commands keep working.
    parser.add_argument('--engine-persistent', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--nboard-depth', type=int, default=0, help='Send "set depth N" to the NBoard engine (0 = engine default)')
    parser.add_argument('--engine-name', help='Reported engine name, without commands or paths')
    parser.add_argument('--engine-threads', type=int, metavar='N', help='Engine threads per game, shown on the bot record. With --edax/--egaroucid it is also passed to the engine (default 4 when the computer has more than 4 logical CPUs, otherwise 1)')
    parser.add_argument('--cpu-name', help='Optional reported CPU model')
    parser.add_argument('--max-think-seconds', type=float, default=10, help='Optional per-turn cap; default 10s, 0 uses the remaining game clock')
    parser.add_argument('--once-match', type=int, help='Only process this match; exit when finished, aborted, expired or rejected')
    parser.add_argument('--max-runtime', type=float, help='Stop after this many seconds (engine cleanup follows)')
    args = parser.parse_args()
    if args.edax_eval and not args.edax:
        parser.error('--edax-eval requires --edax')
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
    engine_name = args.engine_name or ('Edax' if args.edax else 'Egaroucid' if args.egaroucid else None)
    # Threads per game: the connector sets them itself for Edax and Egaroucid (default 4 when the computer has more
    # than 4 logical CPUs, otherwise 1); other engines report the value given here, if any.
    threads = args.engine_threads or ((4 if (os.cpu_count() or 1) > 4 else 1) if (args.edax or args.egaroucid) else None)
    environment = {'cpu': detect_cpu(args.cpu_name), 'engine': engine_name, 'os': platform.system()}
    if threads:
        environment['engine_threads'] = threads
    try:
        if args.edax:
            executable = native_executable(args.edax)
            folder = os.path.dirname(executable)
            candidates = [args.edax_eval] if args.edax_eval else [os.path.join(folder, 'data', 'eval.dat'), os.path.join(os.path.dirname(folder), 'data', 'eval.dat')]
            evaluation = next((os.path.abspath(os.path.expanduser(p)) for p in candidates if os.path.isfile(os.path.expanduser(p))), None)
            if not evaluation:
                raise ValueError('Edax eval.dat not found; specify --edax-eval PATH')
            command = [executable, '-nboard', '-eval-file', evaluation, '-n-tasks', str(threads), '-ponder', 'off']
        elif args.egaroucid:
            command = [native_executable(args.egaroucid), '-quiet', '-noboard', '-thread', str(threads), '-hash', '20']
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
    if not command or not math.isfinite(args.max_think_seconds) or not 0 <= args.max_think_seconds <= 300:
        parser.error('invalid engine command or thinking limit')
    if not 0 <= args.nboard_depth <= 60:
        parser.error('--nboard-depth must be 0..60')
    if args.once_match is not None and args.once_match == 0:
        parser.error('--once-match must be a non-zero ID')
    if args.max_runtime is not None and (not math.isfinite(args.max_runtime) or args.max_runtime <= 0):
        parser.error('--max-runtime must be finite and positive')
    token = os.environ.get('FS_BOT_TOKEN', '')
    try:
        api = API(args.api, token)
        stream_url = None
        if not args.no_stream:
            api_url = urllib.parse.urlsplit(args.api)
            stream_url = args.stream_url or '%s://%s/flipsight_api/bot-stream' % (api_url.scheme, api_url.netloc)
            # The token goes to this URL too: only the API host itself is accepted.
            if check_https(stream_url, 'stream URL').netloc != api_url.netloc:
                raise ValueError('stream URL must be on the API host')
    except ValueError as error:
        parser.error(str(error))

    wake = threading.Event()
    stream = None
    if stream_url:
        stream = EventStream(stream_url, token, wake)
        stream.start()
    engines_by_game = {}
    engine_lock = threading.Lock()
    # Searches running at the same time follow "Max simultaneous games" in bot management (the bot's max_games),
    # but at least 2 so both boards of a bot match can think together. Set threads x max games to fit your CPU.
    searches = {'limit': 2, 'active': 0}
    searches_changed = threading.Condition()
    def run_engine(request, deadline, cancel, queued_at):
        # A game over the limit waits for a free search. Charge that wait to
        # its actual clock, and keep persistent state separate for every room/leg.
        with searches_changed:
            while searches['active'] >= searches['limit']:
                left = deadline - time.monotonic()
                if cancel.is_set() or left <= 0:
                    raise TimeoutError('queued search cancelled or expired')
                searches_changed.wait(min(left, .2))
            searches['active'] += 1
        try:
            return search(request, deadline, cancel, queued_at)
        finally:
            with searches_changed:
                searches['active'] -= 1
                searches_changed.notify_all()
    def search(request, deadline, cancel, queued_at):
        elapsed = int((time.monotonic() - queued_at) * 1000)
        request = dict(request, remaining_ms=max(0, request['remaining_ms'] - elapsed),
                       budget_ms=max(0, int((deadline - time.monotonic()) * 1000)))
        if cancel.is_set() or request['budget_ms'] <= 0:
            raise TimeoutError('queued search cancelled or expired')
        if args.engine_threads:
            request['engine_threads'] = args.engine_threads   # engines may size their search to this
        factory = (lambda: EdaxEngine(command, args.nboard_depth)) if args.edax else (
            (lambda: EgaroucidEngine(command, args.egaroucid_level)) if args.egaroucid else (
            (lambda: NBoardEngine(command, args.nboard_depth)) if args.nboard else (
            (lambda: PersistentJsonEngine(command)))))
        key = (request['match_id'], request['leg'], request.get('round_no'), request.get('room_created_at'))
        with engine_lock:
            if key not in engines_by_game:
                engines_by_game[key] = factory()
            engine = engines_by_game[key]
        return engine.move(request, deadline, cancel)

    jobs, submitted, failed = {}, set(), set()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_SEARCHES)
    failures = 0
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
                payload = api.call('agent_poll', environment=environment)
                challenge = incoming_challenge(payload, payload['bot']['id'], args.once_match)
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
                wanted = max(2, min(MAX_SEARCHES, max_games if type(max_games) is int else 2))
                with searches_changed:
                    if searches['limit'] != wanted:
                        searches['limit'] = wanted
                        searches_changed.notify_all()
                turns = current_turns(payload, my_id)
                for key, (future, cancel) in list(jobs.items()):
                    if key not in turns:
                        cancel.set()
                        future.cancel()
                        del jobs[key]
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
                    api.call('agent_accept', match_id=challenge)
                    wake.set()  # Fetch the newly started boards without waiting for a stream event.
                busy = False
                for key, request in turns.items():
                    if key in submitted or key in failed:
                        continue
                    if key not in jobs:
                        cancel = threading.Event()
                        budget = thinking_budget(request['remaining_ms'], args.max_think_seconds)
                        if budget <= 0:
                            failed.add(key)
                            continue
                        request['budget_ms'] = int(budget * 1000)
                        queued_at = time.monotonic()
                        future = executor.submit(run_engine, request, queued_at + budget, cancel, queued_at)
                        future.add_done_callback(lambda _f: wake.set())   # submit as soon as the engine answers
                        jobs[key] = (future, cancel)
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
                        continue
                    # If the response is lost, the next poll observes the revision. Otherwise retry
                    # the exact move with the same expected revision; never compute twice.
                    # Human matches (negative id) also need the room's round and creation time.
                    extra = {'round_no': key[3], 'room_created_at': key[4]} if key[0] < 0 else {}
                    api.call('agent_move', match_id=key[0], leg=key[1], revision=key[2], move=move, **extra)
                    submitted.add(key)
                    del jobs[key]
                failures = 0
                active = any(m.get('status') == 'running' for m in payload.get('matches', []))
                if stream is not None and stream.healthy:
                    delay = 10   # safety poll; the stream and engine answers wake us earlier
                else:
                    delay = 1 if active or busy else 5
            except PermissionError:
                print('Bot credential rejected. Stopping.', file=sys.stderr)
                return 2
            except (RuntimeError, ValueError, KeyError, TypeError, urllib.error.URLError, OSError):
                failures += 1
                print('Connection/state error; retrying without replaying an outdated move.', file=sys.stderr)
                delay = min(30, 2 ** min(failures, 5)) + random.random()
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
        for future, cancel in jobs.values():
            cancel.set()
        executor.shutdown(wait=True, cancel_futures=True)
        for engine in engines_by_game.values():
            engine.stop()


if __name__ == '__main__':
    sys.exit(main())
