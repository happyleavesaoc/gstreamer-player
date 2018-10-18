"""Gstreamer API."""

import logging
import multiprocessing
import os
import queue
import time
import urllib
import urllib.request

import gi  # pylint: disable=import-error
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst  # pylint: disable=import-error,wrong-import-position
import mutagen  # pylint: disable=wrong-import-position


Gst.init(None)

_LOGGER = logging.getLogger(__name__)
_FORMAT_TIME = Gst.Format(Gst.Format.TIME)
_NANOSEC_MULT = 10 ** 9

STATE_IDLE = 'idle'
STATE_PLAYING = 'playing'
STATE_PAUSED = 'paused'
TASK_PLAY = 'play'
TASK_PAUSE = 'pause'
TASK_STOP = 'stop'
TASK_MEDIA = 'media'
TASK_SET_POSITION = 'set_position'
TASK_SET_VOLUME = 'set_volume'
ATTR_STATE = 'state'
ATTR_VOLUME = 'volume'
ATTR_POSITION = 'position'
ATTR_DURATION = 'duration'
ATTR_URI = 'uri'
ATTR_ARTIST = 'artist'
ATTR_ALBUM = 'album'
ATTR_TITLE = 'title'
PROP_VOLUME = 'volume'
PROP_URI = 'uri'
PROP_CURRENT_URI = 'current-uri'
PROP_AUDIO_SINK = 'audio-sink'
TAG_TITLE = 'title'
TAG_ARTIST = 'artist'
TAG_ALBUM = 'album'


class GstreamerProcess(multiprocessing.Process):
    """Gstreamer process.

    Must be encapsulated as a process because the GLib
    main loop is required to have control and Python GIL
    threads don't play nice with GLib.
    """

    def __init__(self, manager, task_queue, media_queue, pipeline_string):
        """Initialize process."""
        super(GstreamerProcess, self).__init__()
        self._state = None
        self._tags = {}
        self._manager = manager
        self._task_queue = task_queue
        self._media_queue = media_queue
        self.state = STATE_IDLE
        self._player = Gst.ElementFactory.make('playbin', 'player')
        if pipeline_string:
            sink = Gst.parse_bin_from_description(pipeline_string, True)
            self._player.set_property(PROP_AUDIO_SINK, sink)
        bus = self._player.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self._on_message)

    def run(self):
        """Run the process.

        Iterate the GLib main loop and process the task queue.
        """
        pass
        """
        loop = GLib.MainLoop()
        context = loop.get_context()
        while True:
            time.sleep(0.1)
            if context.pending():
                context.iteration()
                self._manager[ATTR_POSITION] = self._position()
            try:
                method, args = self._task_queue.get(False)
                getattr(self, method)(**args)
            except queue.Empty:
                pass
            if self.state != STATE_IDLE:
                continue
            try:
                uri = self._media_queue.get(False)
                self.media(uri)
            except queue.Empty:
                pass
        """

    def media(self, uri):
        """Play a media file."""
        try:
            local_path, _ = urllib.request.urlretrieve(uri)
            metadata = mutagen.File(local_path, easy=True)
            if metadata.tags:
                self._tags = metadata.tags
            title = self._tags.get(TAG_TITLE, [])
            self._manager[ATTR_TITLE] = title[0] if len(title) else ''
            artist = self._tags.get(TAG_ARTIST, [])
            self._manager[ATTR_ARTIST] = artist[0] if len(artist) else ''
            album = self._tags.get(TAG_ALBUM, [])
            self._manager[ATTR_ALBUM] = album[0] if len(album) else ''
            
            local_uri = 'file://{}'.format(local_path)

        # urllib.error.HTTPError
        except Exception:  # pylint: disable=broad-except
            local_uri = uri
        self._player.set_state(Gst.State.NULL)
        self._player.set_property(PROP_URI, local_uri)
        self._player.set_state(Gst.State.PLAYING)
        self.state = STATE_PLAYING
        self._manager[ATTR_URI] = uri
        self._manager[ATTR_DURATION] = self._duration()
        self._manager[ATTR_VOLUME] = self._player.get_property(PROP_VOLUME)
        _LOGGER.info('playing %s (as %s)', uri, local_uri)

    def play(self):
        """Change state to playing."""
        if self.state == STATE_PAUSED:
            self._player.set_state(Gst.State.PLAYING)
            self.state = STATE_PLAYING

    def pause(self):
        """Change state to paused."""
        if self.state == STATE_PLAYING:
            self._player.set_state(Gst.State.PAUSED)
            self.state = STATE_PAUSED

    def stop(self):
        """Stop pipeline."""
        urllib.request.urlcleanup()
        self._player.set_state(Gst.State.NULL)
        self.state = STATE_IDLE
        self._tags = {}

    def set_position(self, position):
        """Set media position."""
        if position > self._duration():
            return
        position_ns = position * _NANOSEC_MULT
        self._manager[ATTR_POSITION] = position
        self._player.seek_simple(_FORMAT_TIME, Gst.SeekFlags.FLUSH, position_ns)

    def set_volume(self, volume):
        """Set volume."""
        self._player.set_property(PROP_VOLUME, volume)
        self._manager[ATTR_VOLUME] = volume
        _LOGGER.info('volume set to %.2f', volume)

    @property
    def state(self):
        """Get state."""
        return self._state

    @state.setter
    def state(self, state):
        """Set state."""
        self._state = state
        self._manager[ATTR_STATE] = state
        _LOGGER.info('state changed to %s', state)

    def _duration(self):
        """Get media duration."""
        duration = 0
        if self.state != STATE_IDLE:
            resp = self._player.query_duration(_FORMAT_TIME)
            duration = resp[1] // _NANOSEC_MULT
        return duration

    def _position(self):
        """Get media position."""
        position = 0
        if self.state != STATE_IDLE:
            resp = self._player.query_position(_FORMAT_TIME)
            position = resp[1] // _NANOSEC_MULT
        return position

    def _on_message(self, bus, message):  # pylint: disable=unused-argument
        """When a message is received from Gstreamer."""
        if message.type == Gst.MessageType.EOS:
            self.stop()
        elif message.type == Gst.MessageType.ERROR:
            self.stop()
            err, _ = message.parse_error()
            _LOGGER.error('%s', err)


class GstreamerPlayer(object):
    """Gstreamer wrapper.

    Simple interface with inter-process communication.
    """

    def __init__(self, pipeline_string):
        """Initialize player wrapper."""
        self._manager = multiprocessing.Manager().dict({
            ATTR_STATE: STATE_IDLE,
            ATTR_DURATION: None,
            ATTR_POSITION: None,
            ATTR_VOLUME: None,
            ATTR_ARTIST: None,
            ATTR_ALBUM: None,
            ATTR_TITLE: None,
            ATTR_URI: None
        })
        self._task_queue = multiprocessing.Queue()
        self._media_queue = multiprocessing.Queue()
        _LOGGER.info('starting gstreamer')
        self._player = GstreamerProcess(self._manager, self._task_queue,
                                        self._media_queue, pipeline_string)
        self._player.start()

    def queue(self, uri):
        """Queue media."""
        self._media_queue.put(uri)

    def pause(self):
        """Pause player."""
        self._queue_task(TASK_PAUSE)

    def play(self):
        """Play player."""
        self._queue_task(TASK_PLAY)

    def stop(self):
        """Stop player."""
        self._queue_task(TASK_STOP)

    def mute(self):
        """Mute."""
        self.volume = 0.0

    def next(self):
        """Play next media in queue."""
        self.stop()

    def quit(self):
        """Turn off player."""
        _LOGGER.info('terminating gstreamer')
        self._player.terminate()

    @property
    def title(self):
        """Get title tag."""
        return self._manager[ATTR_TITLE]

    @property
    def artist(self):
        """Get artist tag."""
        return self._manager[ATTR_ARTIST]

    @property
    def album(self):
        """Get album tag."""
        return self._manager[ATTR_ALBUM]

    @property
    def state(self):
        """Get state."""
        return self._manager[ATTR_STATE]

    @property
    def duration(self):
        """Get duration."""
        return self._manager[ATTR_DURATION]

    @property
    def position(self):
        """Get position."""
        return self._manager[ATTR_POSITION]

    @property
    def uri(self):
        """Get URI."""
        return self._manager[ATTR_URI]

    @property
    def volume(self):
        """Get volume."""
        return self._manager[ATTR_VOLUME]

    @volume.setter
    def volume(self, volume):
        """Set volume."""
        self._queue_task(TASK_SET_VOLUME, volume=volume)

    @position.setter
    def position(self, position):
        """Set position."""
        self._queue_task(TASK_SET_POSITION, position=position)

    def _queue_task(self, name, **kwargs):
        """Queue a task."""
        self._task_queue.put((name, kwargs))
