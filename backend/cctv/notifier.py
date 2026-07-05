from pathlib import Path

import httpx


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id

    async def send_video(self, file_path: Path, caption: str) -> None:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendVideo"
        with file_path.open("rb") as video_file:
            files = {
                "video": (file_path.name, video_file, "video/mp4"),
            }
            data = {
                "chat_id": self._chat_id,
                "caption": caption,
            }
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, data=data, files=files)
        if response.status_code >= 400:
            raise RuntimeError(f"Telegram send failed: {response.status_code} {response.text}")

