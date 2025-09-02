import logging
from app.config import WHISPER_BACKEND, WHISPER_MODEL

logger = logging.getLogger(__name__)

class AudioProcessor:
    """
    Унифицированный интерфейс распознавания:
    - WHISPER_BACKEND = "openai"  -> openai-whisper
    - WHISPER_BACKEND = "faster"  -> faster-whisper
    """
    def __init__(self):
        self.backend = WHISPER_BACKEND
        self.model_name = WHISPER_MODEL
        self._model = None

    def _load_openai_whisper(self):
        import whisper
        logger.info(f"Загрузка openai-whisper: {self.model_name}")
        return whisper.load_model(self.model_name)

    def _load_faster_whisper(self):
        from faster_whisper import WhisperModel
        # compute_type="int8" ускорит на CPU; можно выбрать "int8"|"float16"|"float32"
        logger.info(f"Загрузка faster-whisper: {self.model_name}")
        return WhisperModel(self.model_name, device="cpu", compute_type="int8")

    def load_model(self):
        if self._model is not None:
            return
        if self.backend == "openai":
            self._model = self._load_openai_whisper()
        else:
            self._model = self._load_faster_whisper()

    def transcribe_audio(self, audio_path: str) -> dict:
        self.load_model()
        if self.backend == "openai":
            # Совместимый формат ответа
            try:
                result = self._model.transcribe(audio_path, language='ru', verbose=False)
                return result
            except Exception as e:
                logger.error(f"Ошибка openai-whisper: {e}")
                raise
        else:
            # faster-whisper -> приведём к совместимому формату
            try:
                segments, info = self._model.transcribe(audio_path, language="ru")
                text = []
                seg_list = []
                for seg in segments:
                    seg_list.append({
                        "id": seg.id,
                        "start": seg.start,
                        "end": seg.end,
                        "text": seg.text.strip()
                    })
                    text.append(seg.text)
                return {"text": "".join(text).strip(), "segments": seg_list, "language": info.language}
            except Exception as e:
                logger.error(f"Ошибка faster-whisper: {e}")
                raise

    def format_transcription(self, result: dict, with_timestamps: bool = False) -> str:
        if not result or 'text' not in result:
            return "Не удалось распознать текст."
        if not with_timestamps:
            return result['text'].strip()
        out = []
        for seg in result.get('segments', []) or []:
            start = seg.get('start', 0)
            end = seg.get('end', 0)
            text = seg.get('text', '').strip()
            out.append(f"[{start:.0f}s-{end:.0f}s] {text}")
        return "\n".join(out).strip()

audio_processor = AudioProcessor()
