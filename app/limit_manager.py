# app/audio_processor.py
import logging
import asyncio
import os
from typing import Dict, List, Any, Optional

from app.config import WHISPER_BACKEND, WHISPER_MODEL, WHISPER_LANGUAGE

logger = logging.getLogger(__name__)


class AudioProcessor:
    """
    Унифицированный интерфейс распознавания:
      - WHISPER_BACKEND = "faster" (по умолчанию) -> faster-whisper
      - WHISPER_BACKEND = "openai"                -> openai-whisper

    WHISPER_LANGUAGE: "auto" / "ru" / "en" / ...

    Методы:
      • transcribe_audio(path) — синхронное API
      • async transcribe(path) — асинхронная обёртка (использует to_thread)
    """

    def __init__(self) -> None:
        self.backend = (WHISPER_BACKEND or "faster").lower()
        self.model_name = WHISPER_MODEL or "small"
        # корректная интерпретация "auto"
        lang_cfg = (WHISPER_LANGUAGE or "auto").strip().lower()
        self.language: Optional[str] = None if lang_cfg == "auto" else lang_cfg
        self._model = None

        # Тонкая настройка faster-whisper через ENV (без обязательной зависимости от config.py)
        self._fw_compute_type = os.getenv("FAST_WHISPER_COMPUTE_TYPE", "int8")  # int8, int8_float16, float16, float32
        self._fw_beam_size = int(os.getenv("FAST_WHISPER_BEAM_SIZE", "5"))
        self._fw_vad_filter = os.getenv("FAST_WHISPER_VAD_FILTER", "1") in ("1", "true", "yes")
        self._fw_cpu_threads = int(os.getenv("FAST_WHISPER_CPU_THREADS", "0"))  # 0 = авто

    # ---------- загрузка моделей ----------

    def _load_openai_whisper(self):
        try:
            import whisper  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "WHISPER_BACKEND=openai, но пакет 'openai-whisper' не установлен. "
                "Добавьте `openai-whisper==20231117` в requirements.txt или переключитесь на WHISPER_BACKEND=faster."
            ) from e
        logger.info("[whisper(openai)] загрузка модели: %s", self.model_name)
        return whisper.load_model(self.model_name)

    def _load_faster_whisper(self):
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "WHISPER_BACKEND=faster, но пакет 'faster-whisper' не установлен."
            ) from e

        logger.info(
            "[whisper(faster)] загрузка модели: %s (device=cpu, compute_type=%s, cpu_threads=%s, beam=%s, vad=%s)",
            self.model_name, self._fw_compute_type, self._fw_cpu_threads, self._fw_beam_size, self._fw_vad_filter
        )
        model = WhisperModel(
            self.model_name,
            device="cpu",
            compute_type=self._fw_compute_type,
            cpu_threads=(None if self._fw_cpu_threads <= 0 else self._fw_cpu_threads),
        )
        return model

    def load_model(self):
        if self._model is not None:
            return
        self._model = self._load_openai_whisper() if self.backend == "openai" else self._load_faster_whisper()

    # ---------- публичное API ----------

    def transcribe_audio(self, audio_path: str) -> Dict[str, Any]:
        """Синхронное распознавание (используется внутри async-обёртки)."""
        self.load_model()
        lang = self.language  # None -> автоопределение (если поддерживается бэкендом)

        if self.backend == "openai":
            # openai-whisper возвращает dict
            result = self._model.transcribe(audio_path, language=lang, verbose=False)
            segments_out: List[Dict[str, Any]] = []
            for seg in result.get("segments") or []:
                # у openai-whisper сегменты — dict; иногда это объекты с атрибутами
                sid = seg.get("id", 0)
                start = seg.get("start", 0.0)
                end = seg.get("end", 0.0)
                text = seg.get("text", "")
                try:
                    sid = int(getattr(seg, "id", sid))
                    start = float(getattr(seg, "start", start))
                    end = float(getattr(seg, "end", end))
                    text = str(getattr(seg, "text", text))
                except Exception:
                    pass
                segments_out.append({
                    "id": int(sid),
                    "start": float(start),
                    "end": float(end),
                    "text": text.strip(),
                })
            duration = float(result.get("duration", 0.0))
            if duration <= 0.0 and segments_out:
                duration = float(segments_out[-1]["end"])
            return {
                "text": (result.get("text") or "").strip(),
                "segments": segments_out,
                "language": result.get("language"),
                "duration": duration,
                "title": os.path.basename(audio_path),
            }

        # faster-whisper
        segments_iter, info = self._model.transcribe(
            audio_path,
            language=lang,
            beam_size=self._fw_beam_size,
            vad_filter=self._fw_vad_filter,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        text_parts: List[str] = []
        segments_out: List[Dict[str, Any]] = []
        last_end = 0.0
        for seg in segments_iter:
            t = (seg.text or "").strip()
            text_parts.append(t)
            segments_out.append({
                "id": int(getattr(seg, "id", 0)),
                "start": float(getattr(seg, "start", 0.0)),
                "end": float(getattr(seg, "end", 0.0)),
                "text": t,
            })
            if getattr(seg, "end", None) is not None:
                last_end = float(seg.end)

        duration = getattr(info, "duration", None)
        duration = float(duration) if duration is not None else last_end

        return {
            "text": "".join(text_parts).strip(),
            "segments": segments_out,
            "language": getattr(info, "language", None),
            "duration": duration,
            "title": os.path.basename(audio_path),
        }

    async def transcribe(self, audio_path: str, language: Optional[str] = None) -> Dict[str, Any]:
        """
        Асинхронная обёртка. При переданном language временно переопределяет язык.
        """
        if language is not None:
            prev = self.language
            try:
                self.language = language
                return await asyncio.to_thread(self.transcribe_audio, audio_path)
            finally:
                self.language = prev
        return await asyncio.to_thread(self.transcribe_audio, audio_path)

    def format_transcription(self, result: Dict[str, Any], with_timestamps: bool = False) -> str:
        if not result or "text" not in result:
            return "Не удалось распознать текст."
        if not with_timestamps:
            return (result.get("text") or "").strip()
        out_lines: List[str] = []
        for seg in result.get("segments") or []:
            out_lines.append(
                f"[{seg.get('start', 0):.0f}s–{seg.get('end', 0):.0f}s] {str(seg.get('text','')).strip()}"
            )
        return "\n".join(out_lines).strip()


audio_processor = AudioProcessor()
