# GIF Maker

A simple desktop application for creating GIF animations from videos by URL (YouTube, etc.).

## Features

* Upload video by URL using `yt-dlp`.
* Selecting a video fragment to convert.
* Adjust GIF parameters: width, FPS, quality.
* Preview the created GIF animation.
* Automatic watermarking.

## Installation and startup

1.  Make sure you have Python 3 installed.
2.  Clone the repository:
    ```
 git clone [https://github.com/ВАШ_НИК/GifStudioPro.git](https://github.com/ВАШ_НИК/GifStudioPro.git)
 ```
3.  Go to the project folder:
 ```
 cd GIF-Maker
 ```
4.  The application itself will check and suggest to install missing dependencies (`yt-dlp`, `Pillow`, `requests`, `wmi`).
5.  `FFmpeg` is required for correct operation. The application will try to find it automatically. If it fails, it will ask for the path to `ffmpeg.exe`.
6.  Run the application:
 `` ``
 python gifmaker.py
