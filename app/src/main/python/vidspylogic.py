import os
import yt_dlp
import tempfile
from pathlib import Path

def download_video(url: str) -> str:
    try:
        temp_dir = tempfile.mkdtemp()

        ydl_opts = {
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'format': 'best[height<=720]',
            'noplaylist': True
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'video')

            ydl.download([url])

            temp_files = os.listdir(temp_dir)
            video_file = None

            for file in temp_files:
                if file.endswith(('.mp4', '.webm', '.mkv', '.avi', '.mov')):
                    video_file = os.path.join(temp_dir, file)
                    break

            if video_file and os.path.exists(video_file):
                return video_file
            else:
                raise Exception("Video file not found after download")
    except Exception as e:
        with open('/data/local/tmp/aaaaa.txt', 'w') as f:
            f.write(str(e))
        raise Exception(f"Failed to download video: {str(e)}")
