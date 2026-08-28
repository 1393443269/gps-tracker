"""
智令(ZhiLing)设备指令协议模块
==============================
指令格式: *CMD,param1,param2,...#
串口发送: AT%PARA=*CMD,...#
所有命令严格区分大小写，分隔符为半角逗号

本模块提供：
1. 指令构造（平台→设备）
2. 指令解析（设备→平台的响应）
"""


def _build_cmd(cmd: str, *params) -> str:
    """构造智令指令字符串: *CMD,p1,p2,...#"""
    _ILLEGAL = (',', '#', '*')
    for p in params:
        if isinstance(p, str) and any(c in p for c in _ILLEGAL):
            raise ValueError(f"指令参数含非法字符: {p!r}")
    parts = [str(p) for p in params if p is not None]
    return '*' + cmd + ',' + ','.join(parts) + '#'


def build_set_ip(ip: str, port: int, proto: int = 0, channel: int = 1) -> str:
    """设置服务器IP。channel=1用IP, channel=2用IP2。proto: 0=TCP, 1=UDP"""
    cmd = 'IP' if channel == 1 else 'IP2'
    return _build_cmd(cmd, ip, port, proto)


def build_close_ip(channel: int = 1) -> str:
    """关闭IP通道"""
    return _build_cmd('IPOFF', channel)


def build_query_status() -> str:
    """查询设备状态"""
    return '*QS#'


def build_set_interval(move_sec: int, static_sec: int, heartbeat_sec: int) -> str:
    """设置上传频率(秒)。最小3秒"""
    return _build_cmd('INTERVAL', move_sec, static_sec, heartbeat_sec)


def build_reset(delay_sec: int = 3) -> str:
    """远程复位"""
    return _build_cmd('RESET', delay_sec)


def build_upload_now() -> str:
    """立即上传一条定位"""
    return '*UPLOAD#'


def build_set_volume(level: int) -> str:
    """设置音量(0-100)"""
    return _build_cmd('SOUND_LEVEL', level)


def build_set_card_info(company: str, name: str, emp_id: str, title: str) -> str:
    """设置工卡信息(墨水瓶工牌)"""
    return _build_cmd('CardInfo', company, name, emp_id, title)


def build_send_message(message: str) -> str:
    """发送留言(最长140字符/70中文，不支持标点)"""
    return _build_cmd('Message', message)


def build_set_gps_repair(angle: int, speed: int) -> str:
    """设置拐弯补偿参数"""
    return _build_cmd('GpsRepair', angle, speed)


def build_ota_http(url: str, file_size: int, md5: str) -> str:
    """OTA升级(HTTP)"""
    return _build_cmd('OtaHttp', url, file_size, md5)


def build_open_ota(url: str, file_size: int) -> str:
    """OPEN版本OTA升级"""
    return _build_cmd('OpenOta', url, file_size)


def build_set_cmcc_rtk(enable: int, server: str, port: int, freq: int) -> str:
    """中移SDK RTK IP设置"""
    return _build_cmd('CMCCRTK_IP', enable, server, port, freq)


def build_set_cmcc_rtk_account(user: str, pwd: str, rid: str, mountpoint: str, authtype: str) -> str:
    """中移SDK RTK用户设置"""
    return _build_cmd('CMCCRTK_ACCOUNT', user, pwd, rid, mountpoint, authtype)


def build_alarm_power_on_off(start_time: str, end_time: str) -> str:
    """定时开关机。时间格式HHMM，如0830,1200。取消: 0000,0000"""
    return _build_cmd('AlarmPowerOnOff', start_time, end_time)


def build_flash_led(on: bool) -> str:
    """爆闪灯开关"""
    return _build_cmd('FLASH_LED', 1 if on else 0)


def build_red_blue_style(style: int) -> str:
    """红蓝灯样式(0-5, 0=关闭)"""
    return _build_cmd('RedBlueSet', style)


def build_locate_mode(gps: bool, lbs: bool, wifi: bool) -> str:
    """定位模式开关。三位: GPS/LBS/WIFI, 1=开 0=关"""
    mode = f"{1 if gps else 0}{1 if lbs else 0}{1 if wifi else 0}"
    return _build_cmd('LocateMode', mode)


def build_set_mileage(meters: int) -> str:
    """重置里程(米)"""
    return _build_cmd('MILEAGE', meters)


def build_set_mileage_factor(factor: int) -> str:
    """设置里程计算因子(如102=1.02倍)。0=关闭"""
    return _build_cmd('MILEAGE_FACTOR', factor)


def build_set_family_numbers(num1: str = '', num2: str = '', num3: str = '') -> str:
    """设置亲情号码"""
    return _build_cmd('FamilyNumber', num1, num2, num3)


def build_set_sos_numbers(*numbers) -> str:
    """设置SOS求救电话(最多5个)"""
    padded = list(numbers[:5]) + [''] * (5 - min(len(numbers), 5))
    return _build_cmd('SosNumber', *padded)


def build_set_sos_msg(msg: str) -> str:
    """设置SOS短信内容(<128字符,不能有逗号)"""
    return _build_cmd('SosMsg', msg)


def build_set_ntrip(ip: str, port: int, mountpoint: str, user: str, pwd: str, freq: int) -> str:
    """设置NTRIP RTK账户"""
    return _build_cmd('NTRIP', ip, port, mountpoint, user, pwd, freq)


def build_close_rtk() -> str:
    """关闭RTK"""
    return _build_cmd('RTKTYPE', 0)


def build_set_apn(apn_type: int, name: str, user: str = '', pwd: str = '') -> str:
    """设置APN"""
    return _build_cmd('APN', apn_type, name, user, pwd)


# ── 指令编码为字节（用于 TCP 下发）──────────────────────────────────────────────

def encode_cmd(cmd_str: str) -> bytes:
    """将指令字符串编码为 UTF-8 字节"""
    return cmd_str.encode('utf-8')


# ── 可用指令清单（供 API 校验）─────────────────────────────────────────────────

AVAILABLE_COMMANDS = {
    'set_ip':           {'func': build_set_ip,           'params': ['ip', 'port'], 'desc': '设置服务器IP'},
    'close_ip':         {'func': build_close_ip,         'params': [],             'desc': '关闭IP通道'},
    'query_status':     {'func': build_query_status,     'params': [],             'desc': '查询设备状态'},
    'set_interval':     {'func': build_set_interval,     'params': ['move_sec', 'static_sec', 'heartbeat_sec'], 'desc': '设置上传频率'},
    'reset':            {'func': build_reset,            'params': [],             'desc': '远程复位'},
    'upload':           {'func': build_upload_now,       'params': [],             'desc': '立即上传定位'},
    'set_volume':       {'func': build_set_volume,       'params': ['level'],      'desc': '设置音量'},
    'set_card_info':    {'func': build_set_card_info,    'params': ['company', 'name', 'emp_id', 'title'], 'desc': '设置工卡信息'},
    'send_message':     {'func': build_send_message,     'params': ['message'],    'desc': '发送留言'},
    'set_gps_repair':   {'func': build_set_gps_repair,   'params': ['angle', 'speed'], 'desc': '拐弯补偿参数'},
    'ota_http':         {'func': build_ota_http,         'params': ['url', 'file_size', 'md5'], 'desc': 'OTA升级'},
    'open_ota':         {'func': build_open_ota,         'params': ['url', 'file_size'], 'desc': 'OPEN版OTA'},
    'alarm_power':      {'func': build_alarm_power_on_off, 'params': ['start_time', 'end_time'], 'desc': '定时开关机'},
    'flash_led':        {'func': build_flash_led,        'params': ['on'],         'desc': '爆闪灯开关'},
    'locate_mode':      {'func': build_locate_mode,      'params': ['gps', 'lbs', 'wifi'], 'desc': '定位模式'},
    'set_mileage':      {'func': build_set_mileage,      'params': ['meters'],     'desc': '重置里程'},
    'set_family':       {'func': build_set_family_numbers, 'params': ['num1', 'num2', 'num3'], 'desc': '亲情号码'},
    'set_sos_numbers':  {'func': build_set_sos_numbers,  'params': ['numbers'],    'desc': 'SOS电话'},
    'set_sos_msg':      {'func': build_set_sos_msg,      'params': ['msg'],        'desc': 'SOS短信'},
    'set_ntrip':        {'func': build_set_ntrip,        'params': ['ip', 'port', 'mountpoint', 'user', 'pwd', 'freq'], 'desc': 'NTRIP RTK'},
    'close_rtk':        {'func': build_close_rtk,        'params': [],             'desc': '关闭RTK'},
    'set_apn':          {'func': build_set_apn,          'params': ['apn_type', 'name'], 'desc': '设置APN'},
}
