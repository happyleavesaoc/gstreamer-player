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
_MAX_UPDATE_QUEUE_SIZE = 100
_WAIT = 1

STATE_IDLE = 'idle'
STATE_PLAYING = 'playing'
STATE_PAUSED = 'paused'
UPDATE_STATE = 'state'
UPDATE_VOLUME = 'volume'
UPDATE_POSITION = 'position'
UPDATE_DURATION = 'duration'
UPDATE_TITLE = 'title'
UPDATE_ARTIST = 'artist'
UPDATE_ALBUM = 'album'
UPDATE_URI = 'uri'
TASK_PLAY = 'play'
TASK_PAUSE = 'pause'
TASK_STOP = 'stop'
TASK_MEDIA = 'media'
TASK_GET_STATE = 'get_state'
TASK_GET_DURATION = 'get_duration'
TASK_GET_POSITION = 'get_position'
TASK_GET_URI = 'get_uri'
TASK_GET_TITLE = 'get_title'
TASK_GET_ARTIST = 'get_artist'
TASK_GET_ALBUM = 'get_album'
TASK_GET_VOLUME = 'get_volume'
TASK_SET_POSITION = 'set_position'
TASK_SET_VOLUME = 'set_volume'
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

    def __init__(self, task_queue, update_queue, media_queue, pipeline_string):
        """Initialize process."""
        super(GstreamerProcess, self).__init__()
        self._state = None
        self._tags = {}
        self._task_queue = task_queue
        self._update_queue = update_queue
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
        loop = GLib.MainLoop()
        context = loop.get_context()
        while True:
            if context.pending():
                context.iteration()
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

    def media(self, uri):
        """Play a media file."""
        try:
            local_path, _ = urllib.request.urlretrieve(uri)
            metadata = mutagen.File(local_path, easy=True)
            if metadata.tags:
                self._tags = metadata.tags
        # urllib.error.HTTPError
        except Exception:  # pylint: disable=broad-except
            local_path = uri
        self._player.set_state(Gst.State.NULL)
        self._player.set_property(PROP_URI, 'file://{}'.format(local_path))
        self._player.set_state(Gst.State.PLAYING)
        self.state = STATE_PLAYING
        _LOGGER.info('playing %s', uri)

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

    def get_uri(self):
        """Get URI."""
        uri = self._player.get_property(PROP_CURRENT_URI)
        self._update(UPDATE_URI, uri)

    def get_title(self):
        """Get media title."""
        title = self._tags.get(TAG_TITLE, [])
        self._update(UPDATE_TITLE, title[0] if len(title) else '')

    def get_artist(self):
        """Get media artist."""
        artist = self._tags.get(TAG_ARTIST, [])
        self._update(UPDATE_ARTIST, artist[0] if len(artist) else '')

    def get_album(self):
        """Get media album."""
        album = self._tags.get(TAG_ALBUM, [])
        self._update(UPDATE_ALBUM, album[0] if len(album) else '')

    def get_state(self):
        """Get player state."""
        self._update(UPDATE_STATE, self.state)

    def get_duration(self):
        """Get media duration."""
        self._update(UPDATE_DURATION, self._duration())

    def get_volume(self):
        """Get volume."""
        volume = self._player.get_property(PROP_VOLUME)
        self._update(UPDATE_VOLUME, volume)

    def get_position(self):
        """Get media position."""
        self._update(UPDATE_POSITION, self._position())

    def set_position(self, position):
        """Set media position."""
        if position > self._duration():
            return
        position_ns = position * _NANOSEC_MULT
        self._player.seek_simple(_FORMAT_TIME, Gst.SeekFlags.FLUSH, position_ns)

    def set_volume(self, volume):
        """Set volume."""
        self._player.set_property(PROP_VOLUME, volume)
        _LOGGER.info('volume set to %.2f', volume)

    @property
    def state(self):
        """Get state."""
        return self._state

    @state.setter
    def state(self, state):
        """Set state."""
        self._state = state
        self._update(UPDATE_STATE, state)
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

    def _update(self, key, value):
        """Push an update to the queue."""
        self._update_queue.put((key, value, time.time()))

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

    Simple interface to queue-based communication of process.
    """

    def __init__(self, pipeline_string):
        """Initialize player wrapper."""
        self._task_queue = multiprocessing.Queue()
        self._update_queue = multiprocessing.Queue()
        self._media_queue = multiprocessing.Queue()
        _LOGGER.info('starting gstreamer')
        self._tags = None
        self._player = GstreamerProcess(self._task_queue, self._update_queue,
                                        self._media_queue, pipeline_string)
        self._player.start()

    def queue(self, uri):
        """Queue media."""
        self._media_queue.put(uri)
        return self._getter(TASK_GET_STATE, UPDATE_STATE)

    def pause(self):
        """Pause player."""
        return self._getter(TASK_PAUSE, UPDATE_STATE)

    def play(self):
        """Play player."""
        return self._getter(TASK_PLAY, UPDATE_STATE)

    def stop(self):
        """Stop player."""
        return self._getter(TASK_STOP, UPDATE_STATE)

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
        return self._getter(TASK_GET_TITLE, UPDATE_TITLE)

    @property
    def artist(self):
        """Get artist tag."""
        return self._getter(TASK_GET_ARTIST, UPDATE_ARTIST)

    @property
    def album(self):
        """Get album tag."""
        return self._getter(TASK_GET_ALBUM, UPDATE_ALBUM)

    @property
    def state(self):
        """Get state."""
        return self._getter(TASK_GET_STATE, UPDATE_STATE)

    @property
    def duration(self):
        """Get duration."""
        return self._getter(TASK_GET_DURATION, UPDATE_DURATION)

    @property
    def position(self):
        """Get position."""
        return self._getter(TASK_GET_POSITION, UPDATE_POSITION)

    @property
    def uri(self):
        """Get URI."""
        return self._getter(TASK_GET_URI, UPDATE_URI)

    @property
    def volume(self):
        """Get volume."""
        return self._getter(TASK_GET_VOLUME, UPDATE_VOLUME)

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

    def _getter(self, task, update):
        """Get a property via queues."""
        queued_ts = time.time()
        # Put task in queue.
        self._queue_task(task)
        # Wait for update response.
        size = 0
        while time.time() < queued_ts + _WAIT:
            try:
                key, value, update_ts = self._update_queue.get(False)
                if key == update and update_ts >= queued_ts:
                    return value
                elif size <= _MAX_UPDATE_QUEUE_SIZE:
                    self._update_queue.put((key, value, update_ts))
                    size += 1
            except queue.Empty:
                pass
        _LOGGER.error('did not receive %s update', update)
