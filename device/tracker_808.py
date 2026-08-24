import ustruct
import usocket
import utime

# ── 防重复运行:新 exec 时通知上一个实例退出 ──────────────────────────────────
# _t808_stop 留在 REPL(__main__)全局域;两次 exec 共享同一命名空间
try:
    _t808_stop[0] = True   # 通知旧实例退出
    utime.sleep(3)         # 等旧实例退出循环(最多一个 sleep 周期)
except NameError:
    pass                   # 首次运行,变量不存在,忽略
_t808_stop = [False]       # 创建/重置给本次实例用

SERVER_HOST = '10.tcp.cpolar.top'
SERVER_PORT = 11038

REPORT_INTERVAL    = 10
HEARTBEAT_INTERVAL = 30   # 30s 心跳,确保在 wifilocator 阻塞期间服务端不踢连接
RECONNECT_DELAY    = 15

FLAG   = 0x7E
ESCAPE = 0x7D


def _escape(data):
    out = bytearray()
    for b in data:
        if b == FLAG:
            out.extend([ESCAPE, 0x02])
        elif b == ESCAPE:
            out.extend([ESCAPE, 0x01])
        else:
            out.append(b)
    return bytes(out)


def _unescape(data):
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == ESCAPE and i + 1 < len(data):
            nxt = data[i + 1]
            out.append(ESCAPE if nxt == 0x01 else FLAG)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def _xor(data):
    cs = 0
    for b in data:
        cs ^= b
    return cs


def _phone_to_bcd(phone):
    p = ('000000000000' + phone)[-12:]
    return bytes([(int(p[i*2]) << 4) | int(p[i*2+1]) for i in range(6)])


def _encode(msg_id, phone, serial, body=b''):
    hdr = (
        ustruct.pack('>H', msg_id) +
        ustruct.pack('>H', len(body) & 0x3FF) +
        _phone_to_bcd(phone) +
        ustruct.pack('>H', serial)
    )
    payload = hdr + body
    frame   = payload + bytes([_xor(payload)])
    return bytes([FLAG]) + _escape(frame) + bytes([FLAG])


def _extract_frame(buf):
    buf = bytearray(buf)
    while True:
        if 0x7E not in buf:
            return None, buf
        start = buf.index(0x7E)
        buf = buf[start + 1:]
        while buf and buf[0] == 0x7E:
            buf = buf[1:]
        if not buf:
            return None, bytearray([0x7E])
        end = -1
        i = 0
        while i < len(buf):
            if buf[i] == 0x7E:
                end = i
                break
            elif buf[i] == 0x7D and i + 1 < len(buf):
                i += 2
            else:
                i += 1
        if end < 0:
            return None, bytearray([0x7E]) + buf
        raw = _unescape(bytes(buf[:end]))
        buf = buf[end + 1:]
        if len(raw) < 13:
            continue
        if _xor(raw[:-1]) != raw[-1]:
            continue
        return raw[:-1], buf


def _parse_header(data):
    msg_id = ustruct.unpack('>H', data[0:2])[0]
    serial = ustruct.unpack('>H', data[10:12])[0]
    body   = data[12:]
    return msg_id, serial, body


def _build_register(phone, serial, imei):
    body = (
        ustruct.pack('>HH', 44, 1) +
        b'QUECT' +
        b'EC800M\x00\x00' +
        b'SIM0001' +
        bytes([1]) +
        imei.encode('ascii')
    )
    return _encode(0x0100, phone, serial, body)


def _build_auth(phone, serial, auth_code):
    code = auth_code.encode('ascii')
    return _encode(0x0102, phone, serial, bytes([len(code)]) + code)


def _build_heartbeat(phone, serial):
    return _encode(0x0002, phone, serial)


def _build_location(phone, serial, lat, lng, speed=0, direction=0, altitude=50):
    status = 0x02
    if lat < 0: status |= 0x04
    if lng < 0: status |= 0x08
    t = utime.localtime()
    bcd = bytes([
        ((t[0] % 100) // 10) << 4 | (t[0] % 10),
        (t[1] // 10) << 4 | (t[1] % 10),
        (t[2] // 10) << 4 | (t[2] % 10),
        (t[3] // 10) << 4 | (t[3] % 10),
        (t[4] // 10) << 4 | (t[4] % 10),
        (t[5] // 10) << 4 | (t[5] % 10),
    ])
    body = (
        ustruct.pack('>I', 0x00) +
        ustruct.pack('>I', status) +
        ustruct.pack('>I', int(abs(lat) * 1000000)) +
        ustruct.pack('>I', int(abs(lng) * 1000000)) +
        ustruct.pack('>H', altitude) +
        ustruct.pack('>H', speed * 10) +
        ustruct.pack('>H', direction) +
        bcd
    )
    return _encode(0x0200, phone, serial, body)


def _ensure_datacall():
    try:
        import dataCall
        info = dataCall.getInfo(1, 0)
        if info and info[2][0] == 1:
            print('[4G] connected:', info[2][2])
            return True
        print('[4G] dialing...')
        if dataCall.startCall(1, 0, 'cmnet', '', '', 0) == 0:
            utime.sleep(5)
            info = dataCall.getInfo(1, 0)
            if info and info[2][0] == 1:
                print('[4G] ok:', info[2][2])
                return True
    except Exception as e:
        print('[4G] err:', e)
    print('[4G] failed')
    return False


_gnss_mode = None   # 'gnss' | 'quecgnss' | 'wifi' | 'cell' | None
_gnss_obj  = None   # gnss.GNSS instance when mode='gnss'
_LBS_TOKEN = 'mGv2nX2JGxKFHiuy'


def _init_gnss():
    global _gnss_mode, _gnss_obj
    # 1. gnss.GNSS via UART2 (only works if AT+QGPS=1 was sent externally)
    try:
        import gnss as _gmod
        obj = _gmod.GNSS(2, 9600, 8, 0, 1, 0)
        obj.readAndParse()
        if obj.getLocationMode() != -1:
            _gnss_obj  = obj
            _gnss_mode = 'gnss'
            print('[GNSS] mode=gnss uart2')
            return
    except Exception:
        pass
    # 2. quecgnss (newer firmware)
    try:
        import quecgnss
        quecgnss.init()
        _gnss_mode = 'quecgnss'
        print('[GNSS] mode=quecgnss')
        return
    except Exception:
        pass
    # 3. wifilocator - Wi-Fi scan based, ~10-50m accuracy
    try:
        import wifilocator
        wl = wifilocator.wifilocator(_LBS_TOKEN)
        # find correct method name across firmware versions
        _wifi_fn = None
        for _n in ['getwifilocator', 'geolocation', 'getLocation', 'get_location']:
            if hasattr(wl, _n):
                _wifi_fn = _n
                break
        if _wifi_fn is None:
            print('[GNSS] wifi methods: ' + str([x for x in dir(wl) if not x.startswith('_')]))
        _gnss_mode = 'wifi'
        print('[GNSS] mode=wifilocator fn=' + str(_wifi_fn))
        return
    except Exception as e:
        print('[GNSS] wifi err: ' + str(e))
    # 4. cellLocator - cell tower, ~100-2000m accuracy
    try:
        import cellLocator
        _gnss_mode = 'cell'
        print('[GNSS] mode=cellLocator')
        return
    except Exception:
        pass
    print('[GNSS] no location module, heartbeat only')


def _read_gnss():
    if _gnss_mode == 'gnss' and _gnss_obj is not None:
        try:
            _gnss_obj.readAndParse()
            if _gnss_obj.getLocationMode() <= 0:
                return None
            loc = _gnss_obj.getLocation()
            if loc == -1 or loc is None:
                return None
            spd = _gnss_obj.getSpeed()
            alt = _gnss_obj.getAltitude()
            return (float(loc[0]), float(loc[1]),
                    int(spd) if spd != -1 else 0, 0,
                    int(alt) if alt != -1 else 50)
        except Exception as e:
            print('[GNSS] read err: ' + str(e))
        return None
    if _gnss_mode == 'quecgnss':
        try:
            import quecgnss
            loc = quecgnss.getLocation()
            if loc and loc[0] == 1:
                return float(loc[1]), float(loc[2]), int(loc[4]), 0, int(loc[3])
        except Exception:
            pass
        return None
    if _gnss_mode == 'wifi':
        # 用线程 + 20s 超时防止阻塞主循环;超时后降级到 cellLocator
        _r = [None, False]   # [result, done]

        def _wifi_task():
            try:
                import wifilocator as _wm
                _wobj = _wm.wifilocator(_LBS_TOKEN)
                for _fn in ['getwifilocator', 'geolocation', 'getLocation', 'get_location']:
                    if hasattr(_wobj, _fn):
                        _loc = getattr(_wobj, _fn)()
                        print('[GNSS] wifi fn=' + _fn + ' raw=' + str(_loc))
                        if isinstance(_loc, (tuple, list)) and len(_loc) >= 2:
                            try:
                                if float(_loc[0]) != 0:
                                    _r[0] = (float(_loc[0]), float(_loc[1]), 0, 0, 50)
                            except Exception:
                                pass
                        break
            except Exception as _e:
                print('[GNSS] wifi err: ' + str(_e))
            _r[1] = True

        try:
            import _thread
            _thread.start_new_thread(_wifi_task, ())
        except ImportError:
            _wifi_task()   # 没有 _thread,直接调用(可能阻塞)

        for _ in range(40):          # 最多等 20 秒
            if _r[1]:
                break
            utime.sleep_ms(500)

        if _r[0] is not None:
            return _r[0]

        if not _r[1]:
            print('[GNSS] wifi timeout, fall back to cell')

        # 降级:cellLocator(单次 HTTP 请求,通常 1-5s)
        try:
            import cellLocator
            _cl = cellLocator.getLocation('lbs.quectel.com', 80, _LBS_TOKEN, 8, 1)
            print('[GNSS] cell raw=' + str(_cl))
            if isinstance(_cl, (tuple, list)) and len(_cl) >= 2 and float(_cl[0]) != 0:
                return float(_cl[0]), float(_cl[1]), 0, 0, 50
        except Exception as _e:
            print('[GNSS] cell err: ' + str(_e))
        return None
    if _gnss_mode == 'cell':
        try:
            import cellLocator
            loc = cellLocator.getLocation('lbs.quectel.com', 80, _LBS_TOKEN, 8, 1)
            print('[GNSS] cell raw=' + str(loc))
            if isinstance(loc, (tuple, list)) and len(loc) >= 2 and float(loc[0]) != 0:
                acc = loc[2] if len(loc) > 2 else 0
                print('[GNSS] cell acc=' + str(acc) + 'm')
                return float(loc[0]), float(loc[1]), 0, 0, 50
        except Exception as e:
            print('[GNSS] cell err: ' + str(e))
        return None
    return None


def _recv_resp(sock, timeout_s=5):
    buf = bytearray()
    sock.settimeout(timeout_s)
    try:
        data = sock.recv(512)
        if data:
            buf.extend(data)
            frame, _ = _extract_frame(buf)
            if frame:
                return _parse_header(frame)
    except Exception:
        pass
    return None


def run():
    try:
        import modem
        imei = modem.getDevImei()
    except Exception:
        imei = '000000000000000'

    phone = imei[-12:]
    print('[Init] IMEI=' + imei + ' phone=' + phone)

    _init_gnss()

    serial = [0]

    def nxt():
        serial[0] = (serial[0] + 1) & 0xFFFF
        return serial[0]

    while not _t808_stop[0]:
        while not _t808_stop[0] and not _ensure_datacall():
            utime.sleep(10)

        print('[TCP] connecting ' + SERVER_HOST + ':' + str(SERVER_PORT))
        try:
            sock = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
            sock.connect((SERVER_HOST, SERVER_PORT))
            print('[TCP] connected!')
        except Exception as e:
            print('[TCP] failed:', e)
            utime.sleep(RECONNECT_DELAY)
            continue

        auth_code = 'DEFAULT'

        try:
            print('[808] register...')
            sock.sendall(_build_register(phone, nxt(), imei))
            resp = _recv_resp(sock, 12)   # 超时从 8s 加到 12s(cpolar 有延迟)
            if resp and resp[0] == 0x8100:
                body   = resp[2]
                result = body[2] if len(body) > 2 else 1
                if result == 0 and len(body) > 3:
                    auth_code = body[3:].decode('ascii', 'replace').rstrip('\x00').strip()
                print('[808] reg result=' + str(result) + ' auth=' + auth_code)
            else:
                print('[808] no reg resp, use default auth')

            utime.sleep_ms(500)

            print('[808] auth: ' + auth_code)
            sock.sendall(_build_auth(phone, nxt(), auth_code))
            resp = _recv_resp(sock, 8)
            if resp:
                body      = resp[2]
                auth_res  = body[4] if len(body) >= 5 else 0
                print('[808] auth result=' + str(auth_res))
                if auth_res != 0:
                    print('[808] auth failed, reconnect...')
                    raise Exception('auth_failed')

            utime.sleep_ms(300)

            # 认证成功后立即发一次心跳,避免 wifilocator 阻塞期间服务端超时踢连接
            sock.sendall(_build_heartbeat(phone, nxt()))
            print('[808] heartbeat (init)')
            last_hb = utime.ticks_ms()

            print('[808] loop start')
            while not _t808_stop[0]:
                now = utime.ticks_ms()

                if utime.ticks_diff(now, last_hb) >= HEARTBEAT_INTERVAL * 1000:
                    sock.sendall(_build_heartbeat(phone, nxt()))
                    print('[808] heartbeat')
                    last_hb = utime.ticks_ms()

                loc = _read_gnss()   # wifi 模式下最多等 20s 后降级到 cell
                if loc:
                    lat, lng, spd, dire, alt = loc
                    sock.sendall(_build_location(phone, nxt(), lat, lng, spd, dire, alt))
                    print('[808] loc lat=' + str(lat) + ' lng=' + str(lng))
                else:
                    sock.sendall(_build_heartbeat(phone, nxt()))
                    print('[808] no gnss, heartbeat')
                    last_hb = utime.ticks_ms()   # 发了心跳,重置计时

                _recv_resp(sock, 1)
                utime.sleep(REPORT_INTERVAL)

        except Exception as e:
            print('[loop] disconnected:', e)
        finally:
            try:
                sock.close()
            except Exception:
                pass

        print('[reconnect] wait ' + str(RECONNECT_DELAY) + 's')
        utime.sleep(RECONNECT_DELAY)


run()
