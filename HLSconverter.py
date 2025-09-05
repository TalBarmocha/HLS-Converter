import sys
import os
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from concurrent.futures import ThreadPoolExecutor
from tkinterdnd2 import TkinterDnD, DND_FILES

# Detect the correct base folder (works for PyInstaller and normal Python)
BASE_DIR = getattr(sys, '_MEIPASS', os.path.abspath("."))

# Detect platform-specific binary names
if sys.platform.startswith("win"):
    FFMPEG_BIN = os.path.join(BASE_DIR, "bin", "ffmpeg.exe")
    FFPROBE_BIN = os.path.join(BASE_DIR, "bin", "ffprobe.exe")
else:
    FFMPEG_BIN = os.path.join(BASE_DIR, "bin", "ffmpeg")
    FFPROBE_BIN = os.path.join(BASE_DIR, "bin", "ffprobe")

# -----------------------------
# Helpers & preflight checks
# -----------------------------

def verify_binaries():
    """Ensure ffmpeg/ffprobe exist and are executable; otherwise exit gracefully."""
    missing = []
    for b in (FFMPEG_BIN, FFPROBE_BIN):
        if not os.path.isfile(b):
            missing.append(b)
        elif not os.access(b, os.X_OK):
            try:
                os.chmod(b, 0o755)
            except Exception:
                missing.append(b)
    if missing:
        messagebox.showerror(
            "Missing Binaries",
            "Required ffmpeg/ffprobe not found or not executable:\n" + "\n".join(missing)
        )
        root.destroy()


def _clean_path(p: str) -> str:
    """Normalize incoming paths (especially from TkinterDnD) without stripping backslashes."""
    if not isinstance(p, str):
        return ""
    p = p.strip()
    # TkinterDnD may wrap paths with braces or quotes if they contain spaces
    if (p.startswith("{") and p.endswith("}")) or (p.startswith('"') and p.endswith('"')):
        p = p[1:-1]
    return os.path.normpath(p)


# Thread-safe UI updater (call from worker threads)
# Will be bound after `root` is created
ui_async = None


# -----------------------------
# FFmpeg utilities
# -----------------------------

def generate_thumbnail(input_path, output_dir):
    """Generate a thumbnail image from the video at 25% of its duration."""
    try:
        duration = get_video_duration(input_path)
        if duration <= 0:
            return  # skip silently
        quarter_time = duration * 0.25
        seek_str = time.strftime("%H:%M:%S", time.gmtime(quarter_time))

        thumbnail_path = os.path.join(output_dir, "thumbnail.jpg")
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-ss", seek_str,         # Seek BEFORE input for fast seeking
            "-i", input_path,
            "-frames:v", "1",
            thumbnail_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as e:
        print(f"Thumbnail generation failed for {input_path}: {e}")


def detect_gpu_encoder():
    """Detect the best available GPU encoder for H.264."""
    try:
        result = subprocess.run(
            [FFMPEG_BIN, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        if result.returncode != 0:
            return "libx264"
        encoders = (result.stdout or "").lower()

        if sys.platform.startswith("darwin"):
            if "h264_videotoolbox" in encoders:
                return "h264_videotoolbox"
        elif sys.platform.startswith("win"):
            if "h264_nvenc" in encoders:
                return "h264_nvenc"
            elif "h264_qsv" in encoders:
                return "h264_qsv"
            elif "h264_amf" in encoders:
                return "h264_amf"

        return "libx264"
    except Exception as e:
        print(f"GPU detection failed: {e}")
        return "libx264"


def gpu_type_to_string():
    """Convert GPU encoder type to a human-readable string."""
    gpu = detect_gpu_encoder()
    if gpu == "h264_videotoolbox":
        return "Apple VideoToolbox"
    elif gpu == "h264_nvenc":
        return "NVIDIA NVENC"
    elif gpu == "h264_qsv":
        return "Intel Quick Sync"
    elif gpu == "h264_amf":
        return "AMD AMF"
    elif gpu == "libx264":
        return "Software (libx264)"
    else:
        return "Unknown GPU Encoder"


def get_codecs(video_path):
    """Get the video and audio codecs of the input video file."""
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        video_codec = (result.stdout or "").strip().lower()

        result = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        audio_codec = (result.stdout or "").strip().lower()

        return video_codec or None, audio_codec or None
    except Exception as e:
        print(f"Failed to get codecs for {video_path}: {e}")
        return None, None


def get_video_duration(video_path):
    """Get the duration of the video file in seconds (float). Returns 0 on failure."""
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )
        return float((result.stdout or "0").strip())
    except Exception:
        return 0


# -----------------------------
# Global state
# -----------------------------
selected_videos = []
processed_duration_total = [0]
lock = threading.Lock()

MAX_WORKERS = 2  # Number of simultaneous conversions
controls_enabled = True
encoder = detect_gpu_encoder()


# -----------------------------
# Conversion workers (run in threads)
# -----------------------------

def convert_single_video(video_path, custom_output_dir, crf_value, total_duration_sum, start_time):
    """Convert a single video to HLS with live progress updates."""
    duration = get_video_duration(video_path)
    if duration <= 0:
        return video_path, False

    video_name = os.path.splitext(os.path.basename(video_path))[0]
    base_output_dir = custom_output_dir.strip() if custom_output_dir.strip() else os.path.dirname(video_path)
    output_dir = os.path.join(base_output_dir, video_name)

    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        print(f"Failed to create output dir {output_dir}: {e}")
        return video_path, False

    output_file = os.path.join(output_dir, "output.m3u8")
    video_codec, audio_codec = get_codecs(video_path)
    generate_thumbnail(video_path, output_dir)

    v = (video_codec or "").lower()
    a = (audio_codec or "").lower()
    use_remux = v.startswith("h264") and a == "aac"

    if use_remux:
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", video_path,
            "-c", "copy",
            "-start_number", "0",
            "-hls_time", "10",
            "-hls_list_size", "0",
            "-f", "hls",
            output_file,
            "-progress", "pipe:1",
            "-nostats"
        ]
    else:
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", video_path,
            "-c:v", encoder,
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2",
            "-start_number", "0",
            "-hls_time", "10",
            "-hls_list_size", "0",
            "-f", "hls",
            output_file,
            "-progress", "pipe:1",
            "-nostats"
        ]
        # Add optional parameters only if using CPU
        if encoder == "libx264":
            cmd.insert(cmd.index("-c:v") + 2, "-preset")
            cmd.insert(cmd.index("-preset") + 1, "fast")
            cmd.insert(cmd.index("-preset") + 2, "-crf")
            cmd.insert(cmd.index("-crf") + 1, str(int(crf_value)))
        else:
            cmd.insert(cmd.index("-c:v") + 2, "-b:v")
            cmd.insert(cmd.index("-b:v") + 1, "5000k")

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, bufsize=1)
    last_reported = 0

    for line in process.stdout:
        if "out_time_ms=" in line:
            value = line.strip().split("=")[1]
            if not value.isdigit():  # Skip 'N/A' or invalid values
                continue
            processed_seconds = int(value) / 1_000_000
            increment = max(0, processed_seconds - last_reported)
            last_reported = processed_seconds

            with lock:
                processed_duration_total[0] = min(total_duration_sum, processed_duration_total[0] + increment)
                overall_percent = (processed_duration_total[0] / total_duration_sum) * 100 if total_duration_sum else 0
                elapsed = time.time() - start_time
                speed = processed_duration_total[0] / elapsed if elapsed > 0 else 0
                remaining = (total_duration_sum - processed_duration_total[0]) / speed if speed > 0 else 0
                mins, secs = divmod(max(0, int(remaining)), 60)

                # Update UI from main thread
                ui_async(progress_bar.configure, value=overall_percent)
                ui_async(progress_label.config, text=f"{overall_percent:.1f}%")
                ui_async(total_time_label.config, text=f"Total Estimated Time Left: {mins:02d}:{secs:02d}")

    process.wait()
    return video_path, process.returncode == 0


def convert_all_videos_parallel(custom_output_dir, crf_value):
    """Convert all selected videos in parallel with progress updates."""
    try:
        total_duration = sum(get_video_duration(v) for v in selected_videos)
        processed_duration_total[0] = 0
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(convert_single_video, v, custom_output_dir, crf_value, total_duration, start_time)
                       for v in selected_videos]

            for future in futures:
                video_path, success = future.result()
                if not success:
                    ui_async(messagebox.showerror, "Error", f"Failed: {os.path.basename(video_path)}")

        ui_async(messagebox.showinfo, "Done", "All conversions completed!")

    finally:
        # Reset UI from main thread
        ui_async(enable_controls)
        ui_async(clear_all_files)
        ui_async(progress_bar.configure, value=0)
        ui_async(progress_label.config, text="0%")
        ui_async(total_time_label.config, text="Total Estimated Time Left: 00:00")


# -----------------------------
# UI Actions
# -----------------------------

def start_conversion():
    """Start the conversion process for all selected videos."""
    if not selected_videos:
        messagebox.showerror("Error", "Please select at least one video!")
        return

    output_dir = output_entry.get().strip()
    if output_dir:
        try:
            os.makedirs(output_dir, exist_ok=True)
            # quick writability probe
            test_path = os.path.join(output_dir, ".write_test")
            with open(test_path, "w") as f:
                f.write("ok")
            os.remove(test_path)
        except Exception as e:
            messagebox.showerror("Error", f"Output folder is not writable:\n{output_dir}\n\n{e}")
            return

    crf_value = quality_slider.get()
    disable_controls()
    threading.Thread(target=convert_all_videos_parallel, args=(output_dir, crf_value), daemon=True).start()


def add_files(files):
    """Add files to the list of selected videos (robust to Hebrew/Unicode paths)."""
    if not controls_enabled:
        return
    added = False
    for raw in files:
        f = _clean_path(raw)
        if f and os.path.isfile(f) and f not in selected_videos:
            selected_videos.append(f)
            added = True
            add_file_row(f)
    if added and not output_entry.get() and selected_videos:
        folder_path = os.path.dirname(selected_videos[0])
        output_entry.insert(0, folder_path)


def add_file_row(file_path):
    """Add a row to the file list frame for the given file path."""
    row = tk.Frame(file_list_frame, bg=file_list_frame.cget("bg"))
    row.pack(fill="x", pady=2)

    name_label = tk.Label(row, text=os.path.basename(file_path), anchor="w")
    name_label.pack(side="left", padx=5)

    rm_btn = tk.Button(
        row, text="❌", command=lambda: remove_file(file_path, row),
        fg="red", font=("Arial", 11), relief="flat", bd=0,
        highlightthickness=0, bg=row.cget("bg"), activebackground=row.cget("bg"),
        cursor="hand2"
    )
    rm_btn.pack(side="right", padx=5)


def remove_file(file_path, row):
    """Remove a file from the list of selected videos."""
    if not controls_enabled:
        return
    if file_path in selected_videos:
        selected_videos.remove(file_path)
    row.destroy()


def clear_all_files():
    """Clear all selected files and the file list frame."""
    selected_videos.clear()
    for widget in file_list_frame.winfo_children():
        widget.destroy()


def drop(event):
    """Handle file drop events."""
    if controls_enabled:
        files = root.tk.splitlist(event.data)
        add_files(files)


def browse_files():
    """Open a file dialog to select video files."""
    if not controls_enabled:
        return
    files = filedialog.askopenfilenames(
        filetypes=[("Video Files", "*.mp4 *.m4v *.avi *.mkv *.mov *.flv *.wmv *.ts *.webm")]
    )
    if files:
        add_files(files)


def browse_output_folder():
    """Open a folder dialog to select the output folder."""
    if not controls_enabled:
        return
    folder = filedialog.askdirectory()
    if folder:
        output_entry.delete(0, tk.END)
        output_entry.insert(0, folder)


# Enable/disable controls

def disable_controls():
    """Disable all controls to prevent user interaction during conversion."""
    global controls_enabled
    controls_enabled = False
    browse_btn.config(state="disabled")
    clear_all_btn.config(state="disabled")
    browse_output_btn.config(state="disabled")
    quality_slider.config(state="disabled")
    convert_btn.config(state="disabled")


def enable_controls():
    """Enable all controls after conversion is done."""
    global controls_enabled
    controls_enabled = True
    browse_btn.config(state="normal")
    clear_all_btn.config(state="normal")
    browse_output_btn.config(state="normal")
    quality_slider.config(state="normal")
    convert_btn.config(state="normal")


class ScrollableFrame(tk.Frame):
    def __init__(self, parent, height=220, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, height=height)
        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas)

        self.inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")
        ))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vscroll.set)

        # Layout
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vscroll.pack(side="right", fill="y")

        # Mouse wheel support (Windows/macOS/Linux)
        self._bind_mousewheel(self.canvas)

    def _bind_mousewheel(self, widget):
        # Bind only when pointer is over the widget; unbind on leave
        widget.bind("<Enter>", lambda e: widget.bind("<MouseWheel>", self._on_mousewheel))
        widget.bind("<Leave>", lambda e: widget.unbind("<MouseWheel>"))
        # Some X11 builds use Button-4/5
        widget.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        widget.bind("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

    def _on_mousewheel(self, event):
        # On Windows event.delta is multiples of 120; on Linux often ±1
        delta = -1 if event.delta > 0 else 1
        if abs(event.delta) >= 120:
            delta = -int(event.delta / 120)  # normalize Windows steps
        self.canvas.yview_scroll(delta, "units")

    @property
    def content(self):
        return self.inner


# -----------------------------
# GUI Setup
# -----------------------------
root = TkinterDnD.Tk()
root.title("Video to HLS Converter")
root.geometry("650x800")
root.resizable(True, True)

# Bind the UI async dispatcher now that root exists
ui_async = lambda fn, *args, **kwargs: root.after(0, lambda: fn(*args, **kwargs))

# (Optional) verify ffmpeg/ffprobe existence immediately
verify_binaries()

frame = tk.Frame(root)
frame.pack(expand=True, fill="both", pady=10)

label = tk.Label(frame, text="Drag and Drop Video Files Here", pady=10)
label.pack(pady=5)

drop_area = tk.Label(frame, text="📂 Drop Files Here", relief="solid", width=60, height=6)
drop_area.pack(pady=10)
drop_area.drop_target_register(DND_FILES)
drop_area.dnd_bind('<<Drop>>', drop)

browse_btn = tk.Button(frame, text="Browse Files", command=browse_files, width=15, height=1)
browse_btn.pack(anchor="center", padx=15, pady=5)

# File list (scrollable)
file_list_container = ScrollableFrame(frame, height=150)
file_list_container.pack(pady=5, fill="both", expand=False)
file_list_frame = file_list_container.content

clear_all_btn = tk.Button(frame, text="Clear All", command=clear_all_files, width=10)
clear_all_btn.pack(pady=5)

output_label = tk.Label(frame, text="Base Output Folder:")
output_label.pack(pady=5)

output_frame = tk.Frame(frame)
output_frame.pack(pady=5)

output_entry = tk.Entry(output_frame, width=40)
output_entry.pack(side="left", padx=5)

browse_output_btn = tk.Button(output_frame, text="Select", command=browse_output_folder, width=8)
browse_output_btn.pack(side="right", padx=5)

# Quality slider
quality_label = tk.Label(frame, text="Quality (CRF): 16")
quality_label.pack(pady=10)
quality_slider = tk.Scale(frame, from_=16, to=28, orient="horizontal", length=300, tickinterval=2,
                          command=lambda val: quality_label.config(text=f"Quality (CRF): {val}"))
quality_slider.set(16)
quality_slider.pack(pady=5)

quality_hint = tk.Label(frame, text="Lower CRF = Better Quality (16=Best, 28=Low)", fg="gray")
quality_hint.pack(pady=2)

gpu_type_label = tk.Label(frame, text="GPU Encoder Detected: " + gpu_type_to_string())
gpu_type_label.pack(pady=5)

convert_btn = tk.Button(frame, text="CONVERT ALL", command=start_conversion, width=20, height=2)
convert_btn.pack(pady=10)

progress_bar = ttk.Progressbar(frame, length=500, mode='determinate')
progress_bar.pack(pady=5)
progress_label = tk.Label(frame, text="0%")
progress_label.pack()

total_time_label = tk.Label(frame, text="Total Estimated Time Left: 00:00", fg="gray")
total_time_label.pack(pady=2)

root.mainloop()
