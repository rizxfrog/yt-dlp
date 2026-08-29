import hashlib
import unittest

from yt_dlp.extractor._hongguo.branch_one import branch_one_table
from yt_dlp.extractor._hongguo.resolver import (
    build_video_model_url,
    resolve_fallback,
    sign_json_request,
)


class TestHongguo(unittest.TestCase):
    def test_branch_table(self):
        self.assertEqual(len(branch_one_table), 81936)
        self.assertEqual(
            hashlib.sha256(branch_one_table).hexdigest(),
            '239ddf955d14a008311b8c3adcf05713b858954068fff02ab19c2ba81e31d1d3')

    def test_sign_request(self):
        device_keys = {'device_id': '1' * 19, 'install_id': '2' * 19}
        signed_url, headers, body = sign_json_request(
            build_video_model_url(device_keys['device_id'], device_keys['install_id']), {
                'biz_param': {'video_platform': 1024},
                'mixed_video_id_map': {'1004': ['7671213206915779646']},
            }, device_keys)
        self.assertTrue(signed_url.startswith(
            'https://api5-normal-sinfonlineb.fqnovel.com/'))
        self.assertTrue(body.startswith(b'{'))
        for header in ('x-gorgon', 'x-helios', 'x-medusa'):
            self.assertTrue(headers[header])

    def test_resolve_unencrypted_fallback(self):
        result = resolve_fallback('123', {'duration': 42}, {
            'video_info': {'data': {'video_list': {
                'low': {
                    'main_url': 'https://example.com/low.mp4',
                    'vheight': 720,
                    'vwidth': 1280,
                    'bitrate': 1000,
                },
                'high': {
                    'main_url': 'https://example.com/high.mp4',
                    'vheight': 1080,
                    'vwidth': 1920,
                    'bitrate': 2000,
                },
            }}},
        })
        self.assertEqual(result['url'], 'https://example.com/high.mp4')
        self.assertEqual(result['duration'], 42)
        self.assertEqual((result['width'], result['height']), (1920, 1080))
        self.assertIsNone(result['content_key'])


if __name__ == '__main__':
    unittest.main()
