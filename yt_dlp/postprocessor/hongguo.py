import os
import subprocess

from .common import PostProcessor
from ..utils import PostProcessingError


class HongguoDecryptPP(PostProcessor):
    def run(self, info):
        content_key = info.get('_hongguo_key') or next((
            fmt.get('_hongguo_key') for fmt in info.get('formats') or []
            if fmt.get('_hongguo_key')), '')
        if not content_key:
            return [], info

        filepath = info.get('filepath')
        if not filepath or not os.path.exists(filepath):
            self.report_warning(f'Unable to decrypt missing file: {filepath}')
            return [], info
        if self._is_playable(filepath):
            self.to_screen(f'[hongguo] Already decrypted: {os.path.basename(filepath)}')
            info['_hongguo_key'] = ''
            return [], info

        ffmpeg = self._find_ffmpeg()
        if not ffmpeg:
            raise PostProcessingError('ffmpeg is required to decrypt Hongguo videos')

        temporary_filename = f'{filepath}.decrypting.mp4'
        self.to_screen(f'[hongguo] Decrypting: {os.path.basename(filepath)}')
        try:
            subprocess.run(
                [ffmpeg, '-y', '-decryption_key', content_key, '-i', filepath,
                 '-c', 'copy', '-movflags', '+faststart', temporary_filename],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as err:
            if os.path.exists(temporary_filename):
                os.remove(temporary_filename)
            raise PostProcessingError(f'ffmpeg failed to decrypt the video: {err}') from err

        os.replace(temporary_filename, filepath)
        info['_hongguo_key'] = ''
        return [], info

    @classmethod
    def _is_playable(cls, filepath):
        ffmpeg = cls._find_ffmpeg()
        if not ffmpeg:
            return False
        try:
            result = subprocess.run(
                [ffmpeg, '-v', 'error', '-i', filepath, '-frames:v', '1', '-f', 'null', '-'],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)
        except (subprocess.SubprocessError, OSError):
            return False
        return result.returncode == 0 and not result.stderr.strip()

    @staticmethod
    def _find_ffmpeg():
        from shutil import which
        return which('ffmpeg') or ''
