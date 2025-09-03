import logging
from typing import Dict, List, Any
from app.config import WHISPER_BACKEND, WHISPER_MODEL, WHISPER_LANGUAGE

logger = logging.getLogger(__name__)

class AudioProcessor:
    """
    Унифицированный интерфейс распознавания:
    - WHISPER_BACKEND = "faster" (по умолчанию) -> faster-whisper
    - WHISPER_BACKEND = "openai"                -> openai-whisper

    WHISPER_LANGUAGE: "ru" / "auto" / любой ISO (en, de, ...)

    Методы:
      • transcribe_audio(path) — синхронное API (старый стиль)
      • async transcribe(path) — новое API, которое ждёт TaskManager
    """

    def __init__(self):
        self.backend = (WHISPER_BACKEND or "faster").lower()
        self.model_name = WHISPER_MODEL or "small"
        self.language = None if (WHISPER_LANGUAGE or "ru") == "auto" else WHISPER_LANGUAGE
        self._model = None

    def _load_openai_whisper(self):
        try:
            import whisper
        except Exception as e:
            raise RuntimeError(
                "WHISPER_BACKEND=openai, но пакет 'openai-whisper' не установлен.\n"
                "Добавьте `openai-whisper==20231117` в requirements.txt или переключитесь на WHISPER_BACKEND=faster."
            ) from e
        logger.info(f"[whisper(openai)] загрузка модели: {self.model_name}")
        return whisper.load_model(self.model_name)

    def _load_faster_whisper(self):
        from faster_whisper import WhisperModel
        logger.info(f"[whisper(faster)] загрузка модели: {self.model_name} (cpu, int8)")
        return WhisperModel(self.model_name, device="cpu", compute_type="int8")

    def load_model(self):
        if self._model is not None:
            return
        self._model = self._load_openai_whisper() if self.backend == "openai" else self._load_faster_whisper()

    def transcribe_audio(self, audio_path: str) -> Dict[str, Any]:
        """Старый стиль: синхронный вызов"""
        self.load_model()
        lang = self.language  # None -> авто

        if self.backend == "openai":
            result = self._model.transcribe(audio_path, language=lang, verbose=False)
            segments_out: List[Dict[str, Any]] = []
            for seg in result.get("segments") or []:
                segments_out.append({
                    "id": int(getattr(seg, "id", seg.get("id", 0))),
                    "start": float(getattr(seg, "start", seg.get("start", 0.0))),
                    "end": float(getattr(seg, "end", seg.get("end", 0.0))),
                    "text": str(getattr(seg, "text", seg.get("text", ""))).strip(),
                })
            return {
                "text": (result.get("text") or "").strip(),
                "segments": segments_out,
                "language": result.get("language"),
                "duration": float(result.get("duration", 0.0)),
                "title": audio_path.split("/")[-1],
            }
        else:
            segments_iter, info = self._model.transcribe(audio_path, language=lang)
            text_parts: List[str] = []
            segments_out: List[Dict[str, Any]] = []
            last_end = 0.0
            for seg in segments_iter:
                t = (seg.text or "").strip()
                text_parts.append(t)
                segments_out.append({
                    "id": int(seg.id),
                    "start": float(seg.start or 0.0),
                    "end": float(seg.end or 0.0),
                    "text": t,
                })
                last_end = float(seg.end or last_end)
            return {
                "text": "".join(text_parts).strip(),
                "segments": segments_out,
                "language": getattr(info, "language", None),
                "duration": last_end,
                "title": audio_path.split("/")[-1],
            }

    async def transcribe(self, audio_path: str, language: str | None = None) -> Dict[str, Any]:
        """
        Новое API: асинхронная обёртка.
        TaskManager ждёт именно этот метод.
        """
        return await asyncio.to_thread(self.transcribe_audio, audio_path)

    def format_transcription(self, result: Dict[str, Any], with_timestamps: bool = False) -> str:
        if not result or "text" not in result:
            return "Не удалось распознать текст."
        if not with_timestamps:
            return (result.get("text") or "").strip()
        out_lines: List[str] = []
        for seg in result.get("segments") or []:
            out_lines.append(f"[{seg.get('start', 0):.0f}s-{seg.get('end', 0):.0f}s] {seg.get('text','').strip()}")
        return "\n".join(out_lines).strip()


audio_processor = AudioProcessor()
