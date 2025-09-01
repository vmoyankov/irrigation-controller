# Based on https://github.com/wybiral/micropython-aioweb

import uasyncio as asyncio
from hashlib import sha1
from binascii import b2a_base64
import struct
import time

def unquote_plus(s):
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        i += 1
        if c == '+':
            out.append(' ')
        elif c == '%':
            out.append(chr(int(s[i:i + 2], 16)))
            i += 2
        else:
            out.append(c)
    return ''.join(out)

def parse_qs(s):
    out = {}
    for x in s.split('&'):
        kv = x.split('=', 1)
        key = unquote_plus(kv[0])
        kv[0] = key
        if len(kv) == 1:
            val = True
            kv.append(val)
        else:
            val = unquote_plus(kv[1])
            kv[1] = val
        tmp = out.get(key, None)
        if tmp is None:
            out[key] = val
        else:
            if isinstance(tmp, list):
                tmp.append(val)
            else:
                out[key] = [tmp, val]
    return out

def get_mime_type(filename):
    ext = filename.lower().split('.')[-1] if '.' in filename else ''
    mime_types = {
        'html': 'text/html', 'htm': 'text/html', 'css': 'text/css',
        'js': 'application/javascript', 'json': 'application/json',
        'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'gif': 'image/gif', 'ico': 'image/x-icon', 'svg': 'image/svg+xml',
        'txt': 'text/plain', 'pdf': 'application/pdf', 'xml': 'application/xml',
        'zip': 'application/zip', 'woff': 'font/woff', 'woff2': 'font/woff2',
        'ttf': 'font/ttf', 'eot': 'application/vnd.ms-fontobject'
    }
    return mime_types.get(ext, 'application/octet-stream')

def path_matches_pattern(path, pattern):
    if pattern == '/':
        return path == '/'
    if pattern.endswith('/'):
        return path.startswith(pattern[:-1])
    return path == pattern


async def _parse_request(r, w):
    line = await r.readline()
    if not line:
        raise ValueError
    parts = line.decode().split()
    if len(parts) < 3:
        raise ValueError
    r.method = parts[0]
    r.path = parts[1]
    parts = r.path.split('?', 1)
    if len(parts) < 2:
        r.query = None
    else:
        r.path = parts[0]
        r.query = parts[1]
    r.headers = await _parse_headers(r)

async def _parse_headers(r):
    headers = {}
    while True:
        line = await r.readline()
        if not line:
            break
        line = line.decode()
        if line == '\r\n':
            break
        key, value = line.split(':', 1)
        headers[key.lower()] = value.strip()
    return headers


class App:

    def __init__(self, host='0.0.0.0', port=80):
        self.host = host
        self.port = port
        self.handlers = []
        self.buffer = bytearray(1024)

    def route(self, path, methods=['GET']):
        def wrapper(handler):
            self.handlers.append((path, methods, handler))
            return handler
        return wrapper

    def static(self, url_path, directory):
        def static_handler(request, writer):
            return self._serve_static_file(request, writer, url_path, directory)
        self.handlers.append((url_path, ['GET'], static_handler))

    async def _serve_static_file(self, request, writer, url_path, directory):
        try:
            file_path = request.path[len(url_path):]
            
            while file_path.startswith('/'):
                file_path = file_path[1:]
            
            full_path = directory + '/' + file_path if directory else file_path
            if full_path.endswith('/'):
                full_path = full_path[:-1]
            if '..' in file_path or file_path.startswith('/'):
                await writer.awrite(b'HTTP/1.0 403 Forbidden\r\n\r\nForbidden')
                return
            
            try:
                with open(full_path, 'rb') as f:
                    mime_type = get_mime_type(full_path)
                    cache_headers = 'Cache-Control: public, max-age=31536000\r\n'
                    await writer.awrite(f'HTTP/1.0 200 OK\r\nContent-Type: {mime_type}\r\n{cache_headers}\r\n'.encode())
                    
                    while True:
                        n = f.readinto(self.buffer)
                        if n == 0:
                            break
                        await writer.awrite(self.buffer[:n])
                        
            except OSError:
                await writer.awrite(b'HTTP/1.0 404 Not Found\r\n\r\nFile Not Found')
                return
            
        except:
            await writer.awrite(b'HTTP/1.0 500 Internal Server Error\r\n\r\nInternal Server Error')

    async def _dispatch(self, r, w):
        try:
            await _parse_request(r, w)
            for path, methods, handler in self.handlers:
                if path_matches_pattern(r.path, path) and r.method in methods:
                    await handler(r, w)
                    return
            await w.awrite(b'HTTP/1.0 404 Not Found\r\n\r\nNot Found')
        except Exception as e:
            print(e)
        finally:
            await w.wait_closed()

    async def serve(self):
        await asyncio.start_server(self._dispatch, self.host, self.port)


class WebSocket:

    HANDSHAKE_KEY = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
    
    OP_TYPES = {
        0x0: 'cont',
        0x1: 'text',
        0x2: 'bytes',
        0x8: 'close',
        0x9: 'ping',
        0xa: 'pong',
    }

    @classmethod
    async def upgrade(cls, r, w):
        key = r.headers['sec-websocket-key'].encode()
        key += WebSocket.HANDSHAKE_KEY
        x = b2a_base64(sha1(key).digest()).strip()
        w.write(b'HTTP/1.1 101 Switching Protocols\r\n')
        w.write(b'Upgrade: websocket\r\n')
        w.write(b'Connection: Upgrade\r\n')
        w.write(b'Sec-WebSocket-Accept: ' + x + b'\r\n')
        w.write(b'\r\n')
        await w.drain()
        return cls(r, w)

    def __init__(self, r, w):
        self.r = r
        self.w = w

    async def recv(self):
        r = self.r
        x = await r.read(2)
        if not x or len(x) < 2:
            return None
        out = {}
        op, n = struct.unpack('!BB', x)
        out['fin'] = bool(op & (1 << 7))
        op = op & 0x0f
        if op not in WebSocket.OP_TYPES:
            raise None
        out['type'] = WebSocket.OP_TYPES[op]
        masked = bool(n & (1 << 7))
        n = n & 0x7f
        if n == 126:
            n, = struct.unpack('!H', await r.read(2))
        elif n == 127:
            n, = struct.unpack('!Q', await r.read(8))
        if masked:
            mask = await r.read(4)
        data = await r.read(n)
        if masked:
            data = bytearray(data)
            for i in range(len(data)):
                data[i] ^= mask[i % 4]
            data = bytes(data)
        if out['type'] == 'text':
            data = data.decode()
        out['data'] = data
        return out

    async def send(self, msg):
        if isinstance(msg, str):
            await self._send_op(0x1, msg.encode())
        elif isinstance(msg, bytes):
            await self._send_op(0x2, msg)

    async def _send_op(self, opcode, payload):
        w = self.w
        w.write(bytes([0x80 | opcode]))
        n = len(payload)
        if n < 126:
            w.write(bytes([n]))
        elif n < 65536:
            w.write(struct.pack('!BH', 126, n))
        else:
            w.write(struct.pack('!BQ', 127, n))
        w.write(payload)
        await w.drain()


class EventSource:

    @classmethod
    async def upgrade(cls, r, w):
        w.write(b'HTTP/1.0 200 OK\r\n')
        w.write(b'Content-Type: text/event-stream\r\n')
        w.write(b'Cache-Control: no-cache\r\n')
        w.write(b'Connection: keep-alive\r\n')
        w.write(b'Access-Control-Allow-Origin: *\r\n')
        w.write(b'\r\n')
        await w.drain()
        return cls(r, w)

    def __init__(self, r, w):
        self.r = r
        self.w = w

    async def send(self, msg, id=None, event=None):
        w = self.w
        if id is not None:
            w.write(b'id: {}\r\n'.format(id))
        if event is not None:
            w.write(b'event: {}\r\n'.format(event))
        w.write(b'data: {}\r\n'.format(msg))
        w.write(b'\r\n')
        await w.drain()
