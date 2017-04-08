# gstreamer-player

Audio player based on [gstreamer](https://github.com/GStreamer/gst-python).

## Install

### Prerequisites

Debian/Ubuntu/Rasbian:
```bash
sudo apt-get install python-gst-1.0 \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly \
    gstreamer1.0-tools
```

Redhat/Centos/Fedora:
```bash
sudo yum install -y python-gstreamer1 gstreamer1-plugins-good \
    gstreamer1-plugins-ugly
```

If you're using a Python virtual environment, symlink the system Python's `gst` into your env's `site_packages`.

### Python module

`pip install gstreamer-player`

## Usage

```python
from gsp import GstreamerPlayer

player = GstreamerPlayer(None)

player.queue("/path/to/audio.mp3")
```
