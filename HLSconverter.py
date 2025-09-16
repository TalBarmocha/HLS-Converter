import sys
import os
import subprocess
import threading
import time
import signal
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from concurrent.futures import ThreadPoolExecutor
from tkinterdnd2 import TkinterDnD, DND_FILES

# Track all running ffmpeg processes so we can stop them on close
RUNNING_PROCS = set()
SHUTTING_DOWN = False

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


def _test_h264_encoder(encoder_name: str) -> bool:
    """
    Try a tiny hardware-encode to verify the encoder actually works.
    Encodes a few frames from the FFmpeg test source to a null muxer.
    Returns True if the encoder succeeds, False otherwise.
    """
    try:
        # Use null sink; keep it tiny and quiet
        cmd = [
            FFMPEG_BIN,
            "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
            "-t", "1",                     # ~1 second
            "-pix_fmt", "yuv420p",         # safe for h264
            "-c:v", encoder_name,
            "-f", "null", "-"              # write to stdout (ignored)
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return proc.returncode == 0
    except Exception:
        return False


def detect_gpu_encoder():
    """
    Detect the best available GPU encoder for H.264 and verify it with a tiny encode test.
    Falls back to CPU (libx264) if unsupported or the test fails.
    """
    # Default fallback
    fallback = "libx264"

    try:
        # List available encoders
        res = subprocess.run(
            [FFMPEG_BIN, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
        )
        if res.returncode != 0:
            return fallback
        encoders_txt = (res.stdout or "").lower()

        # Candidate order by platform
        candidates = []
        if sys.platform.startswith("darwin"):
            if "h264_videotoolbox" in encoders_txt:
                candidates.append("h264_videotoolbox")
        elif sys.platform.startswith("win"):
            # Prefer NVENC, then QSV, then AMF
            if "h264_nvenc" in encoders_txt:
                candidates.append("h264_nvenc")
            if "h264_qsv" in encoders_txt:
                candidates.append("h264_qsv")
            if "h264_amf" in encoders_txt:
                candidates.append("h264_amf")

        # Verify by actually encoding a few frames
        for enc in candidates:
            if _test_h264_encoder(enc):
                return enc

        # Nothing passed—fallback to CPU
        return fallback

    except Exception as e:
        print(f"GPU detection failed: {e}")
        return fallback



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

MAX_WORKERS = max((os.cpu_count() // 2 ), 2)  # Number of simultaneous conversions
controls_enabled = True
encoder = detect_gpu_encoder()


# -----------------------------
# Conversion workers (run in threads)
# -----------------------------

def convert_single_video(video_path, custom_output_dir, crf_value, total_duration_sum, start_time):
    """Convert a single video to HLS with YOUR exact FFmpeg settings and live progress."""
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

    # Paths
    output_file = os.path.join(output_dir, "output.m3u8")
    seg_pattern = os.path.join(output_dir, "output%0d.ts")

    # (Optional) thumbnail for your UI
    generate_thumbnail(video_path, output_dir)

    cmd = [
        FFMPEG_BIN,
        "-i", video_path,
        # Video
        "-c:v", encoder,
        "-profile:v", "high",
        "-level", "4.0",
        "-pix_fmt", "yuv420p",
    ]

    # ---- Rate control + preset per encoder ----

    if encoder == "h264_videotoolbox":
        # Apple GPU: -q:v (lower = better). Map CRF≈q:v.
        # CRF 16→~62, 18→~68, 20→~74, 23→~83, clamp to [55..95]
        qv = max(1, min(100,66 - 2 * (int(crf_value) - 16)))
        cmd += ["-q:v", str(qv)]

    elif encoder == "h264_nvenc":
        # NVIDIA: CQ + preset p5 ~ "fast"
        cq = max(0, min(51, int(crf_value) + 2))  # CRF16→CQ18 (range 0..51)
        cmd += ["-rc:v","vbr_hq","-cq",str(cq),"-b:v","0","-maxrate","0","-bufsize","0","-preset","p5"]

    elif encoder == "h264_qsv":
        # Intel Quick Sync: ICQ (CRF-like) uses -rc icq + -global_quality
        # Map CRF 16..28 to ICQ ~18..30 (shift +2 like NVENC)
        icq = max(1, min(51, int(crf_value) + 2))
        cmd += ["-preset", "fast", "-rc:v", "icq", "-global_quality", str(icq)]

    elif encoder == "h264_amf":
        # AMD AMF: CQP (constant QP). Use one QP for all frames or bias B-frames slightly.
        # Map CRF 16..28 to QP ~18..30 (shift +2 like NVENC)
        qp = max(0, min(51, int(crf_value) + 2))
        cmd += ["-quality", "balanced", "-rc", "cqp", "-qp_i", str(qp), "-qp_p", str(qp), "-qp_b", str(qp)]

    else:
        # Fallback (treat like CPU x264)
        cmd += ["-preset", "fast", "-crf", str(int(crf_value))]

    # ---- GOP / scene cut ----
    cmd += [
        "-threads", "0",
        "-g", "240",
        "-keyint_min", "240",
        "-sc_threshold", "0",
        # Audio
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-ar", "48000",
        # HLS
        "-f", "hls",
        "-hls_time", "10",
        "-hls_playlist_type", "vod",
        "-hls_segment_type", "mpegts",
        "-hls_flags", "independent_segments",
        "-hls_segment_filename", seg_pattern,
        output_file,
        # Progress
        "-progress", "pipe:1",
        "-nostats",
    ]


    # ---- spawn process in its own group/session for clean kill ----
    creationflags = 0
    preexec_fn = None
    if sys.platform.startswith("win"):
        # New process group on Windows
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        # New session on POSIX so we can os.killpg()
        import os as _os
        preexec_fn = _os.setsid

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                               bufsize=1, creationflags=creationflags, preexec_fn=preexec_fn)

    # Register
    RUNNING_PROCS.add(process)
    last_reported = 0

    for line in process.stdout:
        if SHUTTING_DOWN:
            break
        if "out_time_ms=" in line:
            value = line.strip().split("=")[1]
            if not value.isdigit():
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

                ui_async(progress_bar.configure, value=overall_percent)
                ui_async(progress_label.config, text=f"{overall_percent:.1f}%")
                ui_async(total_time_label.config, text=f"Total Estimated Time Left: {mins:02d}:{secs:02d}")

    process.wait()
    RUNNING_PROCS.discard(process)
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

def _kill_process_tree(p: subprocess.Popen):
    try:
        if p.poll() is not None:
            return  # already exited
        if sys.platform.startswith("win"):
            # Try gentle terminate, then kill
            try:
                p.terminate()
                p.wait(timeout=1.0)
            except Exception:
                pass
            try:
                p.kill()
            except Exception:
                pass
        else:
            # POSIX: kill the whole group/session
            try:
                import os as _os
                _os.killpg(p.pid, signal.SIGTERM)
            except Exception:
                pass
            # last resort
            try:
                p.terminate()
            except Exception:
                pass
    except Exception:
        pass

def on_close():
    # Prevent threads from posting UI / messageboxes
    global SHUTTING_DOWN
    SHUTTING_DOWN = True

    # Disable all controls to avoid new work
    try:
        disable_controls()
    except Exception:
        pass

    # Kill any running ffmpeg processes
    procs = list(RUNNING_PROCS)
    for p in procs:
        _kill_process_tree(p)

    # Give them a brief moment to exit cleanly
    t0 = time.time()
    for p in procs:
        try:
            p.wait(timeout=max(0.0, 1.0 - (time.time() - t0)))
        except Exception:
            pass

    # Tear down the UI and exit
    try:
        root.quit()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass

    # As a final guard (threads may still be alive), force-exit the interpreter
    os._exit(0)



# -----------------------------
# GUI Setup
# -----------------------------
root = TkinterDnD.Tk()
root.protocol("WM_DELETE_WINDOW", on_close)
root.title("Video to HLS Converter")
root.geometry("600x760")
root.resizable(True, True)

# Bind the UI async dispatcher now that root exists
ui_async = lambda fn, *args, **kwargs: root.after(0, lambda: fn(*args, **kwargs))

# (Optional) verify ffmpeg/ffprobe existence immediately
verify_binaries()

frame = tk.Frame(root)
frame.pack(expand=True, fill="both", pady=10)

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
