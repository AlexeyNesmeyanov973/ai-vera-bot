# app/analytics.py
import re
from collections import Counter
from typing import Dict, List, Tuple

# минимальные стоп-слова (можно расширять)
_STOP_RU = {
    "и","в","во","на","что","это","как","к","а","но","или","из","за","с","со","то","у",
    "по","так","же","мы","вы","он","она","они","оно","я","ты","не","да","нет","для",
    "от","до","при","над","под","ли","бы","же","были","был","была","есть","там","здесь",
}
_STOP_EN = {
    "the","and","a","an","in","on","at","to","for","of","is","are","was","were","be",
    "been","it","this","that","as","by","or","not","we","you","i","they","he","she",
    "from","with","about","into","over","after","before","but","so","if","then",
}

def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text.lower())

def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]

def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]

def analyze_text(text: str, lang_code: str | None = None) -> Dict:
    tokens = _tokenize(text)
    words = [t for t in tokens if t.isalpha()]
    word_count = len(words)
    char_count = len(text)
    sent_count = len(_sentences(text))
    para_count = len(_paragraphs(text))
    unique_words = len(set(words))

    # стоп-слова по языку
    stop = _STOP_EN
    if lang_code and lang_code.lower().startswith("ru"):
        stop = _STOP_RU

    filtered = [w for w in words if w not in stop and len(w) > 2]
    freq = Counter(filtered)
    top_words: List[Tuple[str, int]] = freq.most_common(10)

    # скорость чтения ~180 слов/мин
    reading_time_min = round(word_count / 180.0, 2) if word_count else 0.0

    return {
        "word_count": word_count,
        "unique_words": unique_words,
        "char_count": char_count,
        "sentences": sent_count,
        "paragraphs": para_count,
        "reading_time_min": reading_time_min,
        "top_words": top_words,
    }

def build_report_md(metrics: Dict) -> str:
    lines = [
        "📊 *Аналитика текста*",
        f"Слов: {metrics.get('word_count', 0)}",
        f"Уникальных слов: {metrics.get('unique_words', 0)}",
        f"Предложений: {metrics.get('sentences', 0)}",
        f"Абзацев: {metrics.get('paragraphs', 0)}",
        f"Время чтения: ~{metrics.get('reading_time_min', 0)} мин",
    ]
    tw = metrics.get("top_words") or []
    if tw:
        lines.append("\n*Топ-слова:*")
        lines += [f"• {w} — {c}" for w, c in tw]
    return "\n".join(lines)
