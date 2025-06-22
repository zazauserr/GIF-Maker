# 🎬 GIF Maker

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)

A simple desktop application for creating GIF animations from videos by URL (YouTube and other platforms).

## ✨ Features

- 🎥 **Video download by URL** - supports YouTube and many other platforms via yt-dlp
- ✂️ **Fragment selection** - precise selection of the desired video part for conversion
- ⚙️ **Parameter adjustment** - control width, FPS, GIF quality
- 👀 **Preview** - check the result before saving
- 🏷️ **Automatic watermarks** - brand your GIFs
- 🖥️ **User-friendly interface** - intuitive controls

## 📸 Screenshots

<!-- Add screenshots of your application here -->
<!-- ![Main window](screenshots/main-window.png) -->
<!-- ![GIF settings](screenshots/gif-settings.png) -->

## 🚀 Quick Start

### Requirements

- Windows (automatic installation support)
- Internet connection for downloading Python, dependencies, and FFmpeg

### Installation

1. **Download or clone the repository:**
   ```bash
   git clone https://github.com/zazauserr/GIF-Maker.git
   cd GIF-Maker
   ```

2. **If you don't have Python - run:**
   ```
   install_python.bat
   ```

3. **Install dependencies and FFmpeg:**
   ```
   install_dependencies.bat
   ```

4. **Launch GIF Maker:**
   ```
   python main.py
   ```

That's it! The application is ready to use 🚀

### 📦 Dependencies

The application uses the following libraries:
- `yt-dlp` - for downloading videos from various platforms
- `Pillow` - for image processing and GIF creation
- `requests` - for HTTP requests
- `wmi` - for system information (Windows only)
- `FFmpeg` - for video processing

## 🎯 Usage

1. **Launch the application**
2. **Paste video URL** (YouTube, Vimeo, etc.)
3. **Select fragment** for conversion
4. **Adjust GIF parameters:**
   - Width (pixels)
   - Frame rate (FPS)
   - Quality
5. **Preview the result**
6. **Save GIF**

## 🔧 Configuration Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| Width | Output GIF width in pixels | 480px |
| FPS | Frame rate | 15 |
| Quality | Compression quality (1-100) | 85 |

## 🛠️ Development

### Project Structure
```
GIF-Maker/
├── main.py                    # Main application file
├── install_python.bat        # Python auto-installer (Windows)
├── requirements.bat  # Dependencies and FFmpeg auto-installer (Windows)
├── README.md                # Documentation
```

## 📋 Roadmap

- [ ] Batch processing support
- [ ] Additional output formats (WebP, APNG)
- [ ] Cloud storage integration
- [ ] Advanced effects and filters
- [ ] Dark theme interface

## 🔧 Troubleshooting

### Common Issues

**Python not found:**
- Use `install_python.bat` on Windows
- Make sure Python is added to PATH

**FFmpeg not found:**
- The `install_dependencies.bat` script automatically installs FFmpeg
- Check if the installation completed successfully

**Video download errors:**
- Check internet connection
- Make sure the video URL is correct and accessible
- Some sites may block downloads

**GIF creation issues:**
- Verify that FFmpeg is properly installed
- Ensure sufficient disk space
- Try reducing GIF size or quality

## 🐛 Bug Reports

If you find a bug, please:

1. Check the [list of known issues](https://github.com/zazauserr/GIF-Maker/issues)
2. If the issue is new, create a [new issue](https://github.com/zazauserr/GIF-Maker/issues/new)
3. Include:
   - Python version
   - Operating system
   - Detailed problem description
   - Screenshots (if applicable)

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 👨‍💻 Author

**zazauserr** - [GitHub](https://github.com/zazauserr)

---
