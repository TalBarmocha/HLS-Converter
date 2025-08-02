# 🎥 HLS Converter

HLS Converter is a lightweight, cross-platform desktop application for converting video files into **HLS format (`.m3u8`)** using **FFmpeg**.  
It supports:

- Drag-and-drop multiple video files  
- Batch parallel conversions  
- CRF quality control  
- Automatic folder output  
- Real-time progress and estimated time remaining  
- Built-in FFmpeg binaries (no manual install required)

---

## 🚀 Features

- ✅ Convert `.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv` to HLS (`.m3u8`).  
- ✅ Parallel batch processing (2 files simultaneously).  
- ✅ Drag & drop or browse file selection.  
- ✅ Adjustable video quality (CRF slider).  
- ✅ Standalone `.app` (Mac Intel & Apple Silicon) or `.exe` (Windows) – **no Python or FFmpeg installation needed**.  
- ✅ Custom application icon.  
- ✅ Automatic thumbnail generation from the middle frame.

---

## 📦 Installation

### 🔹 macOS (Apple Silicon & Intel)

1. [Download the latest release](https://github.com/TalBarmocha/HLS-Converter/releases).  
   - **`HLSConverter-MacOS_ARM.app.zip`** → for Apple Silicon (M1/M2/M3/M4).  
   - **`HLSConverter-MacOS_Intel.app.zip`** → for Intel-based Macs.  
2. Extract the `.zip` file and move **`HLSConverter.app`** to your `Applications` folder.  
3. On first launch, macOS may block the app because it's unsigned:
   - Go to **System Preferences → Security & Privacy → General**.
   - Click **Allow Anyway**, then reopen the app.

---

### 🔹 Windows

1. [Download the latest release](https://github.com/TalBarmocha/HLS-Converter/releases) (`HLSConverter-Windows.exe`).  
2. Double-click the `.exe` to start converting videos.  
   - No Python or FFmpeg installation is required; everything is bundled.

---

## 🛠️ Building from Source

If you prefer to build the application manually:

***(Python is needed for the build application)***

1. **Clone the repository:**

   ```bash
   git clone https://github.com/yourusername/HLS-Converter.git
   cd HLS-Converter
   ```

2. **Install Python dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Download FFmpeg binaries for all platforms:**  
   - From the `bin` folder get `ffmpeg` and `ffprobe` files for your platform.
   - Place the `ffmpeg` and `ffprobe` for your platform at the root of the `bin` folder:
     ```
     bin/
      ├─ ffmpeg (or .exe on Windows)
      ├─ ffprobe (or .exe on Windows)
     ```
   Each folder bin should contain both `ffmpeg` and `ffprobe` (or `.exe` on Windows).

4. **Build standalone apps:**

   - **macOS:**
     ```bash
     pyinstaller --onefile --windowed --icon=icon.icns --add-binary "bin/ffmpeg:bin" --add-binary "bin/ffprobe:bin" --hidden-import=tkinterdnd2 HLSconverter.py
     ```
   - **Windows:**
     ```bash
     pyinstaller --onefile --windowed --icon=icon.ico --add-binary "bin/ffmpeg:bin" --add-binary "bin/ffprobe:bin" --hidden-import=tkinterdnd2 HLSconverter.py
     ```
     

After the build process the portable application should be in the `dist` folder

---

## 📂 Output

- Each converted video gets its **own folder** inside the chosen output directory.
- Contains:
  - `output.m3u8`
  - `.ts` video segments for HLS streaming.
  - `thumbnail.jpg` preview image.

---

## 📝 License

MIT License – feel free to use and modify.
