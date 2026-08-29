import base64
import binascii
import contextlib
import hashlib
import json
import time
import urllib.parse

from ...aes import aes_cbc_decrypt_bytes

USER_AGENT = (
    'com.phoenix.read/71332 (Linux; U; Android 16; zh_CN; 25053RT47C; '
    'Build/BP2A.250605.031.A3; Cronet/TTNetVersion:04657795 2026-01-23 '
    'QuicVersion:c67e9834 2025-09-08)')

VIDEO_MODEL_URL = 'https://api5-normal-sinfonlineb.fqnovel.com/novel/player/multi_video_model/v1/'
VIDEO_MODEL_QUERY = {
    'ac': 'wifi',
    'channel': 'update_64',
    'aid': '8662',
    'app_name': 'novelread',
    'version_code': '71332',
    'version_name': '7.1.3.32',
    'device_platform': 'android',
    'os': 'android',
    'ssmix': 'a',
    'device_type': '25053RT47C',
    'device_brand': 'Redmi',
    'language': 'zh',
    'os_api': '36',
    'os_version': '16',
    'manifest_version_code': '71332',
    'resolution': '1280*2772',
    'dpi': '520',
    'update_version_code': '71332',
    'host_abi': 'arm64-v8a',
    'dragon_device_type': 'phone',
    'pv_player': '71332',
    'compliance_status': '0',
    'need_personal_recommend': '1',
    'player_so_load': '1',
    'is_android_pad_screen': '0',
}


def build_video_model_url(device_id, install_id):
    query = urllib.parse.urlencode({
        **VIDEO_MODEL_QUERY,
        'iid': install_id,
        'device_id': device_id,
    })
    return f'{VIDEO_MODEL_URL}?{query}'


def sign_json_request(url, payload, device_keys):
    from .core import core_sixgod

    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    timestamp = str(int(time.time() * 1000))
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'application/json; charset=utf-8,application/x-protobuf',
        'Content-Type': 'application/json; charset=UTF-8',
        'x-xs-from-web': '0',
        'x-ss-req-ticket': timestamp,
        'x-tt-request-tag': 't=0;n=0',
        'sdk-version': '2',
        'passport-sdk-version': '50561',
        'x-vc-bdturing-sdk-version': '3.7.2.cn',
    }
    parsed_url = urllib.parse.urlsplit(url)
    signed_headers, signed_url = core_sixgod(
        surl=f'{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}',
        params=dict(urllib.parse.parse_qsl(parsed_url.query, keep_blank_values=True)),
        data=json.loads(body),
        devices={
            'device_id': device_keys['device_id'],
            'iid': device_keys['install_id'],
            'install_id': device_keys['install_id'],
            'device_brand': 'Redmi',
            'device_model': '25053RT47C',
            'device_type': '25053RT47C',
            'device_manufacturer': 'Xiaomi',
            'os_version': '16',
            'version_name': '7.1.3.32',
            'ua': USER_AGENT,
        },
        header=headers,
        log=False)
    return signed_url, signed_headers, body


def extract_fallback_api(data, video_id):
    data_map = data.get('data')
    if not isinstance(data_map, dict):
        raise ValueError('Response does not contain video data')
    video_entry = data_map.get(video_id)
    if not isinstance(video_entry, dict):
        video_entry = next((
            item[0] if isinstance(item, list) and item and isinstance(item[0], dict) else item
            for item in data_map.values()
            if isinstance(item, dict) or (isinstance(item, list) and item)), None)
    if not video_entry:
        raise ValueError(f'Unable to find video {video_id} in the response')

    video_model = video_entry.get('video_model')
    if isinstance(video_model, str):
        video_model = json.loads(video_model)
    if not isinstance(video_model, dict):
        raise ValueError('Response does not contain a video model')

    fallback_api = video_model.get('fallback_api')
    if isinstance(fallback_api, str) and fallback_api.startswith('{'):
        fallback_api = json.loads(fallback_api).get('fallback_api')
    elif isinstance(fallback_api, list):
        fallback_api = fallback_api[0] if fallback_api else None
    elif isinstance(fallback_api, dict):
        fallback_api = fallback_api.get('fallback_api')
    if not isinstance(fallback_api, str) or len(fallback_api) < 10:
        raise ValueError('Unable to parse the fallback API URL')
    return fallback_api, video_model


def resolve_fallback(video_id, video_model, fallback_data):
    video_info = fallback_data.get('video_info')
    video_data = video_info.get('data') if isinstance(video_info, dict) else None
    if not isinstance(video_data, dict):
        raise ValueError('Fallback response does not contain video data')

    video_list = video_data.get('video_list')
    if not isinstance(video_list, dict):
        raise ValueError('Fallback response does not contain any formats')
    best_item = max(
        (item for item in video_list.values() if isinstance(item, dict)),
        key=lambda item: (_int(item.get('vheight')), _int(item.get('bitrate'))),
        default=None)
    if not best_item:
        raise ValueError('Unable to select a video format')

    video_url = _first(best_item, 'main_url', 'play_addr', 'backup_url_1', 'url')
    key_seed = _b64decode(video_data.get('key_seed') or '')
    if key_seed and video_url:
        with contextlib.suppress(Exception):
            video_url = decrypt_spade_url(video_url, key_seed)
    if video_url.startswith('//'):
        video_url = f'https:{video_url}'

    content_key = None
    if best_item.get('spade_a'):
        with contextlib.suppress(Exception):
            content_key = derive_content_key(best_item['spade_a'])
    return {
        'vid': video_id,
        'url': video_url,
        'content_key': content_key,
        'pic': _first(
            best_item, 'cover', 'poster')
            or _first(video_model, 'origin_cover', 'cover_url', 'dynamic_cover', 'cover')
            or _first(video_data, 'cover', 'poster'),
        'duration': _int(video_model.get('duration') or video_data.get('duration')) or None,
        'height': _int(best_item.get('vheight') or best_item.get('height')) or None,
        'width': _int(best_item.get('vwidth') or best_item.get('width')) or None,
    }


def derive_content_key(value):
    raw = _b64decode(value)
    if len(raw) < 3:
        raise ValueError('spade_a is too short')
    length = len(raw) - (raw[0] ^ raw[1] ^ raw[2]) + 47
    if length <= 0 or length > len(raw) * 2:
        raise ValueError('spade_a has an invalid content-key offset')
    if 1 + length > len(raw):
        length = len(raw) - 1
    if length < 33:
        raise ValueError('spade_a does not contain a content key')

    data = bytearray(raw[1:1 + length])
    previous_a, previous_b = 85, 246
    for index in range(length):
        if index & 1:
            previous, previous_a = previous_a, data[index]
        else:
            previous, previous_b = previous_b, data[index]
        data[index] = (-21 - index.bit_count() + (previous ^ data[index])) & 0xff
    return binascii.unhexlify(bytes(data[1:33]).decode('ascii'))


def decrypt_spade_url(value, key_seed):
    raw = _b64decode(value)
    if len(raw) < 5 or raw[0] != 0xa8 or raw[2:4] != b'\x01\x00':
        raise ValueError('Invalid encrypted URL header')
    ciphertext = raw[4:len(raw) - (len(raw) - 4) % 16]
    constants = bytes.fromhex('4dd4c2e6b83162090e52b3c7a6733ba41cb2462b829ab58a196b39db57177524f49baf7f08e8d68d26a72e37c1a95a2f1f05a51892aef2949732b62a38aadd58')
    digest = hashlib.sha512(hashlib.sha512(key_seed).digest() + constants).digest()
    plaintext = aes_cbc_decrypt_bytes(ciphertext, digest[:16], digest[16:32])
    if plaintext and 1 <= plaintext[-1] <= 16:
        plaintext = plaintext[:-plaintext[-1]]
    return plaintext.rstrip(b'\0').decode('utf-8', 'replace')


def _b64decode(value):
    if not value:
        return b''
    value += '=' * (-len(value) % 4)
    try:
        return base64.b64decode(value)
    except Exception:
        return base64.urlsafe_b64decode(value)


def _first(data, *keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            value = next(filter(None, value), '')
        if isinstance(value, dict):
            value = _first(value, 'url', 'uri', 'src', 'download_url', 'url_list', 'urls')
        if value not in (None, ''):
            return str(value).strip()
    return ''


def _int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0
