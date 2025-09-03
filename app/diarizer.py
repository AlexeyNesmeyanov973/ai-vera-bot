# app/diarizer.py
import logging
from typing import List, Dict
from app.config import DIARIZATION_BACKEND, HUGGINGFACE_TOKEN

logger = logging.getLogger(__name__)

class Diarizer:
    """
    diarize(audio_path) -> список сегментов: [{"start": float, "end": float, "speaker": "SPK1"}, ...]
    Бэкенды:
      - "pyannote": pyannote.audio (требует HUGGINGFACE_TOKEN)
      - "none": без диаризации (возвращает пустой список)
    """
    def __init__(self):
        self.backend = (DIARIZATION_BACKEND or "none").lower()
        self._pipeline = None

    def _load_pyannote(self):
        try:
            from pyannote.audio import Pipeline
        except Exception as e:
            raise RuntimeError("pyannote.audio не установлен") from e
        token = (HUGGINGFACE_TOKEN or "").strip() or None
        try:
            pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=token)
        except Exception:
            pipe = Pipeline.from_pretrained("pyannote/speaker-diarization", use_auth_token=token)
        return pipe

    def _ensure(self):
        if self._pipeline is not None or self.backend == "none":
            return
        if self.backend == "pyannote":
            self._pipeline = self._load_pyannote()
            logger.info("Diarizer: pyannote pipeline loaded")
        else:
            logger.info("Diarizer: disabled")

    def diarize(self, audio_path: str) -> List[Dict]:
        if self.backend == "none":
            return []
        self._ensure()
        if self._pipeline is None:
            return []
        try:
            try:
                annotation = self._pipeline(audio_path)
            except TypeError:
                annotation = self._pipeline({"audio": audio_path})
            segments: List[Dict] = []
            for segment, _, label in annotation.itertracks(yield_label=True):
                start = float(getattr(segment, "start", segment.start))
                end = float(getattr(segment, "end", segment.end))
                segments.append({"start": start, "end": end, "speaker": str(label)})

            # Нормализуем имена спикеров в SPK1..N
            mapping: Dict[str, str] = {}
            for s in segments:
                raw = s["speaker"]
                if raw not in mapping:
                    mapping[raw] = f"SPK{len(mapping)+1}"
                s["speaker"] = mapping[raw]
            return segments
        except Exception:
            logger.exception("Diarization failed")
            return []

diarizer = Diarizer()
