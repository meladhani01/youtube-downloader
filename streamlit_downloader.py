import streamlit as st
import yt_dlp
import os
import re
from pathlib import Path

st.set_page_config(
    page_title="برنامج تحميل الميديا",
    page_icon="📥",
    layout="wide"
)

# Custom CSS
st.markdown("""
<style>
    .stApp {
        background-color: #1e1f22;
        color: #dcdde1;
    }
    .stButton>button {
        background-color: #0078d4;
        color: white;
        border-radius: 5px;
        padding: 10px 20px;
        font-weight: bold;
    }
    .stButton>button:hover {
        background-color: #005a9e;
    }
</style>
""", unsafe_allow_html=True)

def sanitize_filename(filename):
    filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
    filename = re.sub(r'\s+', " ", filename).strip()
    if len(filename) > 150:
        filename = filename[:147] + "..."
    return filename

def get_format_options(quality, file_type):
    quality_map = {
        'منخفضة': 'best[height<=360]',
        'متوسطة': 'best[height<=720]',
        'عالية': 'best[height<=1080]/bestvideo[height<=1080]+bestaudio/best'
    }
    quality_value_video = quality_map.get(quality, 'best[height<=720]')
    
    if file_type == 'mp3':
        return 'bestaudio/best'
    else:
        return f'{quality_value_video}[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'

def get_videos_info(url):
    ydl_opts = {
        "quiet": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "simulate": True,
        "no_warnings": True,
        "socket_timeout": 20,
    }
    videos = []
    playlist_title = None
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info:
                raise Exception("لم يتم العثور على معلومات للمرئية.")
            
            if 'entries' in info and info['entries']:
                playlist_title = info.get("title", "قائمة تشغيل")
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
            elif 'id' in info:
                video_id = info.get('id')
                video_title = info.get('title', 'مرئية بدون عنوان')
                if video_id:
                    videos.append({
                        "title": video_title,
                        "url": info.get('webpage_url', f"https://www.youtube.com/watch?v={video_id}"),
                        "id": video_id
                    })
            
            return {"videos": videos, "playlist_title": playlist_title}
    except Exception as e:
        raise Exception(f"خطأ في جلب المعلومات: {str(e)}")

def download_video(url, quality, file_type, progress_placeholder):
    output_dir = "downloads"
    os.makedirs(output_dir, exist_ok=True)
    
    output_template = os.path.join(output_dir, '%(title)s.%(ext)s')
    
    ydl_opts = {
        'format': get_format_options(quality, file_type),
        'outtmpl': output_template,
        'quiet': False,
        'no_warnings': False,
        'progress_hooks': [],
    }
    
    if file_type == 'mp3':
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif file_type == 'mp4':
        ydl_opts['merge_output_format'] = 'mp4'
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if file_type == 'mp3':
                filename = filename.rsplit('.', 1)[0] + '.mp3'
            
            return filename
    except Exception as e:
        raise Exception(f"خطأ في التحميل: {str(e)}")

# Main UI
st.title("📥 برنامج تحميل الميديا")
st.markdown("---")

# Sidebar
with st.sidebar:
    st.header("⚙️ الإعدادات")
    
    file_type = st.selectbox(
        "الصيغة:",
        ["mp4", "mp3"],
        index=0
    )
    
    quality = st.selectbox(
        "الجودة:",
        ["منخفضة", "متوسطة", "عالية"],
        index=1
    )
    
    st.markdown("---")
    st.info("💡 يدعم التطبيق YouTube وغيرها من المنصات")

# Main content
url = st.text_input("🔗 رابط الميديا:", placeholder="أدخل رابط المرئية أو قائمة التشغيل هنا")

col1, col2 = st.columns([1, 1])

with col1:
    fetch_info = st.button("📋 جلب المعلومات", use_container_width=True)

with col2:
    clear_btn = st.button("🗑️ مسح", use_container_width=True)

if clear_btn:
    st.session_state.clear()
    st.rerun()

if fetch_info and url:
    with st.spinner("جاري جلب المعلومات..."):
        try:
            info = get_videos_info(url)
            st.session_state['video_info'] = info
            st.success(f"✅ تم جلب {len(info['videos'])} مرئية")
        except Exception as e:
            st.error(f"❌ {str(e)}")

# Display video list if available
if 'video_info' in st.session_state:
    info = st.session_state['video_info']
    
    if info['playlist_title']:
        st.subheader(f"📁 {info['playlist_title']}")
    
    if len(info['videos']) > 1:
        selected_videos = st.multiselect(
            "اختر المرئيات للتحميل:",
            options=range(len(info['videos'])),
            format_func=lambda x: info['videos'][x]['title'],
            default=list(range(len(info['videos'])))
        )
    else:
        selected_videos = [0]
        st.info(f"📹 {info['videos'][0]['title']}")

    if st.button("⬇️ بدء التحميل", use_container_width=True, type="primary"):
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        total = len(selected_videos)
        
        for idx, video_idx in enumerate(selected_videos):
            video = info['videos'][video_idx]
            status_text.text(f"جاري تحميل ({idx+1}/{total}): {video['title'][:50]}...")
            
            try:
                filename = download_video(video['url'], quality, file_type, status_text)
                
                # Provide download link
                if os.path.exists(filename):
                    with open(filename, 'rb') as f:
                        st.download_button(
                            label=f"💾 تحميل: {os.path.basename(filename)}",
                            data=f,
                            file_name=os.path.basename(filename),
                            mime="video/mp4" if file_type == "mp4" else "audio/mpeg"
                        )
                    st.success(f"✅ اكتمل: {video['title']}")
                
            except Exception as e:
                st.error(f"❌ خطأ في {video['title']}: {str(e)}")
            
            progress_bar.progress((idx + 1) / total)
        
        status_text.text("✅ اكتملت جميع التحميلات!")

st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #96989d;'>
    <p>تم التطوير باستخدام Streamlit | يدعم YouTube وغيرها</p>
</div>
""", unsafe_allow_html=True)
