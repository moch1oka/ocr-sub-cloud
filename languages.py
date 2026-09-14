from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class LanguageConfig:
    code: str
    name: str
    # Bing OCR targeting
    bing_setlang: str
    bing_mkt: str
    bing_accept_lang: str
    # Google Lens targeting
    lens_lang: str
    lens_region: str
    # ASS Subtitle default font
    ass_font: str
    # Fuzzy merge thresholds
    fuzzy_threshold_short: float  # For text length < 10
    fuzzy_threshold_long: float   # For text length >= 10
    # Text post-processing flags
    cjk_mode: bool
    strip_cjk_spaces: bool


LANGUAGE_PRESETS: Dict[str, LanguageConfig] = {
    "vi": LanguageConfig(
        code="vi",
        name="Vietnamese",
        bing_setlang="vi",
        bing_mkt="vi-VN",
        bing_accept_lang="vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        lens_lang="vi",
        lens_region="VN",
        ass_font="Arial",
        fuzzy_threshold_short=0.90,
        fuzzy_threshold_long=0.85,
        cjk_mode=False,
        strip_cjk_spaces=False,
    ),
    "en": LanguageConfig(
        code="en",
        name="English",
        bing_setlang="en",
        bing_mkt="en-US",
        bing_accept_lang="en-US,en;q=0.9",
        lens_lang="en",
        lens_region="US",
        ass_font="Arial",
        fuzzy_threshold_short=0.90,
        fuzzy_threshold_long=0.85,
        cjk_mode=False,
        strip_cjk_spaces=False,
    ),
    "ja": LanguageConfig(
        code="ja",
        name="Japanese",
        bing_setlang="ja",
        bing_mkt="ja-JP",
        bing_accept_lang="ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        lens_lang="ja",
        lens_region="JP",
        ass_font="Meiryo",
        fuzzy_threshold_short=0.85,
        fuzzy_threshold_long=0.80,
        cjk_mode=True,
        strip_cjk_spaces=True,
    ),
    "zh": LanguageConfig(
        code="zh",
        name="Chinese",
        bing_setlang="zh-Hans",
        bing_mkt="zh-CN",
        bing_accept_lang="zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        lens_lang="zh-CN",
        lens_region="CN",
        ass_font="Microsoft YaHei",
        fuzzy_threshold_short=0.85,
        fuzzy_threshold_long=0.80,
        cjk_mode=True,
        strip_cjk_spaces=True,
    ),
}


import warnings


def get_language_config(code: str | None) -> LanguageConfig:
    """Returns the LanguageConfig for the specified language code (default: 'vi')."""
    if not code:
        return LANGUAGE_PRESETS["vi"]
    normalized = code.strip().lower()
    if normalized not in LANGUAGE_PRESETS:
        warnings.warn(
            f"Language preset '{code}' is not recognized. Falling back to 'vi'. "
            f"Available presets: {list(LANGUAGE_PRESETS.keys())}"
        )
        return LANGUAGE_PRESETS["vi"]
    return LANGUAGE_PRESETS[normalized]
