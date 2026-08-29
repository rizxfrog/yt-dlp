import json
import os
import random
import re
import time
import urllib.parse

from .common import InfoExtractor
from ..dependencies import requests
from ..utils import ExtractorError, LazyList

_TYPE_CODE_CANDIDATES = ('1004', '1001', '0', '1', '2', '19')


def _device_keys():
    def random_id():
        return ''.join(random.choices('0123456789', k=19))

    return {
        'device_id': (os.getenv('DUANJU_DEVICE_ID') or random_id()).strip(),
        'install_id': (os.getenv('DUANJU_INSTALL_ID') or random_id()).strip(),
        'platform': 'android',
    }


def _video_model_payload(video_id, type_code):
    return {
        'biz_param': {
            'detail_page_version': 0,
            'device_level': 3,
            'disable_digg_stat': False,
            'need_all_video_definition': True,
            'need_mp4_align': False,
            'use_os_player': False,
            'use_server_dns': False,
            'video_platform': 1024,
        },
        'mixed_video_id_map': {type_code: [video_id]},
    }


def _dependency_error(err):
    if isinstance(err, ImportError):
        return ExtractorError(
            'Hongguo support requires betterproto and gmssl; install yt-dlp[hongguo]',
            expected=True)
    return err


class HongguoIE(InfoExtractor):
    IE_NAME = 'hongguo'
    IE_DESC = 'Hongguo short dramas'
    _VALID_URL = r'''(?x)
        (?:
            https?://(?:www\.)?novelquickapp\.com/s/[A-Za-z0-9]+/?
            |
            https?://(?:www\.)?hongguoduanju\.com/[^\s?#]*
            |
            hongguo:(?P<id>\d{10,20})
        )
    '''
    _TESTS = [{
        'url': 'https://novelquickapp.com/s/OyZtu4aCveY/',
        'only_matching': True,
    }, {
        'url': 'https://hongguoduanju.com/detail?series_id=7671213206915779646',
        'only_matching': True,
    }, {
        'url': 'hongguo:7671213206915779646',
        'only_matching': True,
    }]

    _WEB_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120 Mobile',
    }

    def _expand_share(self, url):
        if requests is None:
            raise ExtractorError(
                'The requests package is required to expand Hongguo share URLs', expected=True)

        response = requests.get(
            url, headers=self._WEB_HEADERS, allow_redirects=False, timeout=15)
        final_url = response.headers.get('Location') or response.url
        zlink = _traverse_query(final_url, 'zlink')
        if not zlink:
            raise ExtractorError(
                f'Share URL does not contain zlink: {final_url[:120]}', expected=True)

        scheme_params = _traverse_query(urllib.parse.unquote(zlink), 'schemeParams')
        if not scheme_params:
            raise ExtractorError('zlink does not contain schemeParams', expected=True)

        share_data = self._parse_json(urllib.parse.unquote(scheme_params), None)
        video_id = share_data.get('video_id') or share_data.get('vid') or ''
        if re.fullmatch(r'\d{18,20}', video_id):
            return video_id

        report_params = share_data.get('report_params')
        if isinstance(report_params, str) and report_params:
            report_data = self._parse_json(urllib.parse.unquote(report_params), None)
            content_id = report_data.get('content_id') or ''
            if re.fullmatch(r'\d{18,20}', content_id):
                return content_id
        raise ExtractorError('Unable to extract a video ID from the share URL', expected=True)

    def _fetch_series(self, video_id):
        webpage = None
        for retry in range(3):
            webpage = self._download_webpage(
                f'https://hongguoduanju.com/detail?series_id={video_id}', video_id,
                note=f'Downloading series page (attempt {retry + 1})',
                fatal=False, headers=self._WEB_HEADERS)
            if webpage and len(webpage.encode()) > 1000:
                break
            if retry < 2:
                time.sleep(1)
        webpage = webpage or ''

        series_name = self._search_regex(
            rf'"series_id":"{video_id}".*?"series_name":"([^"]{{1,60}})"',
            webpage, 'series name', default=None)
        series_start = webpage.find(f'"series_id":"{video_id}"')
        if series_start < 0:
            return series_name, []

        next_series = re.search(r'"series_id":"\d{10,20}"', webpage[series_start + 10:])
        series_end = series_start + 10 + next_series.start() if next_series else len(webpage)
        video_list = self._search_regex(
            r'vid_list"\s*:\s*\[([^\]]*)\]', webpage[series_start:series_end],
            'episode list', default='')
        return series_name, list(dict.fromkeys(re.findall(r'"(\d{10,20})"', video_list)))

    def _real_extract(self, url):
        video_id = self._match_id(url)
        if video_id:
            return self._extract_video(video_id)

        if 'novelquickapp.com/s/' in url:
            video_id = self._expand_share(url)
        else:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            video_id = query.get('series_id', [''])[0] or query.get('video_id', [''])[0]
            if not video_id:
                video_id = self._search_regex(r'(\d{10,20})', url, 'video ID')

        series_name, episode_ids = self._fetch_series(video_id)
        if len(episode_ids) > 1:
            return self.playlist_result(
                LazyList(self._extract_video(episode_id) for episode_id in episode_ids),
                video_id, series_name or video_id)
        return self._extract_video(video_id)

    def _resolve_video(self, video_id):
        try:
            from ._hongguo.resolver import (
                USER_AGENT,
                build_video_model_url,
                extract_fallback_api,
                resolve_fallback,
                sign_json_request,
            )
        except ImportError as err:
            raise _dependency_error(err) from err

        device_keys = _device_keys()
        target_url = build_video_model_url(
            device_keys['device_id'], device_keys['install_id'])
        video_model_data = None
        for type_code in _TYPE_CODE_CANDIDATES:
            try:
                signed_url, headers, body = sign_json_request(
                    target_url, _video_model_payload(video_id, type_code), device_keys)
            except ImportError as err:
                raise _dependency_error(err) from err
            candidate = self._download_json(
                signed_url, video_id, note=f'Downloading video metadata (type {type_code})',
                headers=headers, data=body, fatal=False)
            if isinstance(candidate, dict) and video_id in (candidate.get('data') or {}):
                video_model_data = candidate
                break
        if not video_model_data:
            raise ExtractorError(
                f'Unable to resolve video {video_id}; it may be unavailable or require authentication',
                expected=True)

        try:
            fallback_url, video_model = extract_fallback_api(video_model_data, video_id)
        except (TypeError, ValueError, json.JSONDecodeError) as err:
            raise ExtractorError(f'Unable to parse metadata for {video_id}: {err}', expected=True) from err

        last_error = None
        for retry in range(3):
            fallback_data = self._download_json(
                fallback_url, video_id, note=f'Downloading video URL (attempt {retry + 1})',
                headers={'User-Agent': USER_AGENT}, fatal=False)
            if not fallback_data:
                continue
            try:
                return resolve_fallback(video_id, video_model, fallback_data)
            except (TypeError, ValueError, json.JSONDecodeError) as err:
                last_error = err
        raise ExtractorError(
            f'Unable to parse a video URL for {video_id}: {last_error or "empty response"}',
            expected=True)

    def _extract_video(self, video_id):
        try:
            info = self._resolve_video(video_id)
        except ExtractorError:
            raise
        except Exception as err:
            raise ExtractorError(f'Unable to resolve {video_id}: {err}') from err

        video_url = info.get('url')
        if not video_url:
            raise ExtractorError(
                f'Unable to extract a media URL for {video_id}', expected=True)

        content_key = info.get('content_key')
        content_key = content_key.hex() if content_key else ''
        width = int(info.get('width') or 0) or None
        height = int(info.get('height') or 0) or None
        return {
            'id': video_id,
            'title': video_id,
            'formats': [{
                'url': video_url,
                'format_id': 'cenc',
                'ext': 'mp4',
                'protocol': 'http',
                'http_headers': {
                    'User-Agent': 'com.phoenix.read/71332',
                    'Referer': 'https://novel.snssdk.com/',
                },
                'width': width,
                'height': height,
                '_hongguo_key': content_key,
            }],
            'thumbnail': info.get('pic'),
            'duration': info.get('duration'),
            'width': width,
            'height': height,
            '_hongguo_key': content_key,
        }


def _traverse_query(url, key):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(key, [''])[0]
