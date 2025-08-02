# 🎥 HLS Converter

HLS Converter is a lightweight, cross-platform desktop application for converting video files into **HLS format (`.m3u8`)** using **FFmpeg**.  
It supports:

- Drag-and-drop multiple video files  
- Batch parallel conversions  
- CRF quality control  
- Automatic folder output  
- Real-time progress and estimated time remaining  

---

## 🚀 Features

- ✅ Convert `.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv` to HLS (`.m3u8`).  
- ✅ Parallel batch processing (2 files simultaneously).  
- ✅ Drag & drop or browse file selection.  
- ✅ Adjustable video quality (CRF slider).  
- ✅ Standalone `.app` (Mac) or `.exe` (Windows) – no Python needed.  
- ✅ Custom application icon.  

---

## 📦 Installation

### 🔹 macOS

1. [Download the latest release](https://github.com/TalBarmocha/HLS-Converter/releases) (`HLSConverter.app.zip`).  
2. Extract the `.zip` and move **`HLSConverter.app`** to your `Applications` folder.  
3. Install **FFmpeg** (required):  

   ```bash
   brew install ffmpeg
   ```
4. On first launch, macOS may block the app because it's unsigned:
   - Go to **System Preferences → Security & Privacy → General**.
   - Click **Allow Anyway**, then reopen the app.

---

### 🔹 Windows

1. [Download the latest release](https://github.com/TalBarmocha/HLS-Converter/releases) (`HLSConverter.exe`).  
2. Install **FFmpeg**:
   - Download the static build from [ffmpeg.org](https://ffmpeg.org/download.html#build-windows).
   - Extract it and add the `bin` folder to your system **PATH**.  
3. Double-click **`HLSConverter.exe`** to start converting videos.

---

## 🛠️ Building from Source

If you prefer to build the application manually:

1. **Clone the repository:**

   ```bash
   git clone https://github.com/yourusername/HLS-Converter.git
   cd HLS-Converter
   ```

2. **Install Python dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Install FFmpeg**  
   - macOS:  
     ```bash
     brew install ffmpeg
     ```
   - Windows: see above.

4. **Run the app:**

   ```bash
   python HLSconverter.py
   ```

5. **Build standalone app (optional):**
   - **macOS:**  
     ```bash
     pyinstaller --onefile --windowed --icon=icon.icns --name "HLSConverter" HLSconverter.py
     ```
   - **Windows:**  
     ```bash
     pyinstaller --onefile --windowed --icon=icon.ico --name "HLSConverter" HLSconverter.py
     ```
   - The result will be in the `dist` folder.

---

## 📂 Output

- Each converted video gets its **own folder** inside the chosen output directory.
- Contains:
  - `output.m3u8`
  - `.ts` video segments for HLS streaming.

---

## 📝 License

MIT License – feel free to use and modify.
