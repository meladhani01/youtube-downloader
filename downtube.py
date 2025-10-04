import sys
import os
import json
import re
import subprocess
import threading
import yt_dlp

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox, QProgressBar,
    QFileDialog, QMessageBox, QTabWidget, QPlainTextEdit, QListWidget,
    QListWidgetItem, QAbstractItemView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QFont

# --- بداية قسم منطق التحميل ---
stop_event = threading.Event()

def reset_stop_event():
    stop_event.clear()

def stop_download_process():
    stop_event.set()

def get_format_options(quality, file_type):
    quality_map = {
        'منخفضة': 'best[height<=360]',
        'متوسطة': 'best[height<=720]',
        'عالية': 'best[height<=1080]/bestvideo[height<=1080]+bestaudio/best'
    }
    quality_value_video = quality_map.get(quality, 'best[height<=720]') # الافتراضي متوسطة

    if file_type == 'mp3':
        # جودة الصوت mp3 ستكون 192kbps بواسطة FFmpeg
        return 'bestaudio/best'
    else: # mp4
        # دمج أفضل مرئية (بالجودة المحددة) مع أفضل صوت
        return f'{quality_value_video}[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'


def get_videos_info(url):
    ydl_opts = {
        "quiet": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "simulate": True,
        "forcejson": True,
        "no_warnings": True,
        "socket_timeout": 20, # مهلة للاتصال الأولي
    }
    videos = []
    playlist_title_text = None

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if not info:
                raise Exception("لم يتم العثور على معلومات للمرئية.")

            if 'entries' in info and info['entries']:
                playlist_title_text = info.get("title", "قائمة تشغيل غير مسماة")
                for entry in info["entries"]:
                    if entry:
                        video_id = entry.get('id')
                        video_title = entry.get('title', 'مرئية بدون عنوان')
                        if video_id:
                             videos.append({
                                "title": video_title,
                                "url": f"https://www.youtube.com/watch?v={video_id}",
                                "id": video_id
                            })
            elif 'id' in info :
                video_id = info.get('id')
                video_title = info.get('title', 'مرئية بدون عنوان')
                if video_id:
                    videos.append({
                        "title": video_title,
                        "url": info.get('webpage_url', f"https://www.youtube.com/watch?v={video_id}"),
                        "id": video_id
                    })
            else:
                raise Exception("تنسيق المعلومات غير مدعوم أو الرابط غير صالح.")

            return {
                "videos": videos,
                "playlist_title": playlist_title_text
            }
    except yt_dlp.utils.DownloadError as e:
        if "Unsupported URL" in str(e):
             raise Exception(f"الرابط غير مدعوم: {url}")
        elif "Video unavailable" in str(e):
            raise Exception("المرئية غير متاح.")
        else:
            raise Exception(f"خطأ في جلب معلومات المرئية: {str(e)}")
    except Exception as e:
        raise Exception(f"خطأ غير متوقع في جلب المعلومات: {str(e)}")


def sanitize_filename(filename):
    filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
    filename = re.sub(r'\s+', " ", filename).strip()
    if len(filename) > 150:
        filename = filename[:147] + "..."
    return filename
# --- نهاية قسم منطق التحميل ---

# --- بداية قسم فحص FFmpeg ---
def check_ffmpeg_installed():
    try:
        # إخفاء نافذة الطرفية على ويندوز
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        result = subprocess.run(['ffmpeg', '-version'],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                startupinfo=startupinfo,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        return result.returncode == 0
    except FileNotFoundError:
        return False
# --- نهاية قسم فحص FFmpeg ---


# --- بداية العامل (Worker) للعمليات الطويلة ---
class DownloadWorker(QObject):
    progress_updated = pyqtSignal(int, str)
    status_updated = pyqtSignal(str)
    info_fetched_signal = pyqtSignal(dict)
    download_finished_signal = pyqtSignal(str, bool)
    log_message_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, url, download_dir_base, quality, file_type, download_subtitles, selected_videos_info=None, playlist_title_override=None):
        super().__init__()
        self.url = url
        self.download_dir_base = download_dir_base
        self.quality = quality
        self.file_type = file_type
        self.download_subtitles = download_subtitles
        self.selected_videos_info = selected_videos_info
        self.playlist_title_override = playlist_title_override


    def run_get_info(self):
        try:
            self.log_message_signal.emit(f"جاري جلب معلومات من الرابط: {self.url}")
            result = get_videos_info(self.url)
            self.info_fetched_signal.emit(result)
            self.log_message_signal.emit(f"تم جلب المعلومات بنجاح. عدد المرئيات: {len(result.get('videos', []))}")
        except Exception as e:
            self.log_message_signal.emit(f"خطأ أثناء جلب المعلومات: {str(e)}")
            self.error_signal.emit(f"خطأ في جلب المعلومات: {str(e)}")


    def run_download(self):
        reset_stop_event()

        videos_to_download = []
        if self.selected_videos_info:
            videos_to_download = self.selected_videos_info
            effective_playlist_title = self.playlist_title_override
        else:
            try:
                info_result = get_videos_info(self.url)
                if info_result and info_result["videos"]:
                    videos_to_download = info_result["videos"]
                else:
                    self.error_signal.emit("لم يتم العثور على معلومات المرئية للتحميل.")
                    self.log_message_signal.emit("فشل: لم يتم العثور على معلومات المرئية للتحميل.")
                    return
                effective_playlist_title = None
            except Exception as e:
                self.error_signal.emit(f"خطأ في جلب معلومات المرئية: {str(e)}")
                self.log_message_signal.emit(f"فشل: خطأ في جلب معلومات المرئية: {str(e)}")
                return

        if not videos_to_download:
            self.error_signal.emit("لا توجد مرئيةهات للتحميل.")
            self.log_message_signal.emit("لا توجد مرئيةهات للتحميل.")
            self.download_finished_signal.emit("", False)
            return

        total_videos = len(videos_to_download)
        for i, video_info in enumerate(videos_to_download):
            if stop_event.is_set():
                self.status_updated.emit("تم إيقاف التحميل.")
                self.log_message_signal.emit("تم إيقاف التحميل من قبل المستخدم.")
                self.download_finished_signal.emit(video_info.get("title", "غير معروف"), False)
                break

            current_video_url = video_info["url"]
            current_video_title = video_info.get("title", "مرئية غير مسمى")
            self.log_message_signal.emit(f"بدء تحميل ({i+1}/{total_videos}): {current_video_title}")
            self.status_updated.emit(f"جاري تحميل ({i+1}/{total_videos}): {current_video_title[:50]}...")

            final_download_dir = self.download_dir_base
            if effective_playlist_title:
                s_playlist_title = sanitize_filename(effective_playlist_title)
                playlist_folder_path = os.path.join(self.download_dir_base, s_playlist_title)
                if not os.path.exists(playlist_folder_path):
                    try:
                        os.makedirs(playlist_folder_path)
                        self.log_message_signal.emit(f"تم إنشاء مجلد قائمة التشغيل: {playlist_folder_path}")
                    except OSError as e:
                        self.error_signal.emit(f"فشل في إنشاء مجلد قائمة التشغيل: {e}")
                        self.log_message_signal.emit(f"فشل في إنشاء مجلد قائمة التشغيل: {e}")
                        self.download_finished_signal.emit(current_video_title, False)
                        continue
                final_download_dir = playlist_folder_path


            def custom_progress_hook(d):
                if stop_event.is_set():
                    raise yt_dlp.utils.DownloadError("تم إيقاف التحميل من قبل المستخدم.")

                if d['status'] == 'downloading':
                    filename = d.get('filename', 'غير معروف')
                    total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
                    downloaded_bytes = d.get('downloaded_bytes', 0)
                    if total_bytes and downloaded_bytes is not None:
                        progress = int((downloaded_bytes / total_bytes) * 100)
                        self.progress_updated.emit(progress, os.path.basename(filename))
                elif d['status'] == 'finished':
                    filename = d.get('filename', current_video_title)
                    self.progress_updated.emit(100, os.path.basename(filename))
                    self.log_message_signal.emit(f"اكتمل تحميل: {os.path.basename(filename)}")
                elif d['status'] == 'error':
                    self.log_message_signal.emit(f"خطأ أثناء تحميل {d.get('filename', 'ملف')}")

            output_template = os.path.join(final_download_dir, '%(title)s.%(ext)s')

            ydl_opts = {
                'format': get_format_options(self.quality, self.file_type),
                'outtmpl': output_template,
                'progress_hooks': [custom_progress_hook],
                'noprogress': True,
                'quiet': True,
                'no_warnings': True,
                'retries': 5,  # زيادة عدد مرات إعادة المحاولة
                'fragment_retries': 5, # لنفس السبب
                'socket_timeout': 60, # زيادة المهلة إلى 60 ثانية
                'keepvideo': False, # حذف الملفات المؤقتة بعد المعالجة
                # 'continuedl': True, # افتراضي
                # 'ignoreerrors': True, # إذا أردت تجاهل الأخطاء في قائمة التشغيل والمتابعة
            }

            if self.file_type == 'mp3':
                ydl_opts['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192k', # استخدام 'k' للجودة
                }]
                # لا حاجة لـ merge_output_format هنا
            elif self.file_type == 'mp4':
                 ydl_opts['merge_output_format'] = 'mp4'

            if self.download_subtitles:
                ydl_opts['writesubtitles'] = True
                ydl_opts['subtitleslangs'] = ['ar', 'en'] # اللغات المطلوبة للترجمة
                ydl_opts['writeautomaticsub'] = True


            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([current_video_url])
                self.download_finished_signal.emit(current_video_title, True)

            except yt_dlp.utils.DownloadError as e:
                if "تم إيقاف التحميل من قبل المستخدم" in str(e):
                    self.status_updated.emit(f"توقف تحميل: {current_video_title}")
                    self.log_message_signal.emit(f"توقف تحميل: {current_video_title}")
                    self.download_finished_signal.emit(current_video_title, False)
                else:
                    error_msg = f"خطأ في تحميل {current_video_title}: {str(e)}"
                    # اختصار رسائل الخطأ الطويلة من yt-dlp
                    if "Read timed out" in str(e):
                        error_msg = f"خطأ في تحميل {current_video_title}: انتهت مهلة الاتصال. حاول مرة أخرى أو تحقق من اتصالك بالإنترنت."
                    elif "HTTP Error 403" in str(e):
                        error_msg = f"خطأ في تحميل {current_video_title}: خطأ 403 - الوصول مرفوض. قد يكون المرئية خاصًا أو محظورًا."

                    self.error_signal.emit(error_msg)
                    self.log_message_signal.emit(error_msg)
                    self.download_finished_signal.emit(current_video_title, False)
            except Exception as e:
                self.error_signal.emit(f"خطأ غير متوقع أثناء تحميل {current_video_title}: {str(e)}")
                self.log_message_signal.emit(f"خطأ غير متوقع أثناء تحميل {current_video_title}: {str(e)}")
                self.download_finished_signal.emit(current_video_title, False)

        if not stop_event.is_set():
            self.status_updated.emit("اكتملت جميع التحميلات المجدولة.")
            self.log_message_signal.emit("اكتملت جميع التحميلات المجدولة.")
# --- نهاية العامل (Worker) ---


class YouTubeDownloaderApp(QMainWindow):
    CONFIG_FILE = "config.json"
    DEFAULT_DOWNLOAD_DIR = os.path.join(os.getcwd(), "مجلد_التنزيلات")

    STYLESHEET = """
        QMainWindow, QWidget {
            background-color: #1e1f22;
            color: #dcdde1;
            font-family: Tahoma, Arial, sans-serif;
            font-size: 10pt;
        }
        QLineEdit, QPlainTextEdit, QListWidget {
            background-color: #2b2d31;
            color: #dcdde1;
            border: 1px solid #3a3c41;
            border-radius: 5px;
            padding: 7px; /* زيادة padding قليلاً */
        }
        QPlainTextEdit, QListWidget {
            font-size: 9.5pt;
        }
        QPushButton {
            background-color: #40444b; /* لون زر أساسي */
            color: #ffffff;
            border: none;
            border-radius: 5px;
            padding: 9px 18px; /* زيادة padding */
            min-height: 24px; /* زيادة min-height */
            font-weight: bold;
            outline: none; /* إزالة إطار التركيز */
        }
        QPushButton:hover {
            background-color: #4f545c;
        }
        QPushButton:pressed {
            background-color: #3a3c41;
        }
        QPushButton:disabled {
            background-color: #2b2d31;
            color: #707378;
        }
        QComboBox {
            background-color: #2b2d31;
            color: #dcdde1;
            border: 1px solid #3a3c41;
            border-radius: 5px;
            padding: 7px;
            min-height: 24px;
            outline: none;
        }
        QComboBox::drop-down {
            border: none;
            background-color: transparent;
            width: 22px;
        }
        /* QComboBox::down-arrow : تم الاعتماد على السهم الافتراضي للنظام/النمط */
        QComboBox QAbstractItemView {
            background-color: #2b2d31;
            color: #dcdde1;
            selection-background-color: #0078d4; /* أزرق مايكروسوفت */
            border-radius: 5px;
            border: 1px solid #3a3c41;
            outline: none;
        }
        QCheckBox {
            spacing: 9px;
            color: #dcdde1;
        }
        QCheckBox::indicator {
            width: 19px; /* زيادة حجم المؤشر */
            height: 19px;
            border-radius: 4px;
        }
        QCheckBox::indicator:unchecked {
            background-color: #2b2d31;
            border: 1px solid #4f545c; /* حد أوضح */
        }
        QCheckBox::indicator:unchecked:hover {
            border: 1px solid #7289da; /* Blurple عند التحويم */
        }
        QCheckBox::indicator:checked {
            background-color: #0078d4;
            border: 1px solid #005a9e;
        }
        QCheckBox::indicator:checked:hover {
            background-color: #005a9e;
        }
        QProgressBar {
            border: 1px solid #3a3c41;
            border-radius: 6px; /* زيادة border-radius */
            text-align: center;
            color: #ffffff;
            background-color: #2b2d31;
            min-height: 22px;
            font-weight: bold;
        }
        QProgressBar::chunk {
            background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0078d4, stop:1 #005391); /* تدرج أزرق معدل */
            border-radius: 5px;
        }
        QTabWidget::pane {
            border: 1px solid #3a3c41;
            border-top: none;
            background-color: #1e1f22;
        }
        QTabBar::tab {
            background: #2b2d31;
            color: #96989d;
            padding: 11px 22px; /* زيادة padding */
            border: 1px solid #3a3c41;
            border-bottom: none;
            border-top-left-radius: 7px; /* زيادة border-radius */
            border-top-right-radius: 7px;
            min-width: 110px;
            font-weight: bold;
            outline: none;
        }
        QTabBar::tab:selected {
            background: #1e1f22;
            color: #ffffff;
            border-bottom: 1px solid #1e1f22;
        }
        QTabBar::tab:!selected:hover {
            background: #3a3c41;
            color: #dcdde1;
        }
        QLabel {
            color: #dcdde1;
            padding: 3px;
        }
        QListWidget {
             outline: none; /* إزالة إطار التركيز الأزرق حول القائمة */
        }
        QListWidget::item {
            padding: 6px; /* زيادة padding */
            border-radius: 4px;
        }
        QListWidget::item:selected {
            background-color: #0078d4;
            color: white;
        }
        QListWidget::item:hover:!selected {
            background-color: #3a3c41;
        }
        QScrollBar:vertical {
            border: none; background: #2b2d31; width: 14px; margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:vertical {
            background: #4f545c; min-height: 30px; border-radius: 7px;
        }
        QScrollBar::handle:vertical:hover { background: #7289da; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            border: none; background: none; height: 0px;
        }
        QScrollBar:horizontal {
            border: none; background: #2b2d31; height: 14px; margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:horizontal {
            background: #4f545c; min-width: 30px; border-radius: 7px;
        }
        QScrollBar::handle:horizontal:hover { background: #7289da; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            border: none; background: none; width: 0px;
        }
        QToolTip {
            background-color: #121314; color: #dcdde1; border: 1px solid #3a3c41;
            padding: 6px; border-radius: 5px; opacity: 240; /* زيادة opactiy */
        }
        QMessageBox { background-color: #1e1f22; }
        QMessageBox QLabel { color: #dcdde1; font-size: 10pt; }
        QMessageBox QPushButton {
            background-color: #40444b; color: #ffffff; border-radius: 5px;
            padding: 9px 22px; min-width: 85px; font-weight: bold;
        }
        QMessageBox QPushButton:hover { background-color: #4f545c; }
        QMessageBox QPushButton:pressed { background-color: #3a3c41; }
    """

    def __init__(self):
        super().__init__()
        self.current_playlist_info = None
        self.all_videos_in_playlist = []
        self.playlist_title_for_download = None
        self.setWindowTitle("برنامج تحميل الميديا")
        self.setGeometry(250, 150, 800, 600) # حجم أكبر قليلاً
        self.setStyleSheet(self.STYLESHEET)
        self.load_config()
        self.init_ui()
        self.check_and_create_download_dir()
        self.ffmpeg_checked = False
        self.thread = None # تهيئة للتحقق لاحقًا
        self.worker = None # تهيئة

    def init_ui(self):
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(12, 12, 12, 12) # هوامش أوسع قليلاً

        self.tabs = QTabWidget()
        self.tabs.setLayoutDirection(Qt.RightToLeft) # لترتيب التبويبات نفسها RTL
        self.main_tab = QWidget()
        self.log_tab = QWidget()

        self.tabs.addTab(self.main_tab, "الرئيسية")
        self.tabs.addTab(self.log_tab, "سجل العمليات")
        self.main_layout.addWidget(self.tabs)

        main_tab_layout = QVBoxLayout(self.main_tab)
        main_tab_layout.setSpacing(12)

        url_layout = QHBoxLayout()
        url_layout.setSpacing(8)
        self.url_label = QLabel("رابط الميديا:")
        url_layout.addWidget(self.url_label)
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("أدخل رابط المرئية أو قائمة التشغيل هنا")
        url_layout.addWidget(self.url_entry, 1) # السماح بالتمدد

        self.fetch_info_button = QPushButton("جلب المعلومات")
        self.fetch_info_button.clicked.connect(self.fetch_video_info_threaded)
        url_layout.addWidget(self.fetch_info_button)

        self.clear_url_button = QPushButton("مسح")
        self.clear_url_button.clicked.connect(self.clear_url_and_list)
        url_layout.addWidget(self.clear_url_button)
        main_tab_layout.addLayout(url_layout)

        self.video_list_label = QLabel("المرئيات في القائمة (حدد للتحميل):")
        main_tab_layout.addWidget(self.video_list_label)
        self.video_list_widget = QListWidget()
        self.video_list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        main_tab_layout.addWidget(self.video_list_widget)
        self.video_list_widget.setVisible(False)
        self.video_list_label.setVisible(False)

        playlist_actions_layout = QHBoxLayout()
        playlist_actions_layout.setSpacing(8)
        self.select_all_button = QPushButton("تحديد الكل")
        self.select_all_button.clicked.connect(self.select_all_videos)
        playlist_actions_layout.addWidget(self.select_all_button)
        self.deselect_all_button = QPushButton("إلغاء تحديد الكل")
        self.deselect_all_button.clicked.connect(self.deselect_all_videos)
        playlist_actions_layout.addWidget(self.deselect_all_button)
        playlist_actions_layout.addStretch() # لدفع الأزرار
        main_tab_layout.addLayout(playlist_actions_layout)
        self.select_all_button.setVisible(False)
        self.deselect_all_button.setVisible(False)


        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(10)
        self.format_label = QLabel("الصيغة:")
        settings_layout.addWidget(self.format_label)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["mp4", "mp3"])
        self.format_combo.setCurrentText(self.config.get("format", "mp4"))
        settings_layout.addWidget(self.format_combo)

        self.quality_label = QLabel("الجودة:")
        settings_layout.addWidget(self.quality_label)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["منخفضة", "متوسطة", "عالية"])
        self.quality_combo.setCurrentText(self.config.get("quality", "متوسطة"))
        settings_layout.addWidget(self.quality_combo)

        self.subtitles_checkbox = QCheckBox("تحميل الترجمة (إن وجدت)")
        self.subtitles_checkbox.setChecked(self.config.get("subtitles", False))
        settings_layout.addWidget(self.subtitles_checkbox)
        settings_layout.addStretch()
        main_tab_layout.addLayout(settings_layout)

        dir_layout = QHBoxLayout()
        dir_layout.setSpacing(8)
        self.dir_label_prefix = QLabel("مجلد الحفظ:")
        dir_layout.addWidget(self.dir_label_prefix)
        self.dir_label = QLabel(self.config.get("save_dir", self.DEFAULT_DOWNLOAD_DIR))
        self.dir_label.setWordWrap(True)
        dir_layout.addWidget(self.dir_label, 1) # تمدد ليأخذ المساحة
        self.select_dir_button = QPushButton("اختيار المجلد")
        self.select_dir_button.clicked.connect(self.select_directory)
        dir_layout.addWidget(self.select_dir_button)
        main_tab_layout.addLayout(dir_layout)

        download_controls_layout = QHBoxLayout()
        download_controls_layout.setSpacing(8)
        self.download_button = QPushButton("بدء التحميل")
        self.download_button.clicked.connect(self.start_download_threaded)
        download_controls_layout.addWidget(self.download_button, 1)

        self.stop_button = QPushButton("إيقاف التحميل")
        self.stop_button.clicked.connect(self.confirm_stop_download)
        self.stop_button.setEnabled(False)
        download_controls_layout.addWidget(self.stop_button, 1)
        main_tab_layout.addLayout(download_controls_layout)

        self.status_label = QLabel("الحالة: جاهز")
        main_tab_layout.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%") # إظهار النسبة المئوية دائمًا
        main_tab_layout.addWidget(self.progress_bar)

        main_tab_layout.addStretch()

        log_tab_layout = QVBoxLayout(self.log_tab)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        log_tab_layout.addWidget(self.log_output)

        self.log_message("تم تهيئة التطبيق.")

    def check_and_create_download_dir(self):
        save_dir = self.config.get("save_dir", self.DEFAULT_DOWNLOAD_DIR)
        if not os.path.exists(save_dir):
            try:
                os.makedirs(save_dir)
                self.log_message(f"تم إنشاء مجلد التنزيلات الافتراضي: {save_dir}")
            except OSError as e:
                self.log_message(f"خطأ في إنشاء مجلد التنزيلات: {e}")
                QMessageBox.warning(self, "خطأ", f"لم يتمكن من إنشاء مجلد التنزيلات: {save_dir}\n{e}")

    def load_config(self):
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                    if "save_dir" not in self.config: self.config["save_dir"] = self.DEFAULT_DOWNLOAD_DIR
                    if "format" not in self.config: self.config["format"] = "mp4"
                    if "quality" not in self.config: self.config["quality"] = "متوسطة"
                    if "subtitles" not in self.config: self.config["subtitles"] = False
                    return
        except Exception as e:
            print(f"خطأ في تحميل الإعدادات: {e}")
        self.config = {
            "save_dir": self.DEFAULT_DOWNLOAD_DIR, "format": "mp4",
            "quality": "متوسطة", "subtitles": False
        }

    def save_config(self):
        self.config["save_dir"] = self.dir_label.text()
        self.config["format"] = self.format_combo.currentText()
        self.config["quality"] = self.quality_combo.currentText()
        self.config["subtitles"] = self.subtitles_checkbox.isChecked()
        try:
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
            self.log_message("تم حفظ الإعدادات.")
        except Exception as e:
            self.log_message(f"خطأ في حفظ الإعدادات: {e}")

    def log_message(self, message):
        self.log_output.appendPlainText(message)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())


    def clear_url_and_list(self):
        self.url_entry.clear()
        self.video_list_widget.clear()
        self.all_videos_in_playlist = []
        self.playlist_title_for_download = None
        self.video_list_widget.setVisible(False)
        self.video_list_label.setVisible(False)
        self.select_all_button.setVisible(False)
        self.deselect_all_button.setVisible(False)
        self.status_label.setText("الحالة: جاهز")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.log_message("تم مسح حقل الرابط وقائمة المرئيات.")


    def select_all_videos(self):
        for i in range(self.video_list_widget.count()):
            self.video_list_widget.item(i).setSelected(True)

    def deselect_all_videos(self):
        for i in range(self.video_list_widget.count()):
            self.video_list_widget.item(i).setSelected(False)

    def fetch_video_info_threaded(self):
        url = self.url_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "تنبيه", "الرجاء إدخال رابط الميديا أولاً.")
            return

        self.status_label.setText("الحالة: جاري جلب معلومات المرئية...")
        self.log_message(f"بدء جلب المعلومات للرابط: {url}")
        self.fetch_info_button.setEnabled(False)
        self.download_button.setEnabled(False)

        self.worker = DownloadWorker(url, "", "", "", False)
        self.thread = QThread()
        self.worker.moveToThread(self.thread)

        self.worker.info_fetched_signal.connect(self.handle_video_info_fetched)
        self.worker.error_signal.connect(self.handle_error)
        self.worker.log_message_signal.connect(self.log_message)

        self.thread.started.connect(self.worker.run_get_info)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.info_fetched_signal.connect(lambda: (self.thread.quit() if self.thread else None))
        self.worker.error_signal.connect(lambda: (self.thread.quit() if self.thread else None))

        self.thread.start()

    def handle_video_info_fetched(self, result):
        self.fetch_info_button.setEnabled(True)
        self.download_button.setEnabled(True)

        videos = result.get("videos", [])
        self.playlist_title_for_download = result.get("playlist_title")

        self.all_videos_in_playlist = videos
        self.video_list_widget.clear()

        if not videos:
            self.status_label.setText("الحالة: لم يتم العثور على مرئيةهات.")
            self.log_message("لم يتم العثور على مرئيةهات في الرابط المقدم.")
            self.video_list_widget.setVisible(False)
            self.video_list_label.setVisible(False)
            self.select_all_button.setVisible(False)
            self.deselect_all_button.setVisible(False)
            return

        if len(videos) > 1 or self.playlist_title_for_download:
            self.video_list_label.setText(f"المرئيات في '{self.playlist_title_for_download or 'القائمة الحالية'}':")
            self.video_list_widget.setVisible(True)
            self.video_list_label.setVisible(True)
            self.select_all_button.setVisible(True)
            self.deselect_all_button.setVisible(True)
            for video in videos:
                item = QListWidgetItem(f"{video['title']}")
                item.setData(Qt.UserRole, video)
                self.video_list_widget.addItem(item)
            self.status_label.setText(f"الحالة: تم جلب {len(videos)} مرئية. حدد المطلوب واضغط تحميل.")
            self.log_message(f"تم عرض {len(videos)} مرئية في القائمة.")
        else:
            video = videos[0]
            self.video_list_widget.setVisible(False)
            self.video_list_label.setVisible(False)
            self.select_all_button.setVisible(False)
            self.deselect_all_button.setVisible(False)
            self.status_label.setText(f"الحالة: جاهز لتحميل '{video['title'][:50]}...'")
            self.log_message(f"تم جلب معلومات المرئية الواحد: {video['title']}")


    def select_directory(self):
        selected_dir = QFileDialog.getExistingDirectory(self, "اختر مجلد الحفظ", self.dir_label.text())
        if selected_dir:
            self.dir_label.setText(selected_dir)
            self.save_config()
            self.log_message(f"تم تحديد مجلد الحفظ: {selected_dir}")

    def start_download_threaded(self):
        url = self.url_entry.text().strip()
        if not url and not (self.video_list_widget.isVisible() and self.video_list_widget.selectedItems()):
            QMessageBox.warning(self, "تنبيه", "الرجاء إدخال رابط الميديا أو تحديد مرئيةهات من القائمة.")
            return

        if not self.ffmpeg_checked:
            if not check_ffmpeg_installed():
                QMessageBox.critical(self, "خطأ FFmpeg",
                                     "لم يتم العثور على FFmpeg. بعض الميزات مثل تحويل الصيغ قد لا تعمل بشكل صحيح. "
                                     "يرجى تثبيت FFmpeg وإضافته إلى متغيرات البيئة (PATH).")
                self.log_message("تحذير: FFmpeg غير مثبت أو غير موجود في PATH.")
            else:
                self.log_message("تم العثور على FFmpeg.")
            self.ffmpeg_checked = True

        selected_videos_to_download = []
        is_playlist_download = False

        if self.video_list_widget.isVisible() and self.video_list_widget.count() > 0:
            selected_items = self.video_list_widget.selectedItems()
            if not selected_items:
                QMessageBox.information(self, "معلومة", "الرجاء تحديد مرئية واحد على الأقل من القائمة للتحميل.")
                return
            for item in selected_items:
                selected_videos_to_download.append(item.data(Qt.UserRole))
            is_playlist_download = True
            self.log_message(f"تم تحديد {len(selected_videos_to_download)} مرئية من القائمة للتحميل.")

        elif self.all_videos_in_playlist and len(self.all_videos_in_playlist) == 1 and not self.playlist_title_for_download:
            selected_videos_to_download = self.all_videos_in_playlist
            self.log_message(f"سيتم تحميل المرئية الواحد الذي تم جلب معلوماته: {selected_videos_to_download[0]['title']}")

        elif not url: # إذا لم يكن هناك رابط ولا قائمة محددة
             QMessageBox.warning(self, "تنبيه", "الرجاء إدخال رابط الميديا أولاً.")
             return


        download_dir = self.dir_label.text()
        quality = self.quality_combo.currentText()
        file_type = self.format_combo.currentText()
        download_subtitles = self.subtitles_checkbox.isChecked()

        self.save_config()

        self.status_label.setText("الحالة: جاري التحضير للتحميل...")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.download_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.fetch_info_button.setEnabled(False)

        actual_playlist_title_for_worker = self.playlist_title_for_download if is_playlist_download else None

        self.worker = DownloadWorker(url, download_dir, quality, file_type, download_subtitles,
                                     selected_videos_to_download if selected_videos_to_download else None,
                                     actual_playlist_title_for_worker)
        self.thread = QThread()
        self.worker.moveToThread(self.thread)

        self.worker.progress_updated.connect(self.update_progress)
        self.worker.status_updated.connect(self.update_status)
        self.worker.download_finished_signal.connect(self.on_single_download_finished)
        self.worker.error_signal.connect(self.handle_error)
        self.worker.log_message_signal.connect(self.log_message)

        self.thread.started.connect(self.worker.run_download)
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.status_updated.connect(self.check_if_all_done)


        self.thread.start()

    def check_if_all_done(self, status_message):
        if "اكتملت جميع التحميلات المجدولة" in status_message or "تم إيقاف التحميل" in status_message:
            self.on_all_downloads_finished_or_stopped()


    def confirm_stop_download(self):
        reply = QMessageBox.question(self, "تأكيد الإيقاف",
                                     "هل أنت متأكد أنك تريد إيقاف التحميل الحالي؟",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.log_message("طلب المستخدم إيقاف التحميل.")
            stop_download_process()
            self.stop_button.setEnabled(False)
            self.status_label.setText("الحالة: جاري محاولة إيقاف التحميل...")


    def update_progress(self, value, filename):
        self.progress_bar.setValue(value)
        short_filename = os.path.basename(filename)
        if len(short_filename) > 30: # اختصار اسم الملف الطويل
            short_filename = short_filename[:27] + "..."

        if value < 100:
             self.progress_bar.setFormat(f"{short_filename} - %p%")
        else:
             self.progress_bar.setFormat(f"اكتمل: {short_filename}")


    def update_status(self, message):
        self.status_label.setText(f"الحالة: {message}")

    def on_single_download_finished(self, filename, success):
        if success:
            self.log_message(f"اكتمل تحميل '{filename}' بنجاح.")
        else:
            if not stop_event.is_set():
                 self.log_message(f"فشل تحميل أو تم إيقاف '{filename}'.")

        if self.thread and not self.thread.isRunning():
             self.on_all_downloads_finished_or_stopped()


    def on_all_downloads_finished_or_stopped(self):
        self.download_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.fetch_info_button.setEnabled(True)

        if stop_event.is_set():
            self.status_label.setText("الحالة: تم إيقاف التحميل.")
            self.log_message("العملية الكلية للتحميل توقفت.")
        else:
             self.log_message("العملية الكلية للتحميل انتهت.")

        if stop_event.is_set() or "فشل" in self.status_label.text() or "خطأ" in self.status_label.text():
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("%p%")

        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait()


    def handle_error(self, error_message):
        QMessageBox.critical(self, "خطأ", error_message)
        self.status_label.setText(f"الحالة: خطأ - {error_message[:100]}")

        self.download_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.fetch_info_button.setEnabled(True)

        if self.thread and self.thread.isRunning():
            self.thread.quit()


    def closeEvent(self, event):
        self.save_config()
        if self.thread and self.thread.isRunning():
            reply = QMessageBox.question(self, "تأكيد الخروج",
                                         "يوجد تحميل جاري. هل تريد حقاً الخروج؟ سيتم إيقاف التحميل.",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                stop_download_process()
                self.thread.quit()
                if not self.thread.wait(3000): # انتظر حتى 3 ثواني
                    self.log_message("لم يتمكن الخيط من الانتهاء في الوقت المحدد عند الإغلاق.")
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


if __name__ == '__main__':
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.RightToLeft) # تطبيق RTL على مستوى التطبيق

    main_win = YouTubeDownloaderApp()
    main_win.show()
    sys.exit(app.exec_())
