"""
video_translate_bot.py
----------------------
בוט טלגרם לתרגום סרטונים + כתוביות צורבות + הטמעת לוגו.
כולל התקנות אוטומטיות בהפעלה הראשונה ובדיקה ש-FFmpeg זמין (או בינארי נייד).

מדריך התקנה ידני (לא חובה אם מריצים את הקובץ כפי שהוא):
-------------------------------------------------------
pip install python-telegram-bot==13.7 faster-whisper googletrans==4.0.0-rc1 langdetect Pillow imageio-ffmpeg
# אופציונלי (למקרה שאין faster-whisper):
pip install openai-whisper

חובה חיבור אינטרנט בשביל התקנות אוטומטיות בהפעלה הראשונה.
אם אין הרשאות מערכת – ההתקנה תתבצע ל-user site-packages (הוסף --user אם צריך).
"""

import os
import sys
import re
import ast
import json
import uuid
import time
import math
import shutil
import queue
import atexit
import errno
import tempfile
import logging
import threading
import subprocess
import multiprocessing
from pathlib import Path
from typing import List, Tuple, Dict, Optional, Any, Union, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import psutil
import bidi.algorithm as bidi  # For RTL support in Hebrew

# -----------------------------
# לוגים בסיסיים ונקיים
# -----------------------------
LOG = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# -----------------------------
# קבועים כלליים
# -----------------------------
APP_DIR = Path.home() / "telegram_temp"
APP_DIR.mkdir(parents=True, exist_ok=True)

# הסרת טוקן קבוע מהקוד והעברה לקובץ קונפיגורציה חיצוני
CONFIG_FILE = Path.home() / ".telegram_video_bot.conf"
FFMPEG_BIN: Optional[str] = None

# פונקציה לקריאת טוקן מקובץ קונפיגורציה
def get_bot_token() -> Optional[str]:
    """
    מנסה לקרוא טוקן מקובץ קונפיגורציה או משתנה סביבה BOT_TOKEN
    אם אין - מבקש מהמשתמש להזין טוקן
    """
    # 1. בדיקה אם הטוקן מוגדר כמשתנה סביבה
    token = os.getenv("BOT_TOKEN")
    if token and ":" in token:
        return tokenא
        
    # 2. בדיקה אם קיים קובץ קונפיגורציה
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                token = config.get("bot_token")
                if token and ":" in token:
                    return token
        except Exception:
            pass
            
    # 3. אם אין טוקן - בקש מהמשתמש
    print("\n" + "=" * 60)
    print("הגדרת טוקן לבוט טלגרם")
    print("=" * 60)
    print("טוקן לא נמצא בקובץ קונפיגורציה או כמשתנה סביבה.")
    print("אנא הזן את הטוקן שקיבלת מ-BotFather:")
    token = input("Bot Token: ").strip()
    
    # שמירת הטוקן לקובץ קונפיגורציה
    if token and ":" in token:
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"bot_token": token}, f)
            print(f"הטוקן נשמר בקובץ: {CONFIG_FILE}")
            os.chmod(CONFIG_FILE, 0o600)  # הרשאות קריאה/כתיבה רק לבעלים
            return token
        except Exception as e:
            print(f"שגיאה בשמירת הטוקן: {e}")
    
    print("הטוקן שהוזן אינו תקין. הפעל שוב את הבוט והזן טוקן תקין.")
    return None

# הגדרות עיבוד מקבילי
def get_optimal_workers():
    """קביעת מספר אופטימלי של תהליכים בהתאם למשאבי המערכת"""
    try:
        # מספר ליבות לוגיות
        cpu_count = multiprocessing.cpu_count()
        
        # בדיקת זיכרון פנוי
        mem = psutil.virtual_memory()
        available_mem_gb = mem.available / (1024 * 1024 * 1024)
        
        # נשתמש במודל פשוט:
        # - לפחות 2 תהליכים
        # - לכל היותר מספר הליבות
        # - לפחות 1GB זיכרון פנוי לכל תהליך
        mem_based_limit = max(2, int(available_mem_gb))
        workers = min(cpu_count, mem_based_limit)
        
        # רשום לוג
        LOG.info(f"מערכת: {cpu_count} ליבות, {available_mem_gb:.1f}GB זיכרון פנוי. נקבעו {workers} תהליכים במקביל.")
        return max(2, workers)  # לפחות 2 תהליכים
    except Exception as e:
        LOG.warning(f"שגיאה בקביעת מספר תהליכים: {e}, משתמש בברירת מחדל")
        return 2  # ברירת מחדל שמרנית

# הקמת מאגר תהליכים גלובלי
MAX_WORKERS = get_optimal_workers()
EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS)

@atexit.register
def shutdown_executor():
    """סגירה מסודרת של מאגר התהליכים בעת יציאה"""
    try:
        if EXECUTOR:
            LOG.info("סוגר מאגר תהליכים...")
            EXECUTOR.shutdown(wait=False)
    except Exception as e:
        LOG.error(f"שגיאה בסגירת מאגר תהליכים: {e}")

# מסך צבעים לכתוביות (ASS PrimaryColour בפורמט &HBBGGRR)
ASS_COLORS = {
    "white":   "&H00FFFFFF",
    "yellow":  "&H0000FFFF",
    "black":   "&H00000000",
    "red":     "&H000000FF",
    "blue":    "&H00FF0000",
    "green":   "&H0000FF00",
    "cyan":    "&H00FFFF00",
    "magenta": "&H00FF00FF",
    "orange":  "&H000080FF",  # צבעים נוספים
    "pink":    "&H00FF00FF",
    "purple":  "&H00800080",
    "gray":    "&H00808080",
}

# הגדרות מתקדמות לכתוביות
SUBTITLE_POSITIONS = {
    "bottom":     "2", # ברירת מחדל - מרכז תחתון
    "top":        "8", # מרכז עליון
    "bottom-left": "1", # שמאל תחתון
    "bottom-right": "3", # ימין תחתון
    "top-left":   "7", # שמאל עליון
    "top-right":  "9", # ימין עליון
    "middle":     "5", # מרכז
}

# סוגי גופנים נתמכים לכתוביות
SUBTITLE_FONTS = {
    "arial": "Arial",
    "times": "Times New Roman",
    "courier": "Courier New",
    "impact": "Impact",
    "comic": "Comic Sans MS",
    "tahoma": "Tahoma",
    "verdana": "Verdana",
    "david": "David", # עברית
    "narkisim": "Narkisim", # עברית
    "miriam": "Miriam", # עברית
}

# הגדרות סגנון מתקדמות לכתוביות
class SubtitleConfig:
    """מחלקה לניהול הגדרות כתוביות מתקדמות"""
    
    def __init__(self):
        self.font_size = 16           # גודל גופן
        self.font_color = "white"     # צבע גופן
        self.font_name = "arial"      # סוג גופן
        self.position = "bottom"      # מיקום
        self.outline_size = 1         # גודל מתאר (קו חיצוני)
        self.shadow_size = 1          # גודל צל
        self.bold = False             # הדגשה
        self.italic = False           # הטיה
        self.background_color = "black"  # צבע מתאר/רקע
        
    def get_ass_style(self) -> str:
        """יצירת מחרוזת סגנון ASS לפי ההגדרות"""
        style_parts = [
            f"FontSize={self.font_size}",
            f"PrimaryColour={ASS_COLORS.get(self.font_color, '&H00FFFFFF')}",
            f"OutlineColour={ASS_COLORS.get(self.background_color, '&H00000000')}",
            f"BorderStyle=1",
            f"Outline={self.outline_size}",
            f"Shadow={self.shadow_size}",
            f"Alignment={SUBTITLE_POSITIONS.get(self.position, '2')}",
        ]
        
        # הוספת סגנונות נוספים אם צריך
        if self.font_name in SUBTITLE_FONTS:
            style_parts.append(f"FontName={SUBTITLE_FONTS[self.font_name]}")
        if self.bold:
            style_parts.append("Bold=1")
        if self.italic:
            style_parts.append("Italic=1")
            
        return ",".join(style_parts)
        
    @classmethod
    def from_user_state(cls, state: Dict) -> 'SubtitleConfig':
        """יצירת אובייקט קונפיגורציה מנתוני המשתמש"""
        config = cls()
        
        if "font_size" in state:
            config.font_size = state["font_size"]
        if "font_color" in state:
            config.font_color = state["font_color"]
        if "subtitle_position" in state:
            config.position = state["subtitle_position"]
        if "font_name" in state:
            config.font_name = state["font_name"]
        if "outline_size" in state:
            config.outline_size = state["outline_size"]
        if "shadow_size" in state:
            config.shadow_size = state["shadow_size"]
        if "bold" in state:
            config.bold = state["bold"]
        if "italic" in state:
            config.italic = state["italic"]
        if "background_color" in state:
            config.background_color = state["background_color"]
            
        return config

# מפה של שפות עם דגלי מדינות. ברירת מחדל: en; חובה לכלול he.
LANG_CHOICES = [
    ("🇺🇸 English", "en"), ("🇮🇱 עברית", "he"), ("🇸🇦 العربية", "ar"), ("🇷🇺 Русский", "ru"),
    ("🇫🇷 Français", "fr"), ("🇪🇸 Español", "es"), ("🇩🇪 Deutsch", "de"), ("🇮🇹 Italiano", "it"),
    ("🇵🇹 Português", "pt"), ("🇯🇵 日本語", "ja"), ("🇰🇷 한국어", "ko"), ("🇨🇳 中文", "zh-cn"),
    ("🇹🇷 Türkçe", "tr"), ("🇵🇱 Polski", "pl"), ("🇺🇦 Українська", "uk"), ("🇳🇱 Nederlands", "nl"),
    ("🇸🇪 Svenska", "sv"), ("🇳🇴 Norsk", "no"), ("🇫🇮 Suomi", "fi"), ("🇮🇳 हिंदी", "hi"),
    ("🇹🇭 ไทย", "th"), ("🇮🇩 Bahasa Indonesia", "id"), ("🇮🇷 فارسی", "fa"), ("🇷🇴 Română", "ro")
]

# טקסטים לממשק משתמש לפי שפה (הרחבה קלה: en/he). מפתחות אחידים לכל ההודעות/כפתורים.
UI_STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        # Buttons (main)
        "btn_ui_lang": "🌐 Interface language",
        "btn_target_lang": "🎯 Target translation language",
        "btn_font_size": "🔤 Subtitle font size",
        "btn_font_color": "🎨 Subtitle color",
        "btn_upload_video": "📥 Upload video for translation & burn",
        "btn_logo": "🖼️ Overlay a logo",
        "btn_logo_size": "📏 Logo size",
        "btn_help": "ℹ️ Help",
        "btn_back_main": "⬅️ Back to main menu",
        
        # Advanced subtitle settings
        "btn_subtitle_position": "📍 Subtitle position",
        "btn_font_type": "🔠 Font type",
        "btn_subtitle_outline": "🖋️ Outline size",
        "btn_subtitle_shadow": "👥 Shadow size",
        "btn_subtitle_style": "🎭 Text style",
        "btn_background_color": "🎨 Background color",

        # Prompts
        "prompt_choose_ui_lang": "Choose interface language:",
        "prompt_choose_target_lang": "Choose target language for translation:",
        "prompt_choose_font_size": "Choose subtitle font size:",
        "prompt_choose_font_color": "Choose subtitle color:",
        "prompt_logo_size": "Choose logo size (percent of video height):",
        "prompt_logo_start": "Upload a logo image (JPEG/PNG). After upload, choose position/size/opacity.",
        "prompt_back_main": "Back to main menu:",

        # Start/Help
        "start_message": (
            "Hi! 👋\n"
            "I'm a bot for translating videos + burning subtitles + overlaying a logo.\n\n"
            "Recommended flow: choose target language → choose font size/color → upload a video.\n"
            "Supported files: mp4/mov/mkv/avi/flv up to 20MB.\n"
            "Logo: upload an image → choose position/size/opacity → upload a video for overlay."
        ),
        "help_message": (
            "ℹ️ Help:\n"
            "• '🎯 Target translation language' – choose the translation language.\n"
            "• '🔤 Font size' + '🎨 Color' – subtitle styling.\n"
            "• '📥 Upload video' – send a video/document up to 20MB.\n"
            "• '🖼️ Overlay a logo' – upload logo → choose position/opacity → send a video."
        ),

        # Feedback
        "ui_lang_set_to": "✅ Interface language set to {lang_name}.",
        "target_lang_set_to": "✅ Target language set to {lang_name}.",
        "settings_current_title": "Current settings:",
        "settings_language": "🎯 Language",
        "settings_font_size": "🔤 Font size",
        "settings_color": "🎨 Color",
        "logo_uploaded_success": "✅ Logo uploaded!\n\nNow choose the position:",
        "logo_size_set": "✅ Logo size set to {size}%.",
        "logo_pos_set": "✅ Logo position set to {pos_name}. Now choose opacity:",
        "logo_size_set_in_flow": "✅ Logo size set to {size}%. Now upload a video to overlay the logo.",
        "logo_opacity_set": "✅ Opacity set to {opacity}%. Now choose size:",
        "choose_from_menu": "Choose from the menu:",
        "upload_video_prompt": (
            "📥 Send a video file (mp4/mov/mkv/avi/flv) up to 20MB to translate and burn subtitles.\n\n"
            "{settings_title}\n{lang_label}: {lang}\n{size_label}: {size}\n{color_label}: {color}"
        ),
        "back_main_done": "🎉 Done! What would you like to do next?",

        # Changes confirmations
        "font_size_set": "✅ Font size set to {size}.",
        "font_color_set": "✅ Font color set to {color_name}.",

        # Generic flows
        "downloading_video": "⬇️ Downloading the video...",
        "processing_start": (
            "🎬 Starting video processing...\n\n"
            "Settings:\n{lang_label}: {lang}\n{size_label}: {size}\n{color_label}: {color}"
        ),
        "convert_audio": "🔊 Converting audio...",
        "transcribing": "📝 Transcribing (auto language detection)...",
        "translating_to": "🌐 Translating to {lang}...",
        "burning_subtitles": "🎬 Burning subtitles to the video...",
        "output_too_big": "⚠️ Output is too large for Telegram (>20MB). Try a shorter video or reduce resolution.",
        "translated_done_caption": (
            "✅ Video translated and burned successfully!\n\n"
            "Applied settings:\n{lang_label}: {lang}\n{size_label}: {size}\n{color_label}: {color}\n\nSource language: {src_lang}"
        ),
        "error_processing": "❌ Processing failed: {error}",
        "error_logo": "❌ Logo processing failed: {error}",
        "doc_not_video": "The document is not a supported video file.",
        "no_suitable_file": "No suitable file detected.",
        "file_too_large": "❌ File is too large (over 20MB). Please try a smaller file.",
        "no_logo_found": "No logo file found. Start with '🖼️ Overlay a logo' and upload a logo.",
        "received_video_but_wrong_state": "Received a video, but not in 'Upload video for translation' mode. Click '📥 Upload video for translation & burn' first.",

        # Logo flow
        "logo_processing_start": (
            "🎬 Starting logo overlay...\n\n"
            "Settings:\n📍 Position: {pos_name}\n📏 Size: {size}%\n🎭 Opacity: {opacity}%"
        ),
        "logo_done_caption": (
            "✅ Logo overlaid successfully!\n\n"
            "Applied settings:\n📍 Position: {pos_name}\n📏 Size: {size}%\n🎭 Opacity: {opacity}%"
        ),

        # Error messages (new)
        "error_file_too_large": "❌ הקובץ גדול מדי!\n\nהקובץ שהעלית גדול מ-20MB. אנא העלה קובץ קטן יותר.",
        "error_unsupported_file_type": "❌ סוג קובץ לא נתמך!\n\nהקובץ שהעלית אינו מסוג נתמך. קבצים נתמכים: mp4, mov, mkv, avi, flv.",
        "error_image_too_large": "❌ תמונת הלוגו גדולה מדי!\n\nאנא העלה תמונה קטנה יותר (עד 5MB).",
        "error_unsupported_image": "❌ סוג תמונה לא נתמך!\n\nאנא העלה תמונה בפורמט JPEG או PNG.",
        "error_processing_failed": "❌ כשל בעיבוד!\n\nאירעה שגיאה בעיבוד הקובץ. אנא נסה שוב או העלה קובץ אחר.",
        "error_no_internet": "❌ בעיית חיבור!\n\nאין חיבור לאינטרנט. אנא בדוק את החיבור ונסה שוב.",
        "error_upload_failed": "❌ כשל בהעלאה!\n\nהקובץ לא הועלה בהצלחה. אנא נסה שוב.",
        "error_invalid_file": "❌ קובץ לא תקין!\n\nהקובץ שהעלית פגום או לא תקין. אנא העלה קובץ אחר.",
        "error_process_in_progress": "❌ תהליך פעיל!\n\nיש לך תהליך פעיל כרגע. אנא סיים את התהליך הנוכחי לפני שתתחיל חדש.",
        "error_logo_process_in_progress": "❌ תהליך הטמעת לוגו פעיל!\n\nיש לך תהליך הטמעת לוגו פעיל כרגע. אנא סיים אותו לפני שתתחיל תהליך חדש.",
        "error_translation_process_in_progress": "❌ תהליך תרגום פעיל!\n\nיש לך תהליך תרגום פעיל כרגע. אנא סיים אותו לפני שתתחיל תהליך חדש.",
    },
    "he": {
        # Buttons (main)
        "btn_ui_lang": "🌐 שפת ממשק",
        "btn_target_lang": "🎯 בחירת שפת יעד לתרגום",
        "btn_font_size": "🔤 גודל גופן לכתוביות",
        "btn_font_color": "🎨 צבע גופן",
        "btn_upload_video": "📥 העלאת סרטון לתרגום וצריבה",
        "btn_logo": "🖼️ הטמעת לוגו",
        "btn_logo_size": "📏 גודל לוגו",
        "btn_help": "ℹ️ עזרה",
        "btn_back_main": "⬅️ חזרה לתפריט הראשי",

        # Prompts
        "prompt_choose_ui_lang": "בחרו שפת ממשק:",
        "prompt_choose_target_lang": "בחרו שפת יעד לתרגום:",
        "prompt_choose_font_size": "בחרו גודל גופן לכתוביות:",
        "prompt_choose_font_color": "בחרו צבע גופן:",
        "prompt_logo_size": "בחרו גודל לוגו (באחוזים מהגובה):",
        "prompt_logo_start": "🖼️ העלו תמונת לוגו (JPEG/PNG). לאחר ההעלאה תוכלו לבחור מיקום/גודל/שקיפות.",
        "prompt_back_main": "חזרה לתפריט הראשי:",

        # Start/Help
        "start_message": (
            "היי! 👋\n"
            "אני בוט לתרגום סרטונים + צורב כתוביות + הטמעת לוגו.\n\n"
            "זרימה מומלצת: בחרו שפת יעד → בחרו גודל/צבע → לחצו העלאת סרטון.\n"
            "קבצים נתמכים: mp4/mov/mkv/avi/flv עד 20MB.\n"
            "לוגו: העלו תמונה → בחרו מיקום/גודל/שקיפות → העלו סרטון להטמעת הלוגו."
        ),
        "help_message": (
            "ℹ️ עזרה:\n"
            "• '🎯 בחירת שפת יעד' – בחרו את שפת התרגום.\n"
            "• '🔤 גודל גופן' + '🎨 צבע' – עיצוב הכתוביות.\n"
            "• '📥 העלאת סרטון' – שלחו וידאו/מסמך וידאו עד 20MB.\n"
            "• '🖼️ הטמעת לוגו' – העלו לוגו → בחרו מיקום/שקיפות → שלחו וידאו."
        ),

        # Feedback
        "ui_lang_set_to": "✅ שפת הממשק נקבעה ל-{lang_name}.",
        "target_lang_set_to": "✅ שפת היעד נקבעה ל-{lang_name}.",
        "settings_current_title": "הגדרות נוכחיות:",
        "settings_language": "🎯 שפה",
        "settings_font_size": "🔤 גודל גופן",
        "settings_color": "🎨 צבע",
        "logo_uploaded_success": "✅ הלוגו הועלה בהצלחה!\n\nכעת בחרו את המיקום הרצוי:",
        "logo_size_set": "✅ גודל הלוגו נקבע ל-{size}%.",
        "logo_pos_set": "✅ מיקום הלוגו נקבע ל-{pos_name}. כעת בחרו שקיפות:",
        "logo_size_set_in_flow": "✅ גודל הלוגו נקבע ל-{size}%. העלו כעת וידאו להטמעת הלוגו.",
        "logo_opacity_set": "✅ שקיפות נקבעה ל-{opacity}%. כעת בחרו גודל:",
        "choose_from_menu": "בחרו מהתפריט:",
        "upload_video_prompt": (
            "📥 שלחו קובץ וידאו (mp4/mov/mkv/avi/flv) עד 20MB לתרגום וצריבת כתוביות.\n\n"
            "{settings_title}\n{lang_label}: {lang}\n{size_label}: {size}\n{color_label}: {color}"
        ),
        "back_main_done": "🎉 בוצע בהצלחה! מה תרצו לעשות עכשיו?",

        # Changes confirmations
        "font_size_set": "✅ גודל הגופן נקבע ל-{size}.",
        "font_color_set": "✅ צבע הגופן נקבע ל-{color_name}.",

        # Generic flows
        "downloading_video": "⬇️ מוריד את הווידאו...",
        "processing_start": (
            "🎬 מתחיל עיבוד הסרטון...\n\n"
            "הגדרות:\n{lang_label}: {lang}\n{size_label}: {size}\n{color_label}: {color}"
        ),
        "convert_audio": "🔊 ממיר אודיו...",
        "transcribing": "📝 מתמלל (זיהוי שפה אוטומטי)...",
        "translating_to": "🌐 מתרגם ל-{lang}...",
        "burning_subtitles": "🎬 צורב כתוביות על הווידאו...",
        "output_too_big": "⚠️ הפלט גדול מדי לשליחה בטלגרם (>20MB). נסו וידאו קצר יותר או הקטנת רזולוציה.",
        "translated_done_caption": (
            "✅ הסרטון תורגם וצורב בהצלחה!\n\n"
            "הגדרות שהוחלו:\n{lang_label}: {lang}\n{size_label}: {size}\n{color_label}: {color}\n\nשפת מקור: {src_lang}"
        ),
        "error_processing": "❌ כשל בעיבוד: {error}",
        "error_logo": "❌ כשל בהטמעת לוגו: {error}",
        "doc_not_video": "המסמך אינו קובץ וידאו נתמך.",
        "no_suitable_file": "לא זוהה קובץ מתאים.",
        "file_too_large": "❌ הקובץ גדול מדי (מעל 20MB). נסו קובץ קטן יותר.",
        "no_logo_found": "לא נמצא קובץ לוגו. התחילו ב-'🖼️ הטמעת לוגו' והעלו לוגו.",
        "received_video_but_wrong_state": "קיבלתי וידאו, אך איני במצב 'העלאת סרטון לתרגום'. לחצו '📥 העלאת סרטון לתרגום וצריבה' תחילה.",

        # Logo flow
        "logo_processing_start": (
            "🎬 מתחיל הטמעת לוגו...\n\n"
            "הגדרות:\n📍 מיקום: {pos_name}\n📏 גודל: {size}%\n🎭 שקיפות: {opacity}%"
        ),
        "logo_done_caption": (
            "✅ הלוגו הוטמע בהצלחה!\n\n"
            "הגדרות שהוחלו:\n📍 מיקום: {pos_name}\n📏 גודל: {size}%\n🎭 שקיפות: {opacity}%"
        ),

        # Error messages (new)
        "error_file_too_large": "❌ הקובץ גדול מדי!\n\nהקובץ שהעלית גדול מ-20MB. אנא העלה קובץ קטן יותר.",
        "error_unsupported_file_type": "❌ סוג קובץ לא נתמך!\n\nהקובץ שהעלית אינו מסוג נתמך. קבצים נתמכים: mp4, mov, mkv, avi, flv.",
        "error_image_too_large": "❌ תמונת הלוגו גדולה מדי!\n\nאנא העלה תמונה קטנה יותר (עד 5MB).",
        "error_unsupported_image": "❌ סוג תמונה לא נתמך!\n\nאנא העלה תמונה בפורמט JPEG או PNG.",
        "error_processing_failed": "❌ כשל בעיבוד!\n\nאירעה שגיאה בעיבוד הקובץ. אנא נסה שוב או העלה קובץ אחר.",
        "error_no_internet": "❌ בעיית חיבור!\n\nאין חיבור לאינטרנט. אנא בדוק את החיבור ונסה שוב.",
        "error_upload_failed": "❌ כשל בהעלאה!\n\nהקובץ לא הועלה בהצלחה. אנא נסה שוב.",
        "error_invalid_file": "❌ קובץ לא תקין!\n\nהקובץ שהעלית פגום או לא תקין. אנא העלה קובץ אחר.",
        "error_process_in_progress": "❌ תהליך פעיל!\n\nיש לך תהליך פעיל כרגע. אנא סיים את התהליך הנוכחי לפני שתתחיל חדש.",
        "error_logo_process_in_progress": "❌ תהליך הטמעת לוגו פעיל!\n\nיש לך תהליך הטמעת לוגו פעיל כרגע. אנא סיים אותו לפני שתתחיל תהליך חדש.",
        "error_translation_process_in_progress": "❌ תהליך תרגום פעיל!\n\nיש לך תהליך תרגום פעיל כרגע. אנא סיים אותו לפני שתתחיל תהליך חדש.",
    },
}

def get_ui_lang(uid: int) -> str:
    st = USER_STATE.get(uid)
    if st and st.get("ui_lang"):
        return st["ui_lang"]
    return "en"

UI_DYNAMIC_CACHE: Dict[str, Dict[str, str]] = {}
UI_TXT_LOCK = threading.Lock()

def _preserve_placeholders_before_translate(text: str) -> (str, Dict[str, str]):
    # מחליף placeholders כמו {name} בטוקנים זמניים כדי למנוע שינוי בתרגום
    tokens: Dict[str, str] = {}
    def repl(m):
        name = m.group(1)
        token = f"__PH_{len(tokens)}__"
        tokens[token] = name
        return token
    import re as _re
    protected = _re.sub(r"\{(\w+)\}", repl, text)
    return protected, tokens

def _restore_placeholders_after_translate(text: str, tokens: Dict[str, str]) -> str:
    for token, name in tokens.items():
        text = text.replace(token, f"{{{name}}}")
    return text

def _translate_ui_text(text: str, dest_lang: str) -> str:
    if not text:
        return text
    if dest_lang in ("en", None, ""):
        return text
    # מטמון
    with UI_TXT_LOCK:
        cached = UI_DYNAMIC_CACHE.get(dest_lang, {}).get(text)
    if cached:
        return cached

    protected, tokens = _preserve_placeholders_before_translate(text)
    translated = None

    api_key = os.getenv("DEEPL_API_KEY")
    if api_key:
        try:
            import deepl
            translator = deepl.Translator(api_key)
            for attempt in range(2):
                try:
                    tr = translator.translate_text(protected, target_lang=dest_lang.upper())
                    translated = tr.text
                    break
                except Exception:
                    time.sleep(0.5 * (attempt + 1))
        except Exception:
            translated = None
    if translated is None:
        try:
            from googletrans import Translator as _GT
            _tr = _GT()
            for attempt in range(2):
                try:
                    tr = _tr.translate(protected, dest=dest_lang)
                    translated = tr.text
                    break
                except Exception:
                    time.sleep(0.5 * (attempt + 1))
        except Exception:
            translated = None

    out = _restore_placeholders_after_translate(translated or text, tokens)
    with UI_TXT_LOCK:
        UI_DYNAMIC_CACHE.setdefault(dest_lang, {})[text] = out
    return out

def t(uid: int, key: str, **kwargs) -> str:
    lang = get_ui_lang(uid)
    base = UI_STRINGS.get(lang, {})
    if key in base:
        text = base[key]
    else:
        # נשתמש בבסיס האנגלי ונבצע תרגום אוטומטי לשפת הממשק שנבחרה
        text = _translate_ui_text(UI_STRINGS["en"].get(key, key), lang)
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text

FONT_SIZES = [6, 8, 10, 12, 14, 16, 18]
COLOR_CHOICES = [
    ("⚪ לבן", "white"), ("🟡 צהוב", "yellow"), ("⚫ שחור", "black"), ("🔴 אדום", "red"),
    ("🔵 כחול", "blue"), ("🟢 ירוק", "green"), ("🔵 תכלת", "cyan"), ("🟣 מג'נטה", "magenta")
]

# מיקומי לוגו מורחבים - 7 אופציות
LOGO_POSITIONS = [
    ("🔴 פינה ימין עליונה", "TR"),
    ("🔴 פינה שמאל עליונה", "TL"), 
    ("🔴 מרכז עליון", "TC"),
    ("🔴 פינה ימין למטה", "BR"),
    ("🔴 פינה שמאל למטה", "BL"),
    ("🔴 מרכז למטה", "BC"),
    ("🔴 אמצע הסרטון (ממורכז)", "MC")
]

# גודל לוגו באחוזים
LOGO_SIZE_CHOICES = [0, 5, 10, 15, 20, 25, 30, 35, 40]  # 9 דרגות בין 0% ל-40%

OPACITY_CHOICES = [0, 15, 30, 45, 60, 75, 90, 100]  # 8 דרגות בין 0% ל-100%

ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".flv"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

# -----------------------------
# התקנות אוטומטיות
# -----------------------------
def require(pkg: str, pip_name: Optional[str] = None, version: Optional[str] = None):
    """
    נסה לייבא חבילה; אם אין – התקן אוטומטית ואז נסה שוב.
    """
    mod_name = pip_name or pkg
    try:
        __import__(pkg)
        return True
    except Exception:
        # נסיון התקנה
        install_spec = mod_name + (f"=={version}" if version else "")
        LOG.info(f"🔧 מתקין תלות חסרה: {install_spec}")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", install_spec],
                check=False
            )
            __import__(pkg)
            return True
        except Exception as e:
            LOG.error(f"❌ כשל בהתקנת {install_spec}: {e}")
            return False

def ensure_dependencies():
    ok = True
    ok &= require("telegram", "python-telegram-bot", "13.7")
    ok &= require("googletrans", "googletrans", "4.0.0-rc1")
    ok &= require("langdetect", "langdetect")
    ok &= require("PIL", "Pillow")
    ok &= require("imageio_ffmpeg", "imageio-ffmpeg")
    # openai-whisper - המודל הגדול והארוך
    if not require("whisper", "openai-whisper"):
        LOG.error("❌ openai-whisper לא זמין. יש להתקין אותו.")
        ok = False
    # התקנה של deepl-python מגיט (לא זמין ב-PyPI)
    LOG.info("🔧 מתקין deepl-python מגיט...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "git+https://github.com/DeepLcom/deepl-python.git"],
            check=False
        )
        import deepl
        ok = True
    except Exception as e:
        LOG.error(f"❌ כשל בהתקנת deepl-python: {e}")
        ok = False
    return ok

# -----------------------------
# FFmpeg
# -----------------------------
def ensure_ffmpeg() -> Optional[str]:
    """
    בדוק זמינות ffmpeg מערכתית, אם אין – הורד/אתר בינארי דרך imageio-ffmpeg.
    החזר נתיב מלא לבינארי.
    """
    global FFMPEG_BIN
    # נסה מערכתית
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        FFMPEG_BIN = "ffmpeg"
        LOG.info("✅ FFmpeg זמין במערכת.")
        return FFMPEG_BIN
    except Exception:
        LOG.info("ℹ️ FFmpeg מערכתית לא נמצאה. מנסה להביא בינארי נייד...")

    # דרך imageio-ffmpeg
    try:
        import imageio_ffmpeg
        ffbin = imageio_ffmpeg.get_ffmpeg_exe()
        if ffbin:
            FFMPEG_BIN = ffbin
            LOG.info("✅ FFmpeg נייד אותר/הורד בהצלחה.")
            return FFMPEG_BIN
    except Exception as e:
        LOG.error(f"❌ לא הצלחתי להשיג FFmpeg נייד: {e}")
        return None
    return None

def ffmpeg_exec(args: List[str]) -> Tuple[int, str, str]:
    """
    הרצת FFmpeg עם הבינארי המאותר.
    """
    if not FFMPEG_BIN:
        raise RuntimeError("FFmpeg לא אותר. אי אפשר להמשיך.")
    cmd = [FFMPEG_BIN] + args
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return p.returncode, p.stdout.decode("utf-8", "ignore"), p.stderr.decode("utf-8", "ignore")

def ffprobe_get_video_size(video_path: str) -> Tuple[Optional[int], Optional[int]]:
    """
    מחזיר (width, height) באמצעות ffprobe.
    """
    if not FFMPEG_BIN:
        return None, None
    ffprobe = FFMPEG_BIN.replace("ffmpeg", "ffprobe")
    # אם אין ffprobe לצד ffmpeg הנייד, ננסה "ffprobe" מערכתית
    if not Path(ffprobe).exists():
        ffprobe = "ffprobe"
    try:
        p = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", video_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
        )
        data = json.loads(p.stdout.decode("utf-8", "ignore") or "{}")
        streams = data.get("streams") or []
        if streams:
            return streams[0].get("width"), streams[0].get("height")
    except Exception:
        pass
    return None, None

# -----------------------------
# עיבוד מדיה
# -----------------------------
def extract_audio_16k_mono(video_path: str, wav_out: str) -> None:
    """
    חילוץ אודיו ל-WAV מונו 16k עם דנויז מהיר ושיפור איכות לפני תעתוק.
    """
    # בדיקה שהקובץ קיים
    if not os.path.exists(video_path):
        raise RuntimeError(f"Video file not found: {video_path}")
        
    args = [
        "-y",                    # תמיד דרוס אם קיים
        "-i", video_path,        # קובץ כניסה
        "-vn",                   # ללא וידאו
        "-ac", "1",              # מונו
        "-ar", "16000",          # דגימה 16KHz
        "-af", "afftdn=nr=12,compand=0.3|0.3:1|1:-90/-60|-60/-40|-40/-30|-20/-20:6:0:-90:0.2", # דנויז + נרמול דינמי
        "-sample_fmt", "s16",    # פורמט בייטים
        "-threads", "2",         # מספר תהליכים לעיבוד
        "-hide_banner",          # הסתרת באנר
        "-loglevel", "error",    # לוגים רק בשגיאה
        wav_out
    ]
    code, _, err = ffmpeg_exec(args)
    if code != 0:
        raise RuntimeError(f"ffmpeg extract audio failed: {err[-500:]}")
    
    # בדיקה שהקובץ נוצר
    if not os.path.exists(wav_out):
        raise RuntimeError("Audio extraction failed - output file not created")

# מערכת משופרת לתעתוק קול עם תמיכה במודלים מרובים
class SpeechRecognitionSystem:
    """
    מערכת מרכזית לתעתוק קול עם תמיכה במודלים שונים ובחירה דינמית לפי הצורך.
    תומכת במטמון מודלים, בחירה אוטומטית בהתאם למשאבי מערכת, וניהול אופציות.
    """
    
    # סוגי מודלים נתמכים
    MODEL_FASTER_WHISPER = "faster-whisper"
    MODEL_WHISPER = "whisper"
    
    # גדלי מודלים נתמכים - רק Whisper הרגיל
    MODEL_SIZES = {
        MODEL_WHISPER: ["tiny", "base", "small", "medium", "large"]
    }
    
    def __init__(self):
        # הגדרות מודלים
        self.models = {}  # מטמון מודלים
        self.lock = threading.Lock()
        self.default_model_type = self.MODEL_WHISPER
        self.default_model_size = "large"  # מודל ברירת המחדל - הגדול והארוך
        
        # הגדרת הגדלים המומלצים לפי זיכרון מערכת
        self.recommended_size = self._get_recommended_model_size()
        
    def _get_recommended_model_size(self) -> str:
        """קביעת גודל מודל מומלץ לפי זיכרון מערכת"""
        try:
            mem = psutil.virtual_memory()
            total_gb = mem.total / (1024 * 1024 * 1024)
            
            # הגדרת גודל מודל לפי זיכרון - רק Whisper הרגיל
            if total_gb >= 16:
                return "large"  # מומלץ למחשבים עם 16GB+ - המודל הגדול והארוך
            elif total_gb >= 8:
                return "medium"     # 8-16GB
            elif total_gb >= 4:
                return "small"      # 4-8GB
            else:
                return "base"       # <4GB
        except Exception:
            return "large"  # ברירת מחדל - המודל הגדול והארוך
    
    def _load_whisper_model(self, model_size: str = None) -> Any:
        """טעינת מודל openai-whisper - המודל הגדול והארוך"""
        if not model_size:
            model_size = self.default_model_size
            
        try:
            import whisper
            LOG.info(f"טוען מודל whisper {model_size} (המודל הגדול והארוך)...")
            model = whisper.load_model(model_size)
            return model
        except Exception as e:
            LOG.error(f"שגיאה בטעינת מודל whisper: {e}")
            raise
    
    def _load_whisper_model(self, model_size: str = None) -> Any:
        """טעינת מודל openai-whisper"""
        if not model_size:
            model_size = "small" if self.default_model_size in ("large-v2", "large-v3") else self.default_model_size
            
        try:
            import whisper
            LOG.info(f"טוען מודל whisper {model_size}...")
            model = whisper.load_model(model_size)
            return model
        except Exception as e:
            LOG.error(f"שגיאה בטעינת מודל whisper: {e}")
            raise
            
    def get_model(self, model_type: str = None, model_size: str = None) -> Tuple[Any, str, str]:
        """
        קבלת מודל לתעתוק. אם קיים במטמון - מחזיר אותו, אחרת טוען מודל חדש.
        מחזיר: (מודל, סוג_מודל, גודל_מודל)
        """
        model_type = model_type or self.default_model_type
        
        # קביעת ברירת מחדל לגודל המודל
        if not model_size:
            model_size = self.default_model_size
                
        # מפתח ייחודי למודל
        model_key = f"{model_type}_{model_size}"
        
        # בדיקה האם המודל כבר במטמון
        with self.lock:
            if model_key in self.models:
                return self.models[model_key], model_type, model_size
                
            # טעינת מודל חדש - רק Whisper הרגיל
            model = self._load_whisper_model(model_size)
            self.models[model_key] = model
            return model, model_type, model_size
    
    def transcribe(self, wav_path: str, preferred_model_type: str = None, preferred_model_size: str = None) -> Tuple[List[Dict], Optional[str]]:
        """
        תעתוק קובץ אודיו לטקסט עם Whisper הרגיל (הגדול והארוך).
        מחזיר: (רשימת מקטעים, שפה מזוהה)
        """
        if not os.path.exists(wav_path):
            LOG.error(f"קובץ אודיו לא נמצא: {wav_path}")
            return [], None
            
        # תעתוק עם Whisper הרגיל
        try:
            model, model_type, model_size = self.get_model(preferred_model_type, preferred_model_size)
            return self._transcribe_with_whisper(model, wav_path)
                
        except Exception as e:
            LOG.error(f"שגיאה בתעתוק עם Whisper: {e}")
            return [], None
        
    def _transcribe_with_whisper(self, model, wav_path: str) -> Tuple[List[Dict], Optional[str]]:
        """תעתוק בעזרת whisper רגיל"""
        res = model.transcribe(wav_path, fp16=False)
        lang = res.get("language")
        
        # המרה לפורמט אחיד
        out = []
        for s in res.get("segments", []):
            out.append({"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()})
        return out, lang

# יצירת המערכת לתעתוק
SPEECH_SYSTEM = SpeechRecognitionSystem()

def stt_whisper(wav_path: str) -> Tuple[List[Dict], Optional[str]]:
    """
    זיהוי דיבור עם Whisper הרגיל (הגדול והארוך)
    """
    return SPEECH_SYSTEM.transcribe(wav_path, preferred_model_type=SpeechRecognitionSystem.MODEL_WHISPER)

# מטמון גלובלי לתרגומים עם TTL ושמירה לדיסק
translation_cache: Dict[str, Dict] = {}
CACHE_TTL = 7 * 24 * 3600  # 7 ימים
cache_lock = threading.Lock()
CACHE_FILE = Path(APP_DIR) / "translations_cache.json"

def load_translation_cache():
    """טעינת מטמון התרגומים מקובץ"""
    global translation_cache
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                loaded_cache = json.load(f)
                # סינון ערכים שפג תוקפם לפני טעינה
                current_time = time.time()
                translation_cache = {
                    k: v for k, v in loaded_cache.items() 
                    if current_time - v.get('timestamp', 0) < CACHE_TTL
                }
                LOG.info(f"✅ נטענו {len(translation_cache)} תרגומים מהמטמון")
    except Exception as e:
        LOG.warning(f"⚠️ שגיאה בטעינת מטמון התרגומים: {e}")
        translation_cache = {}

def save_translation_cache():
    """שמירת מטמון התרגומים לקובץ"""
    try:
        # ניקוי המטמון לפני שמירה
        cleanup_expired_cache()
        with cache_lock:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(translation_cache, f, ensure_ascii=False)
        LOG.info(f"✅ נשמרו {len(translation_cache)} תרגומים במטמון")
    except Exception as e:
        LOG.warning(f"⚠️ שגיאה בשמירת מטמון התרגומים: {e}")

def get_cache_key(text: str, dest_lang: str) -> str:
    """יצירת מפתח cache ייחודי לכל תרגום"""
    content = f"{text}:{dest_lang}"
    return hashlib.md5(content.encode()).hexdigest()

def get_cached_translation(text: str, dest_lang: str) -> Optional[str]:
    """קבלת תרגום ממטמון אם קיים ולא פג תוקף"""
    with cache_lock:
        cache_key = get_cache_key(text, dest_lang)
        cached = translation_cache.get(cache_key)
        if cached and time.time() - cached['timestamp'] < CACHE_TTL:
            return cached['translation']
        return None

def cache_translation(text: str, dest_lang: str, translation: str):
    """שמירת תרגום במטמון"""
    with cache_lock:
        cache_key = get_cache_key(text, dest_lang)
        translation_cache[cache_key] = {
            'translation': translation,
            'timestamp': time.time()
        }
    
    # שמירה לדיסק כל 50 תרגומים חדשים
    if len(translation_cache) % 50 == 0:
        save_translation_cache()

def cleanup_expired_cache():
    """ניקוי תרגומים שפג תוקפם"""
    with cache_lock:
        current_time = time.time()
        expired_keys = [
            key for key, value in translation_cache.items()
            if current_time - value['timestamp'] >= CACHE_TTL
        ]
        for key in expired_keys:
            del translation_cache[key]
        return len(expired_keys)

# שיפור פונקציית התרגום הראשית
def translate_text(segments: List[Dict], dest_lang: str) -> List[Dict]:
    """
    תרגום משופר עם Google Translate ומטמון חכם
    כולל טיפול שגיאות אחיד, מנגנון ניסיונות חוזרים, ומטמון פרסיסטנטי
    """
    if not segments:
        LOG.warning("No segments to translate")
        return []
    
    # טעינת מטמון בהפעלה ראשונה אם לא נטען עדיין
    if not translation_cache and CACHE_FILE.exists():
        load_translation_cache()
    
    out = []
    num_cache_hits = 0
    num_new_translations = 0
    
    # הכנת Google Translator
    try:
        from googletrans import Translator
        translator = Translator(service_urls=[
            'translate.google.com',
            'translate.google.co.il',
        ])
    except Exception as e:
        LOG.error(f"Failed to initialize Google Translate: {e}")
        return segments

    # טיפול באצווה - חלוקה לקבוצות של 10 תרגומים מקסימום
    batch_size = 10
    segments_batch = []
    
    for i, seg in enumerate(segments):
        text = seg["text"]
        if not text.strip():
            out.append(seg)
            continue
            
        # בדיקה במטמון קודם
        cached_translation = get_cached_translation(text, dest_lang)
        if cached_translation:
            out.append({**seg, "text": cached_translation})
            num_cache_hits += 1
            continue
        
        # מוסיפים לאצווה לתרגום
        segments_batch.append(seg)
        
        # מתרגמים כשהאצווה מלאה או בסיום
        if len(segments_batch) >= batch_size or i == len(segments) - 1:
            if segments_batch:
                # תרגום אצווה עם ניסיונות חוזרים
                for retry in range(3):
                    try:
                        batch_texts = [s["text"] for s in segments_batch]
                        if len(batch_texts) == 1:
                            # תרגום בודד
                            result = translator.translate(batch_texts[0], dest=dest_lang)
                            translations = [result.text] if result and hasattr(result, 'text') else [""]
                        else:
                            # תרגום אצווה - הערה: זו יכולה להיות פונקציה שלא נתמכת בגרסאות מסוימות
                            results = translator.translate(batch_texts, dest=dest_lang)
                            translations = [r.text for r in results] if results else [""] * len(batch_texts)
                        
                        # שומרים את התרגומים במטמון ומוסיפים לפלט
                        for j, s in enumerate(segments_batch):
                            if j < len(translations) and translations[j]:
                                translated_text = translations[j]
                                if dest_lang.lower() == 'he':
                                    translated_text = bidi.get_display(translated_text)
                                cache_translation(s["text"], dest_lang, translated_text)
                                out.append({**s, "text": translated_text})
                                num_new_translations += 1
                            else:
                                out.append(s)  # במקרה של כישלון - משאירים את הטקסט המקורי
                        
                        # ניקוי האצווה אחרי עיבוד
                        segments_batch = []
                        break  # יציאה מלולאת הניסיונות אם הצליח
                        
                    except Exception as e:
                        LOG.warning(f"Batch translation attempt {retry + 1} failed: {e}")
                        if retry < 2:
                            time.sleep(1 * (retry + 1))  # המתנה ארוכה יותר בין ניסיונות
                        else:
                            # כשל סופי - החזרת הטקסטים המקוריים
                            LOG.error(f"Failed to translate batch after 3 attempts")
                            for s in segments_batch:
                                out.append(s)
                            segments_batch = []
    
    # שמירת המטמון בסיום
    if num_new_translations > 0:
        save_translation_cache()
    
    LOG.info(f"Translation complete: {num_cache_hits} cache hits, {num_new_translations} new translations")
    return out

def srt_timestamp(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def write_srt(segments: List[Dict], srt_path: str) -> None:
    # בדיקה שיש תוכן לכתיבה
    if not segments:
        raise RuntimeError("No segments to write to SRT file")
        
    try:
        with open(srt_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments, 1):
                f.write(f"{i}\n")
                f.write(f"{srt_timestamp(seg['start'])} --> {srt_timestamp(seg['end'])}\n")
                f.write(seg["text"].strip() + "\n\n")
    except Exception as e:
        raise RuntimeError(f"Failed to write SRT file: {e}")

def burn_subs_from_srt(
    input_video: str,
    srt_path: str,
    output_video: str,
    subtitle_config: Optional[SubtitleConfig] = None
) -> None:
    """
    צריבת כתוביות ישירות מקובץ SRT בצורה עמידה ל-Windows:
    • מעתיק את הווידאו וה-SRT לשמות פשוטים בתיקיית העבודה (ללא אות כונן/נקודתיים).
    • מריץ ffmpeg מתוך התיקייה עם נתיבים יחסיים כדי למנוע פירוש שגוי של 'C:'.
    • אופטימיזציה משופרת לקידוד יעיל בגודל קובץ טוב יותר ואיכות גבוהה יותר.
    • תומך בהגדרות מתקדמות לסגנון כתוביות דרך אובייקט SubtitleConfig.
    """
    # בדיקות קלט
    if not os.path.exists(input_video):
        raise RuntimeError(f"Input video not found: {input_video}")
    if not os.path.exists(srt_path):
        raise RuntimeError(f"SRT file not found: {srt_path}")
    if not os.path.exists(APP_DIR):
        raise RuntimeError(f"App directory not found: {APP_DIR}")
        
    # בדיקת רזולוציית הווידאו לקביעת הגדרות מיטביות
    width, height = ffprobe_get_video_size(input_video)
    is_hd = width is not None and width >= 1280
    
    # קבצים זמניים עם שמות פשוטים
    simple_id = uuid.uuid4().hex[:8]
    work_dir = APP_DIR
    v_name = f"in_{simple_id}.mp4"
    srt_name = f"subs_{simple_id}.srt"
    out_name = f"out_{simple_id}.mp4"

    # העתקה לשמות פשוטים
    shutil.copyfile(input_video, str(work_dir / v_name))
    shutil.copyfile(srt_path, str(work_dir / srt_name))

    # יצירת פילטר עם force_style - או שימוש בהגדרות ברירת מחדל
    if subtitle_config:
        style = subtitle_config.get_ass_style()
    else:
        # הגדרות ברירת מחדל למקרה שלא סופק אובייקט הגדרות
        style = "FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=1,Shadow=1,Alignment=2"
    
    vf = f"subtitles=filename='{srt_name}':force_style='{style}'"

    # פרמטרים מיטביים לקידוד יעיל
    # אם הסרטון HD - נשתמש בהגדרות איכות טובות יותר
    if is_hd:
        crf = "23"  # איכות טובה יותר
        preset = "medium"  # איזון בין מהירות לאיכות
        tune = "film"  # אופטימיזציה לסרטונים
        maxrate = "2M"  # מגבלת bitrate
    else:
        crf = "26"  # דחיסה חזקה יותר
        preset = "faster"  # מהיר יותר
        tune = "fastdecode"  # פענוח מהיר יותר
        maxrate = "1M"  # מגבלת bitrate נמוכה יותר

    cwd = os.getcwd()
    try:
        os.chdir(str(work_dir))
        
        # הוספת פרמטרים מתקדמים עבור קידוד יעיל יותר
        advanced_args = [
            "-y",                    # תמיד דורס
            "-i", v_name,            # קובץ כניסה
            "-vf", vf,               # פילטר לכתוביות
            "-c:v", "libx264",       # קידוד וידאו H.264
            "-preset", preset,       # מהירות קידוד vs איכות
            "-tune", tune,           # אופטימיזציה ספציפית
            "-crf", crf,             # איכות קבועה
            "-maxrate", maxrate,     # מגבלת bitrate
            "-bufsize", "2M",        # גודל buffer
            "-profile:v", "main",    # פרופיל תואם
            "-level", "4.0",         # רמת תאימות
            "-pix_fmt", "yuv420p",   # פורמט פיקסלים סטנדרטי
            "-movflags", "+faststart", # אופטימיזציה לסטרימינג
            "-c:a", "aac",           # קידוד אודיו AAC (יותר תואם מ-copy)
            "-b:a", "128k",          # ביטרייט אודיו
            "-ac", "2",              # שני ערוצי אודיו (סטריאו)
            "-threads", str(min(4, max(2, multiprocessing.cpu_count() // 2))), # מספר תהליכים אופטימלי
            out_name
        ]
        
        code, _, err = ffmpeg_exec(advanced_args)
        
        if code != 0:
            # ניסיון נוסף עם הגדרות פשוטות יותר אם יש בעיה
            LOG.warning("Advanced encoding failed, trying simpler parameters")
            code2, _, err2 = ffmpeg_exec([
                "-y", "-i", v_name, 
                "-vf", f"subtitles=filename='{srt_name}'", 
                "-preset", "veryfast", 
                "-c:v", "libx264", 
                "-c:a", "copy", 
                out_name
            ])
            if code2 != 0 or not (work_dir / out_name).exists():
                raise RuntimeError(f"ffmpeg burn_subs_from_srt failed: {(err2 or err)[-500:]}")
        
        # בדיקה שהקובץ נוצר וגודלו סביר
        if not os.path.exists(str(work_dir / out_name)):
            raise RuntimeError("Output file was not created")
            
        out_size = os.path.getsize(str(work_dir / out_name))
        if out_size < 10 * 1024:  # פחות מ-10KB
            raise RuntimeError("Output file is too small, encoding probably failed")
            
        shutil.copyfile(str(work_dir / out_name), output_video)
    finally:
        os.chdir(cwd)
        # ניקוי קבצים זמניים
        cleanup_paths([str(work_dir / v_name), str(work_dir / srt_name), str(work_dir / out_name)])


def convert_srt_to_ass(srt_path: str, ass_path: str) -> None:
    """
    המרה דרך ffmpeg כדי שנוכל להחיל force_style.
    """
    code, _, err = ffmpeg_exec(["-y", "-i", srt_path, ass_path])
    if code != 0 or (not Path(ass_path).exists()):
        raise RuntimeError(f"ffmpeg convert srt->ass failed: {err[-500:]}")

def burn_subs(
    input_video: str,
    ass_path: str,
    output_video: str,
    font_size: int = 16,
    primary_colour: str = "&H00FFFFFF"
) -> None:
    """
    צריבת כתוביות עם force_style. שמירה על אודיו כשאפשר.
    """
    style = f"FontSize={font_size},PrimaryColour={primary_colour},OutlineColour=&H00000000,BorderStyle=1,Outline=1,Shadow=1,Alignment=2"
    vf = f"subtitles='{ass_path}':force_style='{style}'"
    args = ["-y", "-i", input_video, "-vf", vf, "-preset", "veryfast", "-c:v", "libx264", "-c:a", "copy", output_video]
    code, _, err = ffmpeg_exec(args)
    if code != 0:
        raise RuntimeError(f"ffmpeg burn_subs failed: {err[-500:]}")

def overlay_logo(
    input_video: str,
    logo_png: str,
    output_video: str,
    position: str = "TR",
    opacity_percent: int = 70,
    scale_ratio: float = 0.2
) -> None:
    """
    הטמעת לוגו עם שקיפות ומיקום. ה-logo יוקטן לגובה יחסי (ברירת מחדל 20%).
    מיטוב קידוד הווידאו עבור תוצאה איכותית ובגודל קובץ אופטימלי.
    """
    # בדיקות קלט
    if not os.path.exists(input_video):
        raise RuntimeError(f"Input video not found: {input_video}")
    if not os.path.exists(logo_png):
        raise RuntimeError(f"Logo file not found: {logo_png}")
    if not os.path.exists(APP_DIR):
        raise RuntimeError(f"App directory not found: {APP_DIR}")
        
    from PIL import Image

    # קובע גודל וידאו
    w, h = ffprobe_get_video_size(input_video)
    if not h:
        h = 720  # ברירת מחדל
    
    # בדיקה אם HD ליצירת הגדרות קידוד אופטימליות
    is_hd = w is not None and w >= 1280
    
    target_h = max(16, int(h * float(scale_ratio)))

    # מייצר לוגו מתאים לגובה הווידאו (שמירה על יחסי רוחב-גובה)
    with Image.open(logo_png) as im:
        ratio = target_h / float(im.height)
        target_w = max(16, int(im.width * ratio))
        
        # מיטוב: אם הלוגו גדול מדי, נקטין אותו למידות סבירות
        if target_w > w * 0.5:
            ratio = (w * 0.5) / float(im.width)
            target_w = max(16, int(im.width * ratio))
            target_h = max(16, int(im.height * ratio))
            
        im = im.convert("RGBA").resize((target_w, target_h), Image.LANCZOS)
        
        # אופטימיזציה של תמונת הלוגו - טיפול בשקיפות אם יש
        if opacity_percent < 100:
            # ניישם את השקיפות ישירות על תמונת הלוגו לפני ההטמעה
            if im.mode == 'RGBA':
                alpha = im.getchannel('A')
                alpha = Image.eval(alpha, lambda a: int(a * opacity_percent / 100))
                im.putalpha(alpha)
                
        tmp_logo = str(APP_DIR / f"logo_resized_{uuid.uuid4().hex}.png")
        im.save(tmp_logo, "PNG", optimize=True)

    # מיקום overlay - כולל המיקום החדש MC (מרכז)
    positions = {
        "TL": "10:10",
        "TC": "(main_w-overlay_w)/2:10",
        "TR": "main_w-overlay_w-10:10",
        "ML": "10:(main_h-overlay_h)/2",
        "MR": "main_w-overlay_w-10:(main_h-overlay_h)/2",
        "BL": "10:main_h-overlay_h-10",
        "BC": "(main_w-overlay_w)/2:main_h-overlay_h-10",
        "BR": "main_w-overlay_w-10:main_h-overlay_h-10",
        "MC": "(main_w-overlay_w)/2:(main_h-overlay_h)/2",  # מרכז הסרטון
    }
    xy = positions.get(position, "main_w-overlay_w-10:10")
    opacity = 1.0  # כבר טיפלנו בשקיפות בתמונה עצמה

    # הגדרות איכות לפי סוג הווידאו
    if is_hd:
        preset = "medium"  # איזון בין מהירות לאיכות
        crf = "23"  # איכות טובה
        maxrate = "2M"
    else:
        preset = "faster"
        crf = "26"  # דחיסה חזקה יותר
        maxrate = "1M"

    # פילטר מורכב להטמעת לוגו
    filter_complex = f"[1:v]format=rgba[logo];[0:v][logo]overlay={xy}"
    
    # הגדרות מתקדמות לקידוד איכותי ויעיל
    args = [
        "-y",                      # תמיד דורס
        "-i", input_video,         # קובץ וידאו מקור
        "-i", tmp_logo,            # תמונת לוגו
        "-filter_complex", filter_complex,  # פילטר הטמעה
        "-preset", preset,         # הגדרת איזון מהירות/איכות
        "-crf", crf,               # איכות קבועה
        "-maxrate", maxrate,       # מגבלת bitrate
        "-bufsize", "2M",          # גודל buffer
        "-profile:v", "main",      # פרופיל תואמות
        "-level", "4.0",           # רמת תאימות
        "-pix_fmt", "yuv420p",     # פורמט פיקסלים סטנדרטי
        "-movflags", "+faststart", # אופטימיזציה לסטרימינג
        "-c:a", "aac",             # קידוד אודיו מתקדם
        "-b:a", "128k",            # ביטרייט אודיו
        "-ac", "2",                # סטריאו
        "-threads", str(min(4, max(2, multiprocessing.cpu_count() // 2))),  # מספר תהליכים אופטימלי
        output_video
    ]
    
    # הרצת הקידוד
    code, _, err = ffmpeg_exec(args)
    
    # נסיון שני עם פרמטרים בסיסיים אם נכשל
    if code != 0:
        LOG.warning("Advanced logo overlay encoding failed, trying simpler parameters")
        filter_complex = (
            f"[1]format=rgba,colorchannelmixer=aa={opacity_percent / 100.0}[wm];"
            f"[0][wm]overlay={xy}"
        )
        simple_args = [
            "-y", "-i", input_video, "-i", tmp_logo, 
            "-filter_complex", filter_complex, 
            "-preset", "veryfast", 
            "-c:v", "libx264", 
            "-c:a", "copy", 
            output_video
        ]
        code, _, err = ffmpeg_exec(simple_args)
    
    # ניקוי קבצים זמניים
    try:
        os.remove(tmp_logo)
    except Exception:
        pass
        
    if code != 0:
        raise RuntimeError(f"ffmpeg overlay_logo failed: {err[-500:]}")
        
    # בדיקה שהקובץ נוצר ותקין
    if not os.path.exists(output_video) or os.path.getsize(output_video) < 10 * 1024:
        raise RuntimeError("Logo overlay failed - output file not created or too small")

# מנהל קבצים זמניים עם שיפור ניהול זיכרון
class TempFileManager:
    """
    מנהל קבצים זמניים חכם.
    מנהל את הקבצים הזמניים ומוודא שהם נמחקים גם במקרה של קריסה.
    מבצע ניקוי אוטומטי של קבצים ישנים.
    """
    def __init__(self, base_dir: Path = APP_DIR, max_age_hours: int = 24):
        self.base_dir = base_dir
        self.max_age_hours = max_age_hours
        self.active_files = set()
        self.cleanup_lock = threading.Lock()
        
    def register_file(self, path: str) -> str:
        """רישום קובץ זמני ושמירת הנתיב"""
        with self.cleanup_lock:
            path = str(path)
            self.active_files.add(path)
            return path
            
    def create_temp_file(self, prefix: str, suffix: str) -> str:
        """יצירת קובץ זמני חדש עם תחילית וסיומת"""
        path = str(self.base_dir / f"{prefix}_{uuid.uuid4().hex}{suffix}")
        return self.register_file(path)
            
    def cleanup_file(self, path: str) -> bool:
        """ניקוי קובץ זמני אחד"""
        with self.cleanup_lock:
            if not path:
                return False
                
            try:
                if os.path.exists(path):
                    os.remove(path)
                if path in self.active_files:
                    self.active_files.remove(path)
                return True
            except Exception as e:
                LOG.warning(f"Failed to clean temporary file {path}: {e}")
                return False
                
    def cleanup_files(self, paths: List[str]) -> int:
        """ניקוי מספר קבצים זמניים"""
        count = 0
        for path in paths:
            if self.cleanup_file(path):
                count += 1
        return count
        
    def cleanup_old_files(self) -> int:
        """ניקוי קבצים ישנים מתיקיית העבודה"""
        with self.cleanup_lock:
            try:
                count = 0
                now = time.time()
                max_age_seconds = self.max_age_hours * 3600
                
                # חיפוש קבצים זמניים עם תבניות מוכרות
                patterns = [
                    "in_*.mp4", "out_*.mp4", "logo_*.png", "audio_*.wav",
                    "subs_*.srt", "subs_*.ass", "logo_resized_*.png"
                ]
                
                for pattern in patterns:
                    for file_path in self.base_dir.glob(pattern):
                        try:
                            # בדיקה אם הקובץ ישן מספיק למחיקה
                            if file_path.is_file():
                                file_age = now - file_path.stat().st_mtime
                                if file_age > max_age_seconds and str(file_path) not in self.active_files:
                                    try:
                                        os.remove(str(file_path))
                                        count += 1
                                    except Exception:
                                        pass
                        except Exception:
                            continue
                            
                return count
            except Exception as e:
                LOG.warning(f"Error in cleanup_old_files: {e}")
                return 0

    def clear_memory(self, force_gc: bool = False) -> None:
        """ניקוי זיכרון יזום במקרה של צורך"""
        try:
            # קריאה ל-garbage collector אם צריך
            if force_gc:
                import gc
                gc.collect()
                
            # פרגמנטציה של הזיכרון בפייתון עלולה להשאיר "חורים"
            # אין פתרון מושלם, אבל השיטה הבאה יכולה לשחרר חלק מהזיכרון
            if psutil.virtual_memory().percent > 80:  # אם יותר מ-80% זיכרון בשימוש
                import gc
                gc.collect()  # איסוף אשפה אגרסיבי
                time.sleep(0.5)  # קצת זמן לפעולת הניקוי
        except Exception as e:
            LOG.warning(f"Memory clearing error: {e}")
            
    def __del__(self):
        """פעולות לביצוע בעת מחיקת האובייקט"""
        try:
            for file in list(self.active_files):
                self.cleanup_file(file)
        except Exception:
            pass

# יצירת המנהל הגלובלי
TEMP_MANAGER = TempFileManager()

# רישום פונקציית ניקוי לסגירת התהליך
@atexit.register
def cleanup_temp_files_at_exit():
    """ניקוי קבצים זמניים בסגירת התהליך"""
    try:
        if TEMP_MANAGER:
            active_files = list(TEMP_MANAGER.active_files)
            LOG.info(f"Cleaning {len(active_files)} temp files at exit")
            TEMP_MANAGER.cleanup_files(active_files)
    except Exception as e:
        LOG.error(f"Error cleaning temp files at exit: {e}")

def cleanup_paths(paths: List[str]):
    """
    פונקציה לניקוי נתיבים - מתווכת למנהל הקבצים הזמניים
    לשמירה על תאימות קוד קיים
    """
    return TEMP_MANAGER.cleanup_files(paths)

# -----------------------------
# Telegram Bot (python-telegram-bot==13.7)
# -----------------------------
if not ensure_dependencies():
    raise SystemExit(1)

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
)
from telegram.error import Unauthorized, BadRequest

# ------------- מצבים ונתוני משתמש -------------
USER_STATE: Dict[int, Dict] = {}
ACTIVE_JOBS: Dict[int, int] = {}  # מספר עבודות במקביל לכל משתמש (מוגבל ל-2)

def get_user_state(uid: int) -> Dict:
    st = USER_STATE.get(uid)
    if not st:
        st = {
            # הגדרות ממשק
            "ui_lang": "he",
            "target_lang": "he",  # שינוי ברירת מחדל לעברית
            
            # הגדרות כתוביות
            "font_size": 16,
            "font_color": "white",
            "subtitle_position": "bottom",  # מיקום כתוביות - ברירת מחדל
            "font_name": "arial",           # סוג גופן
            "outline_size": 1,              # גודל מתאר
            "shadow_size": 1,               # גודל צל
            "bold": False,                  # הדגשה
            "italic": False,                # הטיה
            "background_color": "black",    # צבע רקע/מתאר
            
            # מצבים פעילים
            "expecting_video_for_subs": False,
            "expecting_logo_image": False,
            "logo_path": None,
            "logo_position": "TR",
            "logo_opacity": 70,
            "logo_size_percent": 20,
            "expecting_video_for_logo": False,
            
            # הגדרות מתקדמות נוספות
            "advanced_subtitle_mode": False,  # האם במצב הגדרות מתקדמות לכתוביות
            "export_srt": False,              # האם לייצא קובץ SRT נפרד
            "export_quality": "medium",       # איכות ייצוא
        }
        USER_STATE[uid] = st
        LOG.info(f"👤 משתמש חדש {uid} - שפה ברירת מחדל: {st['target_lang']}")
    return st

def is_process_active(uid: int) -> bool:
    """
    בדיקה אם יש תהליך פעיל למשתמש
    """
    st = get_user_state(uid)
    return (st.get("expecting_video_for_subs") or 
            st.get("expecting_logo_image") or 
            st.get("expecting_video_for_logo") or
            ACTIVE_JOBS.get(uid, 0) > 0)

def is_logo_process_active(uid: int) -> bool:
    """
    בדיקה אם יש תהליך הטמעת לוגו פעיל
    """
    st = get_user_state(uid)
    return (st.get("expecting_logo_image") or 
            st.get("expecting_video_for_logo"))

def is_translation_process_active(uid: int) -> bool:
    """
    בדיקה אם יש תהליך תרגום פעיל
    """
    st = get_user_state(uid)
    return st.get("expecting_video_for_subs") or ACTIVE_JOBS.get(uid, 0) > 0

def inc_jobs(uid: int) -> bool:
    n = ACTIVE_JOBS.get(uid, 0)
    if n >= 2:
        return False
    ACTIVE_JOBS[uid] = n + 1
    return True

def dec_jobs(uid: int):
    n = ACTIVE_JOBS.get(uid, 0)
    ACTIVE_JOBS[uid] = max(0, n - 1)

# ------------- UI -------------

def safe_edit(query, text: str, reply_markup=None):
    """
    מבצע edit_message_text בבטחה: בולע רק את 'Message is not modified'.
    לא קורא ל-query.answer כאן (נעשה בתחילת cb_handler).
    """
    try:
        query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        else:
            raise

    # עונה לקולבק כדי למנוע 'spinner' אינסופי
    try:
        query.answer(cache_time=0)
    except Exception:
        pass


    """
    מגן על edit_message_text מפני השגיאה 'Message is not modified' ע"י בליעה שקטה.
    """
    try:
        safe_edit(query, text, reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            try:
                # לא לשנות את ההודעה שוב – רק לענות בשקט כדי לסגור את ה-Spinner.
                query.answer("אין שינוי", show_alert=False, cache_time=0)
            except Exception:
                pass
        else:
            raise

def main_menu_kb(uid: int, state: Dict) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(t(uid, "btn_ui_lang"), callback_data="choose_ui_lang")],
        [InlineKeyboardButton(t(uid, "btn_target_lang"), callback_data="choose_lang")],
        [InlineKeyboardButton(t(uid, "btn_font_size"), callback_data="choose_fontsize")],
        [InlineKeyboardButton(t(uid, "btn_font_color"), callback_data="choose_fontcolor")],
        
        # הגדרות כתוביות מתקדמות
        [InlineKeyboardButton("🔠 הגדרות כתוביות מתקדמות", callback_data="advanced_subtitle_settings")],
        
        [InlineKeyboardButton(t(uid, "btn_upload_video"), callback_data="upload_video")],
        [InlineKeyboardButton(t(uid, "btn_logo"), callback_data="logo_start")],
        [InlineKeyboardButton(t(uid, "btn_help"), callback_data="help")]
    ]
    return InlineKeyboardMarkup(kb)

def lang_menu(uid: int, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    start = page * per_page
    chunk = LANG_CHOICES[start:start+per_page]
    rows = []
    for name, code in chunk:
        rows.append([InlineKeyboardButton(f"{name} ({code})", callback_data=f"set_lang:{code}:{page}")])
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"lang_page:{page-1}"))
    if start + per_page < len(LANG_CHOICES):
        nav.append(InlineKeyboardButton("▶️", callback_data=f"lang_page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(t(uid, "btn_back_main"), callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def fontsize_menu(uid: int) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, sz in enumerate(FONT_SIZES, 1):
        row.append(InlineKeyboardButton(str(sz), callback_data=f"set_size:{sz}"))
        if i % 4 == 0:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(t(uid, "btn_back_main"), callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def fontcolor_menu(uid: int) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, (label, color) in enumerate(COLOR_CHOICES, 1):
        row.append(InlineKeyboardButton(label, callback_data=f"set_color:{color}"))
        if i % 4 == 0:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(t(uid, "btn_back_main"), callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def logo_pos_menu(uid: int) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, (label, pos_code) in enumerate(LOGO_POSITIONS, 1):
        row.append(InlineKeyboardButton(label, callback_data=f"logo_setpos:{pos_code}"))
        if i % 2 == 0:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(t(uid, "btn_back_main"), callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def logo_size_menu(uid: int) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, size in enumerate(LOGO_SIZE_CHOICES, 1):
        row.append(InlineKeyboardButton(f"{size}%", callback_data=f"logo_setsize:{size}"))
        if i % 4 == 0:  # 4 כפתורים בשורה
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(t(uid, "btn_back_main"), callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def logo_opacity_menu(uid: int) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, p in enumerate(OPACITY_CHOICES, 1):
        row.append(InlineKeyboardButton(f"{p}%", callback_data=f"logo_setopacity:{p}"))
        if i % 4 == 0:  # 4 כפתורים בשורה במקום 3
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(t(uid, "btn_back_main"), callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def ui_lang_menu(uid: int) -> InlineKeyboardMarkup:
    # מציג רק אנגלית ועברית כשפות ממשק זמינות
    rows = []
    ui_lang_choices = [("🇺🇸 English", "en"), ("🇮🇱 עברית", "he")]
    for name, code in ui_lang_choices:
        rows.append([InlineKeyboardButton(name, callback_data=f"set_ui_lang:{code}")])
    rows.append([InlineKeyboardButton(t(uid, "btn_back_main"), callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

# ------------- Handlers -------------
def start(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    st = get_user_state(uid)
    msg = t(uid, "start_message")
    update.message.reply_text(msg, reply_markup=main_menu_kb(uid, st))

def help_cmd(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    st = get_user_state(uid)
    update.message.reply_text(t(uid, "help_message"), reply_markup=main_menu_kb(uid, st))

def cb_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    uid = query.from_user.id
    st = get_user_state(uid)
    data = query.data or ""
    LOG.info(f"Callback data: {data}")
    # עונים פעם אחת בתחילת הטיפול כדי לסגור spinner
    try:
        query.answer(cache_time=0)
    except Exception:
        pass
    except Exception:
        pass

    try:
        if data == "choose_ui_lang":
            LOG.info("Action: choose_ui_lang")
            safe_edit(query, t(uid, "prompt_choose_ui_lang"), reply_markup=ui_lang_menu(uid))
        elif data.startswith("set_ui_lang:"):
            LOG.info("Action: set_ui_lang")
            _, code = data.split(":")
            if code in UI_STRINGS:
                st["ui_lang"] = code
            lang_name = next((name for name, lang_code in LANG_CHOICES if lang_code == st["ui_lang"]), st["ui_lang"])            
            safe_edit(query, t(uid, "ui_lang_set_to", lang_name=lang_name), reply_markup=main_menu_kb(uid, st))
        elif data == "choose_lang":
            LOG.info("Action: choose_lang")
            safe_edit(query, t(uid, "prompt_choose_target_lang"), reply_markup=lang_menu(uid, page=0))
        elif data.startswith("lang_page:"):
            LOG.info("Action: lang_page")
            page = int(data.split(":")[1])
            safe_edit(query, t(uid, "prompt_choose_target_lang"), reply_markup=lang_menu(uid, page=page))
        elif data.startswith("set_lang:"):
            LOG.info("Action: set_lang")
            _, code, page = data.split(":")
            st["target_lang"] = code
            LOG.info(f"🎯 שפה נקבעה ל-{code}")
            # מצא את שם השפה עם הדגל
            lang_name = next((name for name, lang_code in LANG_CHOICES if lang_code == code), code)
            safe_edit(query, t(uid, "target_lang_set_to", lang_name=lang_name), reply_markup=main_menu_kb(uid, st))
        elif data == "choose_fontsize":
            LOG.info("Action: choose_fontsize")
            safe_edit(query, t(uid, "prompt_choose_font_size"), reply_markup=fontsize_menu(uid))
        elif data.startswith("set_size:"):
            LOG.info("Action: set_size")
            size = int(data.split(":")[1])
            if size in FONT_SIZES:
                st["font_size"] = size
            safe_edit(query, t(uid, "font_size_set", size=st['font_size']), reply_markup=main_menu_kb(uid, st))
        elif data == "choose_fontcolor":
            LOG.info("Action: choose_fontcolor")
            safe_edit(query, t(uid, "prompt_choose_font_color"), reply_markup=fontcolor_menu(uid))
        elif data.startswith("set_color:"):
            LOG.info("Action: set_color")
            color = data.split(":")[1]
            if color in ASS_COLORS:
                st["font_color"] = color
            # מצא את שם הצבע עם האימוג'י
            color_name = next((label for label, color_code in COLOR_CHOICES if color_code == color), color)
            safe_edit(query, t(uid, "font_color_set", color_name=color_name), reply_markup=main_menu_kb(uid, st))
        elif data == "upload_video":
            LOG.info("Action: upload_video")
            # בדיקה אם יש תהליך הטמעת לוגו פעיל
            if is_logo_process_active(uid):
                safe_edit(query, t(uid, "error_logo_process_in_progress"), reply_markup=main_menu_kb(uid, st))
                return
            st["expecting_video_for_subs"] = True
            safe_edit(query, t(uid, "upload_video_prompt",
                settings_title=t(uid, "settings_current_title"),
                lang_label=t(uid, "settings_language"),
                size_label=t(uid, "settings_font_size"),
                color_label=t(uid, "settings_color"),
                lang=next((name for name, code in LANG_CHOICES if code == st.get("target_lang", "en")), st.get("target_lang", "en")),
                size=st.get("font_size", 16),
                color=next((label for label, color in COLOR_CHOICES if color == st.get("font_color", "white")), st.get("font_color", "white"))
            ), reply_markup=main_menu_kb(uid, st))
        elif data == "logo_start":
            LOG.info("Action: logo_start")
            # בדיקה אם יש תהליך תרגום פעיל
            if is_translation_process_active(uid):
                safe_edit(query, t(uid, "error_translation_process_in_progress"), reply_markup=main_menu_kb(uid, st))
                return
            st["expecting_logo_image"] = True
            st["logo_path"] = None
            st["expecting_video_for_logo"] = False
            # לא מחזירים לתפריט הראשי - מחכים להעלאת התמונה
            safe_edit(query, t(uid, "prompt_logo_start"), reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ חזרה לתפריט הראשי", callback_data="back_main")
            ]]))
        elif data == "advanced_subtitle_settings":
            LOG.info("Action: advanced_subtitle_settings")
            # מציג את תפריט הגדרות הכתוביות המתקדמות
            advanced_subtitle_menu = InlineKeyboardMarkup([
                [InlineKeyboardButton("📍 מיקום כתוביות", callback_data="choose_subtitle_position")],
                [InlineKeyboardButton("🔠 סוג גופן", callback_data="choose_font_type")],
                [InlineKeyboardButton("🖋️ עובי מתאר", callback_data="choose_outline_size")],
                [InlineKeyboardButton("👥 גודל צל", callback_data="choose_shadow_size")],
                [InlineKeyboardButton("🎭 סגנון טקסט", callback_data="choose_text_style")],
                [InlineKeyboardButton("🎨 צבע רקע", callback_data="choose_background_color")],
                [InlineKeyboardButton("⬅️ חזרה לתפריט הראשי", callback_data="back_main")]
            ])
            safe_edit(query, "הגדרות כתוביות מתקדמות:", reply_markup=advanced_subtitle_menu)
        elif data == "choose_subtitle_position":
            LOG.info("Action: choose_subtitle_position")
            # תפריט בחירת מיקום כתוביות
            position_menu = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔽 תחתית מסך (ברירת מחדל)", callback_data="set_position:bottom")],
                [InlineKeyboardButton("🔼 ראש המסך", callback_data="set_position:top")],
                [InlineKeyboardButton("↙️ פינה שמאלית תחתונה", callback_data="set_position:bottom-left")],
                [InlineKeyboardButton("↘️ פינה ימנית תחתונה", callback_data="set_position:bottom-right")],
                [InlineKeyboardButton("↖️ פינה שמאלית עליונה", callback_data="set_position:top-left")],
                [InlineKeyboardButton("↗️ פינה ימנית עליונה", callback_data="set_position:top-right")],
                [InlineKeyboardButton("⭐ מרכז המסך", callback_data="set_position:middle")],
                [InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")]
            ])
            safe_edit(query, "בחר מיקום לכתוביות:", reply_markup=position_menu)
        elif data.startswith("set_position:"):
            LOG.info("Action: set_position")
            position = data.split(":")[1]
            if position in SUBTITLE_POSITIONS:
                st["subtitle_position"] = position
                position_name = next((name for name, val in SUBTITLE_POSITIONS.items() if val == position), position)
                safe_edit(query, f"✅ מיקום הכתוביות נקבע ל: {position}", 
                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")]]))
        elif data == "choose_font_type":
            LOG.info("Action: choose_font_type")
            # תפריט בחירת סוג גופן
            font_menu_rows = []
            for i, (font_key, font_name) in enumerate(SUBTITLE_FONTS.items()):
                if i % 2 == 0:
                    font_menu_rows.append([])
                font_menu_rows[-1].append(InlineKeyboardButton(font_name, callback_data=f"set_font:{font_key}"))
            font_menu_rows.append([InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")])
            safe_edit(query, "בחר סוג גופן:", reply_markup=InlineKeyboardMarkup(font_menu_rows))
        elif data.startswith("set_font:"):
            LOG.info("Action: set_font")
            font_name = data.split(":")[1]
            if font_name in SUBTITLE_FONTS:
                st["font_name"] = font_name
                safe_edit(query, f"✅ סוג גופן נקבע: {SUBTITLE_FONTS[font_name]}",
                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")]]))
        elif data == "choose_text_style":
            LOG.info("Action: choose_text_style")
            # תפריט בחירת סגנון טקסט (רגיל/מודגש/נטוי)
            style_menu = InlineKeyboardMarkup([
                [InlineKeyboardButton("רגיל", callback_data="set_style:normal")],
                [InlineKeyboardButton("מודגש", callback_data="set_style:bold")],
                [InlineKeyboardButton("נטוי", callback_data="set_style:italic")],
                [InlineKeyboardButton("מודגש + נטוי", callback_data="set_style:bold_italic")],
                [InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")]
            ])
            safe_edit(query, "בחר סגנון טקסט:", reply_markup=style_menu)
        elif data.startswith("set_style:"):
            LOG.info("Action: set_style")
            style = data.split(":")[1]
            if style == "normal":
                st["bold"] = False
                st["italic"] = False
                style_name = "רגיל"
            elif style == "bold":
                st["bold"] = True
                st["italic"] = False
                style_name = "מודגש"
            elif style == "italic":
                st["bold"] = False
                st["italic"] = True
                style_name = "נטוי"
            elif style == "bold_italic":
                st["bold"] = True
                st["italic"] = True
                style_name = "מודגש ונטוי"
            safe_edit(query, f"✅ סגנון טקסט נקבע: {style_name}",
                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")]]))
        elif data == "choose_background_color":
            LOG.info("Action: choose_background_color")
            # תפריט בחירת צבע רקע לכתוביות
            color_rows = []
            for i, (label, color) in enumerate(COLOR_CHOICES, 1):
                if i % 3 == 1:
                    color_rows.append([])
                color_rows[-1].append(InlineKeyboardButton(label, callback_data=f"set_bg_color:{color}"))
            color_rows.append([InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")])
            safe_edit(query, "בחר צבע רקע לכתוביות:", reply_markup=InlineKeyboardMarkup(color_rows))
        elif data.startswith("set_bg_color:"):
            LOG.info("Action: set_bg_color")
            color = data.split(":")[1]
            if color in ASS_COLORS:
                st["background_color"] = color
                color_name = next((label for label, color_code in COLOR_CHOICES if color_code == color), color)
                safe_edit(query, f"✅ צבע רקע נקבע: {color_name}",
                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")]]))
        elif data == "choose_outline_size":
            LOG.info("Action: choose_outline_size")
            # תפריט בחירת עובי מתאר
            outline_menu = InlineKeyboardMarkup([
                [InlineKeyboardButton("0 (ללא מתאר)", callback_data="set_outline:0"),
                 InlineKeyboardButton("1 (ברירת מחדל)", callback_data="set_outline:1")],
                [InlineKeyboardButton("2 (עבה)", callback_data="set_outline:2"),
                 InlineKeyboardButton("3 (עבה מאוד)", callback_data="set_outline:3")],
                [InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")]
            ])
            safe_edit(query, "בחר עובי מתאר לכתוביות:", reply_markup=outline_menu)
        elif data.startswith("set_outline:"):
            LOG.info("Action: set_outline")
            size = int(data.split(":")[1])
            st["outline_size"] = size
            safe_edit(query, f"✅ עובי מתאר נקבע: {size}",
                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")]]))
        elif data == "choose_shadow_size":
            LOG.info("Action: choose_shadow_size")
            # תפריט בחירת גודל צל
            shadow_menu = InlineKeyboardMarkup([
                [InlineKeyboardButton("0 (ללא צל)", callback_data="set_shadow:0"),
                 InlineKeyboardButton("1 (ברירת מחדל)", callback_data="set_shadow:1")],
                [InlineKeyboardButton("2 (צל בולט)", callback_data="set_shadow:2"),
                 InlineKeyboardButton("3 (צל בולט מאוד)", callback_data="set_shadow:3")],
                [InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")]
            ])
            safe_edit(query, "בחר גודל צל לכתוביות:", reply_markup=shadow_menu)
        elif data.startswith("set_shadow:"):
            LOG.info("Action: set_shadow")
            size = int(data.split(":")[1])
            st["shadow_size"] = size
            safe_edit(query, f"✅ גודל צל נקבע: {size}",
                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ חזרה", callback_data="advanced_subtitle_settings")]]))
        elif data == "help":
            LOG.info("Action: help")
            safe_edit(query, t(uid, "choose_from_menu"), reply_markup=main_menu_kb(uid, st))
        elif data == "back_main":
            LOG.info("Action: back_main")
            safe_edit(query, t(uid, "prompt_back_main"), reply_markup=main_menu_kb(uid, st))
        elif data.startswith("logo_setpos:"):
            LOG.info("Action: logo_setpos")
            pos = data.split(":")[1]
            if pos in [p[1] for p in LOGO_POSITIONS]:
                st["logo_position"] = pos
            # מצא את שם המיקום
            pos_name = next((label for label, pos_code in LOGO_POSITIONS if pos_code == pos), pos)
            safe_edit(query, t(uid, "logo_pos_set", pos_name=pos_name), reply_markup=logo_size_menu(uid))
        elif data.startswith("logo_setsize:"):
            LOG.info("Action: logo_setsize")
            size = int(data.split(":")[1])
            if size in LOGO_SIZE_CHOICES:
                st["logo_size_percent"] = size
            # עכשיו נבחר שקיפות
            safe_edit(query, t(uid, "logo_size_set", size=st['logo_size_percent']), reply_markup=logo_opacity_menu(uid))
        elif data.startswith("logo_setopacity:"):
            LOG.info("Action: logo_setopacity")
            p = int(data.split(":")[1])
            st["logo_opacity"] = max(0, min(100, p))
            # עכשיו מחכים לווידאו
            st["expecting_video_for_logo"] = True
            safe_edit(query, t(uid, "logo_opacity_set", opacity=st['logo_opacity']), reply_markup=main_menu_kb(uid, st))
        else:
            query.answer()
    except Unauthorized:
        try:
            safe_edit(query, "❌ שגיאת הרשאות בבוט (Unauthorized). בדקו את ה-Token.")
        except Exception:
            pass
    except Exception as e:
        LOG.exception("Callback error")
        try:
            safe_edit(query, f"ארעה שגיאה: {e}")
        except Exception:
            try:
                query.message.reply_text(f"ארעה שגיאה: {e}")
            except Exception:
                pass

def handle_photo(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    st = get_user_state(uid)
    if not st.get("expecting_logo_image"):
        update.message.reply_text(t(uid, "error_unsupported_file_type"))
        return
    
    try:
        photo = update.message.photo[-1]
        
        # בדיקת גודל התמונה (עד 5MB)
        if photo.file_size and photo.file_size > 5 * 1024 * 1024:
            update.message.reply_text(t(uid, "error_image_too_large"))
            return
            
        try:
            file = context.bot.get_file(photo.file_id)
        except Exception as e:
            if "too big" in str(e).lower():
                update.message.reply_text(t(uid, "error_image_too_large"))
            else:
                update.message.reply_text(t(uid, "error_upload_failed"))
            return
            
        logo_path = str(APP_DIR / f"logo_{uuid.uuid4().hex}.png")
        file.download(custom_path=logo_path)

        # המרה ל-PNG אם צריך (כבר שמור כpng)
        st["logo_path"] = logo_path
        st["expecting_logo_image"] = False
        update.message.reply_text(t(uid, "logo_uploaded_success"), reply_markup=logo_pos_menu(uid))
    except Exception as e:
        update.message.reply_text(t(uid, "error_upload_failed"))

def simple_translate_fallback(texts: List[str], dest_lang: str) -> List[str]:
    """
    תרגום fallback פשוט - מחזיר את הטקסט המקורי עם הודעה
    """
    LOG.info("🔄 משתמש בתרגום fallback פשוט")
    return [f"[{dest_lang.upper()}] {text}" for text in texts]

def translate_text(text: str, dest_lang: str) -> str:
    """
    תרגום פשוט עם googletrans - מודל אחד בלבד
    """
    try:
        from googletrans import Translator
        translator = Translator()
        
        # ניקוי טקסט
        text = text.strip()
        if not text or len(text) < 2:
            return text
            
        # תרגום
        result = translator.translate(text, dest=dest_lang)
        translated = result.text
        
        if translated and translated != text:
            LOG.info(f"✅ תרגום מוצלח: {text[:30]}... -> {translated[:30]}...")
            return translated
        else:
            return text
            
    except Exception as e:
        LOG.error(f"שגיאה בתרגום: {e}")
        return text

def parallel_translate_batch(texts: List[str], dest_lang: str) -> List[str]:
    """
    תרגום מקבילי של אצוות טקסט
    מחזיר רשימת תרגומים בסדר מקביל לטקסט המקורי
    """
    if not texts:
        return []
        
    result_translations = [""] * len(texts)
    cached_indices = []  # מיקומים שנמצאו במטמון
    texts_to_translate = []  # טקסטים לתרגום
    indices_map = []  # מיפוי בין אינדקס מקורי לאינדקס בבקשה
    
    # בדיקת מטמון ראשונית
    for i, text in enumerate(texts):
        if not text.strip():
            result_translations[i] = text
            cached_indices.append(i)
            continue
            
        # ניקוי טקסט לתרגום טוב יותר
        clean_text = text.strip()
        if len(clean_text) < 2:  # טקסטים קצרים מדי
            result_translations[i] = clean_text
            cached_indices.append(i)
            continue
            
        cached = get_cached_translation(clean_text, dest_lang)
        if cached:
            result_translations[i] = cached
            cached_indices.append(i)
        else:
            texts_to_translate.append(clean_text)
            indices_map.append(i)
    
    # אם הכל מהמטמון - החזר מיד
    if not texts_to_translate:
        return result_translations
    
    # הכנת תרגום באצוות קטנות ומקביליות
    max_batch_size = 3  # הקטנת גודל האצווה להפחתת שגיאות
    batches = []
    
    for i in range(0, len(texts_to_translate), max_batch_size):
        batch = texts_to_translate[i:i+max_batch_size]
        batches.append(batch)
    
    # תרגום עם googletrans
    try:
        LOG.info(f"🔄 מתחיל תרגום של {len(texts_to_translate)} טקסטים ל-{dest_lang}")
        
        # תרגום כל טקסט בנפרד
        for i, text in enumerate(texts_to_translate):
            original_idx = indices_map[i]
            
            # תרגום עם googletrans
            translated = translate_text(text, dest_lang)
            
            if translated and translated != text:
                result_translations[original_idx] = translated
                cache_translation(text, dest_lang, translated)
                LOG.info(f"✅ תרגום מוצלח: {text[:30]}... -> {translated[:30]}...")
            else:
                # אם התרגום נכשל - השאר את הטקסט המקורי
                LOG.warning(f"⚠️ לא ניתן לתרגם: {text[:50]}...")
                result_translations[original_idx] = text
        
        LOG.info(f"✅ תרגום הושלם: {len([t for t in result_translations if t])} מתוך {len(texts)} טקסטים")
    
    except Exception as e:
        LOG.error(f"Error in parallel translation setup: {e}")
        # במקרה של כשל כללי - החזרת הטקסט המקורי
        for i, idx in enumerate(indices_map):
            result_translations[idx] = texts_to_translate[i]
    
    return result_translations

def handle_document_or_video(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    st = get_user_state(uid)

    doc = update.message.document
    vid = update.message.video

    # --- תחילה: אם מדובר בלוגו (מסמך תמונה) ---
    if st.get("expecting_logo_image") and doc and (doc.mime_type or "").startswith("image/"):
        # בדיקת גודל התמונה (עד 5MB)
        if doc.file_size and doc.file_size > 5 * 1024 * 1024:
            update.message.reply_text(t(uid, "error_image_too_large"))
            return
            
        # בדיקת סוג הקובץ
        ext = os.path.splitext(doc.file_name or "")[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png']:
            update.message.reply_text(t(uid, "error_unsupported_image"))
            return
            
        try:
            file = context.bot.get_file(doc.file_id)
            logo_path = str(APP_DIR / f"logo_{uuid.uuid4().hex}{ext}")
            file.download(custom_path=logo_path)
        except Exception as e:
            if "too big" in str(e).lower():
                update.message.reply_text(t(uid, "error_image_too_large"))
            else:
                update.message.reply_text(t(uid, "error_upload_failed"))
            return

            # המרה ל-PNG אם צריך
            from PIL import Image
            if ext.lower() != ".png":
                png_path = str(APP_DIR / f"logo_{uuid.uuid4().hex}.png")
                Image.open(logo_path).convert("RGBA").save(png_path, "PNG")
                try: os.remove(logo_path)
                except Exception: pass
                logo_path = png_path

            st["logo_path"] = logo_path
            st["expecting_logo_image"] = False
            update.message.reply_text(t(uid, "logo_uploaded_success"), reply_markup=logo_pos_menu(uid))
            return
        except Exception as e:
            LOG.error(f"Error handling logo document: {e}")
            update.message.reply_text(t(uid, "error_upload_failed"))
            return

    # --- סרטון עבור אחד מהמצבים ---
    # קבע מקור: video או document (וידאו)
    tg_file = None
    filename = None
    size = None
    if vid:
        # בדיקת גודל מוקדמת לפני הורדה
        if vid.file_size and vid.file_size > MAX_FILE_SIZE:
            update.message.reply_text(t(uid, "error_file_too_large"))
            return
            
        try:
            tg_file = context.bot.get_file(vid.file_id)
            filename = f"video_{uuid.uuid4().hex}.mp4"
            size = vid.file_size
        except Exception as e:
            if "too big" in str(e).lower():
                update.message.reply_text(t(uid, "error_file_too_large"))
            else:
                update.message.reply_text(t(uid, "error_upload_failed"))
            return
    elif doc:
        # בדיקת גודל מוקדמת
        if doc.file_size and doc.file_size > MAX_FILE_SIZE:
            update.message.reply_text(t(uid, "error_file_too_large"))
            return
            
        if (doc.mime_type or "").startswith("video/") or (os.path.splitext(doc.file_name or "")[1].lower() in ALLOWED_VIDEO_EXT):
            try:
                tg_file = context.bot.get_file(doc.file_id)
                filename = doc.file_name or f"video_{uuid.uuid4().hex}.mp4"
                size = doc.file_size
            except Exception as e:
                if "too big" in str(e).lower():
                    update.message.reply_text(t(uid, "error_file_too_large"))
                else:
                    update.message.reply_text(t(uid, "error_upload_failed"))
                return
        else:
            update.message.reply_text(t(uid, "error_unsupported_file_type"))
            return
    else:
        # אם זה לא וידאו ולא לוגו - זה מסמך לא נתמך
        if not st.get("expecting_logo_image") and not st.get("expecting_video_for_subs") and not st.get("expecting_video_for_logo"):
            update.message.reply_text(t(uid, "error_unsupported_file_type"))
        else:
            update.message.reply_text(t(uid, "error_invalid_file"))
        return

    # בדיקת גודל הקובץ
    if size and size > MAX_FILE_SIZE:
        update.message.reply_text(t(uid, "error_file_too_large"))
        return

    # הורדה עם מנהל קבצים זמניים
    local_video = TEMP_MANAGER.create_temp_file("in", Path(filename).suffix.lower())
    try:
        update.message.reply_text(t(uid, "downloading_video"))
        tg_file.download(custom_path=local_video)
        # ניקוי זיכרון אחרי הורדה גדולה
        TEMP_MANAGER.clear_memory()
    except Exception as e:
        LOG.error(f"Error downloading file: {e}")
        update.message.reply_text(t(uid, "error_upload_failed"))
        TEMP_MANAGER.cleanup_file(local_video)
        return

    # --- מצב לוגו ---
    if st.get("expecting_video_for_logo"):
        if not st.get("logo_path"):
            update.message.reply_text(t(uid, "error_invalid_file"))
            return
            
        # מעבר לעיבוד במאגר התהליכים המקבילי
        def process_logo_video():
            output_video = None
            try:
                pos_name = next((label for label, pos_code in LOGO_POSITIONS if pos_code == st.get("logo_position", "TR")), st.get("logo_position", "TR"))
                logo_size_percent = st.get("logo_size_percent", 20)
                scale_ratio = logo_size_percent / 100.0
                output_video = TEMP_MANAGER.create_temp_file("logo", ".mp4")
                
                # הטמעת הלוגו בווידאו
                try:
                    overlay_logo(
                        input_video=local_video,
                        logo_png=st["logo_path"],
                        output_video=output_video,
                        position=st.get("logo_position", "TR"),
                        opacity_percent=int(st.get("logo_opacity", 70)),
                        scale_ratio=scale_ratio
                    )
                    # ניקוי זיכרון לאחר פעולת הטמעה כבדה
                    TEMP_MANAGER.clear_memory(True)
                except Exception as e:
                    LOG.error(f"Failed to overlay logo: {e}")
                    if output_video:
                        TEMP_MANAGER.cleanup_file(output_video)
                    raise RuntimeError("Failed to overlay logo on video")
                    
                if not os.path.exists(output_video):
                    raise RuntimeError("Output video with logo not created")
                    
                # שליחת הווידאו עם הלוגו
                with open(output_video, "rb") as f:
                    update.message.reply_video(
                        video=f,
                        supports_streaming=True,
                        caption=t(uid, "logo_done_caption",
                            pos_name=pos_name,
                            size=st.get("logo_size_percent", 20),
                            opacity=st.get("logo_opacity", 70)
                        )
                    )
                # שליחת תפריט ראשי נפרד
                update.message.reply_text(t(uid, "back_main_done"), reply_markup=main_menu_kb(uid, st))
                
                return True
            except Exception as e:
                LOG.error(f"Error in logo processing: {e}")
                if "connection" in str(e).lower() or "network" in str(e).lower():
                    update.message.reply_text(t(uid, "error_no_internet"))
                else:
                    update.message.reply_text(t(uid, "error_processing_failed"))
                return False
            finally:
                try:
                    if output_video:
                        cleanup_paths([local_video, output_video])
                    else:
                        cleanup_paths([local_video])
                except Exception:
                    pass
        
        # התחלת העיבוד והודעה ראשונית
        pos_name = next((label for label, pos_code in LOGO_POSITIONS if pos_code == st.get("logo_position", "TR")), st.get("logo_position", "TR"))
        update.message.reply_text(t(uid, "logo_processing_start", 
                                   pos_name=pos_name, 
                                   size=st.get('logo_size_percent', 20), 
                                   opacity=st.get('logo_opacity', 70)))
        
        # ביצוע העיבוד במאגר התהליכים
        EXECUTOR.submit(process_logo_video)
        st["expecting_video_for_logo"] = False
        return

    # --- מצב תרגום וכתוביות ---
    if not st.get("expecting_video_for_subs"):
        update.message.reply_text(t(uid, "error_invalid_file"))
        cleanup_paths([local_video])
        return

    # הגבלת עומס: עד 2 במקביל למשתמש
    if not inc_jobs(uid):
        update.message.reply_text(t(uid, "error_processing_failed"))
        cleanup_paths([local_video])
        return

    # תהליך תרגום וכתוביות במאגר התהליכים המקבילי
    def process_translation_video():
        try:
            # הודעת התחלה
            target_lang_name = next((name for name, code in LANG_CHOICES if code == st.get("target_lang", "en")), st.get("target_lang", "en"))
            color_name = next((label for label, color in COLOR_CHOICES if color == st.get("font_color", "white")), st.get("font_color", "white"))
            
            # חילוץ אודיו עם מנהל הקבצים הזמניים
            wav_path = TEMP_MANAGER.create_temp_file("audio", ".wav")
            try:
                extract_audio_16k_mono(local_video, wav_path)
                # ניקוי זיכרון לאחר המרת אודיו (שיכולה להיות כבדה)
                TEMP_MANAGER.clear_memory(True)
            except Exception as e:
                LOG.error(f"Audio extraction failed: {e}")
                TEMP_MANAGER.cleanup_file(wav_path)
                raise RuntimeError("Failed to extract audio from video")

            # תעתיק (transcription)
            segs, lang = stt_whisper(wav_path)
            if not segs:
                raise RuntimeError("No transcription results received.")

            # תרגום לשפת היעד
            # משתמש במנגנון תרגום המקבילי החדש עם תמיכה באצוות
            all_texts = [seg["text"] for seg in segs]
            target_lang = st.get("target_lang", "en")
            LOG.info(f"🎯 מתרגם ל-{target_lang} (שפה נבחרת: {target_lang})")
            translated_texts = parallel_translate_batch(all_texts, target_lang)
            
            # שילוב התרגומים בתוך המקטעים
            segs_tr = []
            for i, seg in enumerate(segs):
                translated_text = translated_texts[i] if i < len(translated_texts) else seg["text"]
                segs_tr.append({**seg, "text": translated_text})

            # יצירת קובץ SRT עם מנהל הקבצים הזמניים
            srt_path = TEMP_MANAGER.create_temp_file("subs", ".srt")
            try:
                write_srt(segs_tr, srt_path)
            except Exception as e:
                LOG.error(f"Failed to write SRT: {e}")
                TEMP_MANAGER.cleanup_file(srt_path)
                raise RuntimeError("Failed to create subtitle file")

            # צריבת כתוביות
            out_video = TEMP_MANAGER.create_temp_file("out", ".mp4")
            try:
                # יצירת הגדרות כתוביות מותאמות אישית
                subtitle_config = SubtitleConfig.from_user_state(st)
                burn_subs_from_srt(
                    input_video=local_video,
                    srt_path=srt_path,
                    output_video=out_video,
                    subtitle_config=subtitle_config
                )
                # ניקוי זיכרון לאחר פעולת קידוד כבדה
                TEMP_MANAGER.clear_memory(True)
            except Exception as e:
                LOG.error(f"Failed to burn subtitles: {e}")
                TEMP_MANAGER.cleanup_file(out_video)
                raise RuntimeError("Failed to burn subtitles to video")

            # שליחת הווידאו המתורגם
            if not os.path.exists(out_video):
                raise RuntimeError("Output video file not created")
                
            size_bytes = os.path.getsize(out_video)
            if size_bytes > MAX_FILE_SIZE:
                update.message.reply_text(t(uid, "error_file_too_large"))
            else:
                with open(out_video, "rb") as f:
                    update.message.reply_video(
                        video=f, supports_streaming=True,
                        caption=t(uid, "translated_done_caption",
                            lang_label=t(uid, "settings_language"),
                            size_label=t(uid, "settings_font_size"),
                            color_label=t(uid, "settings_color"),
                            lang=target_lang_name,
                            size=st.get("font_size", 16),
                            color=color_name,
                            src_lang=lang or 'unknown'
                        )
                    )
                # שליחת תפריט ראשי נפרד
                update.message.reply_text(t(uid, "back_main_done"), reply_markup=main_menu_kb(uid, st))
                
            return True
        except Exception as e:
            LOG.error(f"Error processing video: {e}")
            # בדיקה אם זו שגיאת חיבור
            if "connection" in str(e).lower() or "network" in str(e).lower():
                update.message.reply_text(t(uid, "error_no_internet"))
            else:
                update.message.reply_text(t(uid, "error_processing_failed"))
            return False
        finally:
            try:
                cleanup_paths([local_video, wav_path, srt_path, out_video])
            except Exception:
                pass
            st["expecting_video_for_subs"] = False
            dec_jobs(uid)

    # הודעות על התחלת העיבוד
    target_lang_name = next((name for name, code in LANG_CHOICES if code == st.get("target_lang", "en")), st.get("target_lang", "en"))
    color_name = next((label for label, color in COLOR_CHOICES if color == st.get("font_color", "white")), st.get("font_color", "white"))
    update.message.reply_text(t(uid, "processing_start",
        lang_label=t(uid, "settings_language"),
        size_label=t(uid, "settings_font_size"),
        color_label=t(uid, "settings_color"),
        lang=target_lang_name,
        size=st.get('font_size', 16),
        color=color_name
    ))
    update.message.reply_text(t(uid, "convert_audio"))
    
    # הפעלת העיבוד דרך מאגר התהליכים
    def processing_workflow():
        # עדכון סטטוס
        update.message.reply_text(t(uid, "transcribing"))
        # ביצוע העיבוד העיקרי (משלב תעתוק והלאה)
        result = process_translation_video()
        if not result:
            # נוקה כבר בתהליך הפנימי
            pass
    
    # הפעלת התהליך המלא במאגר התהליכים
    EXECUTOR.submit(processing_workflow)

# ------------- פקודות -------------
def help_button_entry(update: Update, context: CallbackContext):
    uid = update.effective_user.id
    st = get_user_state(uid)
    update.message.reply_text(t(uid, "choose_from_menu"), reply_markup=main_menu_kb(uid, st))

def error_handler(update: Update, context: CallbackContext):
    """
    Error handler גלובלי לתפיסת כל השגיאות
    """
    try:
        uid = update.effective_user.id if update.effective_user else None
        if uid:
            st = get_user_state(uid)
        else:
            st = {"ui_lang": "en"}
            
        error = context.error
        LOG.error(f"Error handler caught: {error}")
        
        # בדיקת סוג השגיאה
        if "too big" in str(error).lower() or "file is too big" in str(error).lower():
            update.message.reply_text(t(uid, "error_file_too_large"))
        elif "unsupported" in str(error).lower():
            update.message.reply_text(t(uid, "error_unsupported_file_type"))
        elif "connection" in str(error).lower() or "network" in str(error).lower():
            update.message.reply_text(t(uid, "error_no_internet"))
        else:
            update.message.reply_text(t(uid, "error_processing_failed"))
            
    except Exception as e:
        LOG.error(f"Error in error handler: {e}")
        try:
            update.message.reply_text("❌ אירעה שגיאה לא צפויה. אנא נסה שוב.")
        except:
            pass

# -----------------------------
# בדיקת תחביר + smoke test
# -----------------------------
def run_smoke_tests():
    # 1) בדיקת תחביר על הקובץ עצמו
    try:
        with open(__file__, "r", encoding="utf-8") as f:
            src = f.read()
        ast.parse(src)
        LOG.info("✅ בדיקת תחביר (ast.parse) עברה בהצלחה.")
    except Exception as e:
        LOG.error(f"❌ שגיאת תחביר: {e}")

    # 2) בדיקת FFmpeg
    try:
        if not ensure_ffmpeg():
            LOG.error("❌ FFmpeg לא זמין ולא ניתן להשיג בינארי. הפסקה.")
            raise SystemExit(1)
    except Exception as e:
        LOG.error(f"❌ בעיית FFmpeg: {e}")
        raise SystemExit(1)

    # 3) בדיקה קלה לפונקציות שלא דורשות טלגרם/FFmpeg (ככל האפשר)
    try:
        # תרגום קצר (יתכן ויכשל עקב חיבור/חסימות – לא מפיל את התהליך)
        _ = translate_text([{"start":0.0,"end":1.2,"text":"hello world"}], "he")
        LOG.info("ℹ️ smoke: translate_text רצה.")
    except Exception as e:
        LOG.warning(f"⚠️ smoke translate_text נכשלת (לא קריטי): {e}")

# -----------------------------
# main
# -----------------------------
def main():
    run_smoke_tests()

    # ניקוי קבצים זמניים ישנים בהפעלה
    try:
        count = TEMP_MANAGER.cleanup_old_files()
        if count > 0:
            LOG.info(f"✅ נוקו {count} קבצים זמניים ישנים בהפעלה")
    except Exception as e:
        LOG.warning(f"⚠️ שגיאה בניקוי קבצים ישנים: {e}")

    # טעינת מטמון התרגומים אם קיים
    if CACHE_FILE.exists():
        load_translation_cache()
    
    # רישום שמירת המטמון בסגירה תקינה
    @atexit.register
    def save_cache_at_exit():
        try:
            if translation_cache:
                LOG.info("שומר מטמון תרגומים לפני סגירה...")
                save_translation_cache()
        except Exception as e:
            LOG.error(f"שגיאה בשמירת מטמון בעת יציאה: {e}")

    # מצב בדיקות בלבד (ללא העלאת הבוט)
    if os.getenv("DRY_RUN_SMOKE"):
        LOG.info("✅ DRY_RUN_SMOKE הופעל – מסיים לאחר בדיקות smoke.")
        return

    # קבלת טוקן מקונפיגורציה, משתנה סביבה או מהמשתמש
    token = get_bot_token()
    if not token:
        LOG.error("❌ לא סופק BOT_TOKEN תקין.")
        raise SystemExit(1)

    # אתחול הבוט
    try:
        updater = Updater(token=token, use_context=True)
    except Unauthorized:
        LOG.error("❌ Unauthorized – בדקו את ה-Token ב-BotFather.")
        raise SystemExit(1)
    except Exception as e:
        LOG.error(f"❌ כשל באתחול Updater: {e}")
        raise SystemExit(1)

    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_cmd))

    dp.add_handler(CallbackQueryHandler(cb_handler))

    # תמונות (לוגו)
    dp.add_handler(MessageHandler(Filters.photo, handle_photo))

    # מסמכים/וידאו
    dp.add_handler(MessageHandler(Filters.document | Filters.video, handle_document_or_video))

    # עזרה מהירה
    dp.add_handler(MessageHandler(Filters.text & Filters.regex(r"^/menu$"), help_button_entry))
    
    # Error handler גלובלי
    dp.add_error_handler(error_handler)

    LOG.info("🚀 הבוט עלה. מאזין לעדכונים...")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
