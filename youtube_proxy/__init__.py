#!/usr/bin/env python3

# Based loosely off of https://www.deadf00d.com/post/how-i-hacked-sonos-and-youtube-the-same-day.html
# and pytube

from av import open as av_open
from datetime import datetime
from flask import Flask, Response
from http.client import HTTPConnection  # py3
from io import BytesIO, StringIO
from json import dumps
from logging import getLogger
from logging.config import dictConfig
from os import environ
from requests import Session
from struct import unpack
from threading import Event, Thread
from time import sleep, time


class YouTube(object):
    def __init__(self, id):
        self.log = getLogger(f'YouTube[{id}]')
        self.log.info('__init__:')

        self.id = id

        sess = Session()
        sess.headers = {
            'accept-language': 'en-US,en',
            'content-type': 'application/json',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.87 Safari/537.36',
        }
        self._sess = sess

        self._vid_info = None
        self._streaming_data = None

    @property
    def info(self):
        if self._vid_info is None:
            self.log.info('info: fetching')
            url = 'https://www.youtube.com/youtubei/v1/player'
            params = {
                'key': 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8',
                'contentCheckOk': True,
                'racyCheckOk': True,
                'videoId': self.id,
            }
            context = bytes(
                dumps(
                    {
                        'context': {
                            'client': {
                                'clientName': 'ANDROID',
                                'clientVersion': '16.20',
                            }
                        }
                    }
                ),
                encoding='utf-8',
            )
            resp = self._sess.post(url, params=params, data=context, timeout=10)
            resp.raise_for_status()
            self._vid_info = resp.json()
        return self._vid_info

    @property
    def video_details(self):
        return self.info['videoDetails']

    @property
    def author(self):
        return self.video_details['author']

    @property
    def is_live(self):
        return self.video_details['isLive']

    @property
    def live_chunk_readahead(self):
        return self.video_details['liveChunkReadahead']

    @property
    def short_description(self):
        return self.video_details['shortDescription']

    @property
    def thumbnails(self):
        return self.video_details['thumbnail']['thumbnails']

    @property
    def title(self):
        return self.video_details['title']

    @property
    def streaming_data(self):
        if self._streaming_data is None:
            sd = self.info['streamingData']['adaptiveFormats']
            sd.sort(key=lambda s: s['bitrate'], reverse=True)
            self._streaming_data = sd
        return self._streaming_data

    # https://docs.fileformat.com/video/mp4/#:~:text=Here%20is%20a%20list%20of%20second%2Dlevel%20atoms%20used%20in,the%20user%20and%20track%20information.
    def stream_best_audio_mp4(self):
        self.log.debug('stream_best_audio_mp4: ')
        best = next(
            s
            for s in self.streaming_data
            if s['mimeType'].startswith('audio/mp4')
        )
        url = best['url']
        target_duration = best['targetDurationSec']
        # TODO: stream in chunks
        # TODO: live_chunk_readahead
        while True:
            start = time()
            resp = self._sess.get(url, timeout=5)
            resp.raise_for_status()
            yield resp.content
            elapsed = time() - start
            needed = target_duration - elapsed
            self.log.debug('stream_best_audio_mp4: sleeping %f', needed)
            sleep(needed)


class Trun(object):
    log = getLogger(f'Trun')

    def __init__(self, atom):
        self.flags = unpack('>i', atom[0:4])[0]
        self.sample_count = unpack('>i', atom[4:8])[0]
        self.data_offset = unpack('>i', atom[8:12])[0]
        # rest of the atom is an array of integers indicating the mdat frame
        # sizes
        self.frame_sizes = [
            unpack('>i', atom[i : i + 4])[0] for i in range(12, len(atom), 4)
        ]


class Mdat(object):
    log = getLogger(f'Mdat')

    def __init__(self, atom, trun):
        self.atom = atom
        self.trun = trun

    @property
    def frames(self):
        s = 0
        # walk trun's list of frame sizes, picking up where we left off each
        # time
        for n in self.trun.frame_sizes:
            yield self.atom[s : s + n]
            s = s + n


class Transcoder(object):
    log = getLogger(f'Transcoder')

    def __init__(self, youtube):
        self.log.info('__init__:')

        self.youtube = youtube

    def atomize(self, chunk):
        while chunk:
            self.log.debug(
                'acc_audio: chunk=%s %d', chunk[:32].hex(), len(chunk)
            )
            # first 4 bytes are the atom's size
            atom_size = unpack('>i', chunk[0:4])[0] - 4
            # next 4 bytes are the atom's type
            atom_type = chunk[4:8].decode()
            # rest is the atom's data
            atom = chunk[8:atom_size]

            yield atom_type, atom

            # step to the next chunk
            chunk = chunk[4 + atom_size :]

    def acc_audio(self):
        # TODO: figure out how to stuff title, author, image url etc in here if
        # possible
        self.log.info('acc_audio: ')
        for chunk in self.youtube.stream_best_audio_mp4():
            # skip the header
            header_size = unpack('>i', chunk[0:4])[0]
            # skip over the header, we won't be using it
            chunk = chunk[header_size:]
            trun = None
            for atom_type, atom in self.atomize(chunk):
                if atom_type == 'moof':
                    for atom_type, atom in self.atomize(atom):
                        if atom_type == 'traf':
                            for atom_type, atom in self.atomize(atom):
                                if atom_type == 'trun':
                                    # this will tell us about the next mdat
                                    trun = Trun(atom)
                elif atom_type == 'mdat' and trun:
                    # this is where the frames we're after live
                    mdat = Mdat(atom, trun)
                    # https://wiki.multimedia.cx/index.php/ADTS
                    header_fmt = (
                        '111111111111000101010000100000{:013b}1111111111100'
                    )
                    for frame in mdat.frames:
                        header = int(
                            header_fmt.format(len(frame) + 7), 2
                        ).to_bytes(7, byteorder='big')
                        yield header + frame

                    # we've used the trun
                    trun = None

        # .aac and the mime type audio/aac.


level = environ.get('LOGGING_LEVEL', 'INFO').upper()
if environ.get('LOGGING_HTTP_DEBUG', False):
    HTTPConnection.debuglevel = 1
dictConfig(
    {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'simple': {
                'format': '%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s %(message)s',
                'datefmt': '%Y-%m-%dT%H:%M:%S',
            }
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'level': level,
                'formatter': 'simple',
            }
        },
        'root': {'level': level, 'handlers': ('console',)},
        'loggers': {'urllib3.connectionpool': {'level': 'INFO'}},
    }
)


# TODO: thread safety...
class EvictingDict(dict):
    def __init__(self, maxlen):
        super().__init__()
        self.maxlen = maxlen

    def append(self, k, v):
        if len(self) >= self.maxlen:
            # we need to pop something off first
            oldest = list(self.keys())[0]
            del self[oldest]
        self[k] = v


class Segment(object):
    def __init__(self, sequence, ts, mimetype):
        self.sequence = sequence
        self.ts = ts
        self.mimetype = mimetype


class HlsStream(Thread):
    _streams = {}

    @classmethod
    def get(cls, vid):
        if vid in cls._streams:
            return cls._streams[vid]

        def cleanup():
            # TODO: locking is probably needed here
            del cls._streams[vid]

        stream = HlsStream(YouTube(vid), cleanup)
        stream.start()
        cls._streams[vid] = stream

        return stream

    def __init__(self, youtube, cleanup, keepalive=30):
        name = f'YouTube[{youtube.id}]'
        super().__init__(name=name)
        self.log = getLogger(name)

        self.youtube = youtube
        self.cleanup = cleanup
        self.keepalive = keepalive

        self._segments = EvictingDict(maxlen=5)

        self.extend()

        self.ready = Event()

    @property
    def bandwidth(self):
        # TODO: get this from stream bitrate
        return 44100

    @property
    def codecs(self):
        # TODO: get this from stream mimeType
        return 'mp4a.40.2'

    @property
    def target_duration(self):
        # TODO: get this from stream targetDurationSec
        return 5

    @property
    def datetime_utc(self):
        # TODO: this should be the time of the first media segment
        return datetime.utcnow().isoformat()

    @property
    def segments(self):
        self.extend()
        return self._segments

    def extend(self):
        now = time()
        self.lifetime = now + self.keepalive
        self.log.debug('extend: lifetime=%f, now=%f', self.lifetime, now)

    def start(self):
        super().start()
        # TODO: timeout and error
        self.ready.wait()

    def run(self):
        self.log.info('run: starting')

        sequence = 0
        chunk_iter = self.youtube.stream_best_audio_mp4()
        chunk = next(chunk_iter, None)
        while chunk and time() < self.lifetime:
            self.log.debug('run: chunk=***, sequence=%d', sequence)

            source_buf = BytesIO()
            source_buf.write(chunk)
            source_buf.seek(0)
            source = av_open(source_buf)

            target_buf = BytesIO()
            target = av_open(target_buf, mode='w', format='mpegts')

            # TODO: set this to the video title
            target.metadata['title'] = 'YouTube-Proxy'

            source_stream = source.streams.audio[0]
            target_stream = target.add_stream(template=source_stream)

            for packet in source.demux(source_stream):
                if packet.dts is None:
                    continue
                packet.stream = target_stream
                target.mux(packet)

            source.close()
            target.close()

            segment = Segment(sequence, target_buf.getvalue(), 'video/MP2T')
            self._segments.append(sequence, segment)
            sequence += 1

            self.ready.set()

            chunk = next(chunk_iter, None)

        self.log.info('run: cleaning up')
        self.cleanup()
        self.log.info('run: ending')


def create_app():
    app = Flask('sonos-proxy')

    @app.route('/<string:vid>')
    def youtube(vid):
        yt = YouTube(vid)
        return Response(Transcoder(yt).acc_audio(), mimetype='audio/aac')

    @app.route('/hls/<string:vid>')
    def hls(vid):
        stream = HlsStream.get(vid)

        return Response(
            f'''#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH={stream.bandwidth},CODECS="{stream.codecs}"
stream/{vid}
''',
            mimetype='application/x-mpegurl',
        )

    @app.route('/hls/stream/<string:vid>')
    def hls_stream(vid):
        stream = HlsStream.get(vid)

        segments = list(stream.segments.items())
        # TODO: what if there's no sequences
        target_duration = stream.target_duration
        datetime_utc = stream.datetime_utc
        buf = StringIO()
        buf.write(
            f'''
#EXTM3U
#EXT-X-VERSION:3
## Created by youtube-proxy
#EXT-X-MEDIA-SEQUENCE:{segments[0][0]}
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-TARGETDURATION:{target_duration}
#EXT-X-PROGRAM-DATE-TIME:{datetime_utc}
'''
        )
        for sequence, _ in segments:
            buf.write(f'#EXTINF:{target_duration:0.1f}, no desc\n')
            buf.write(vid)
            buf.write('-')
            buf.write(f'{sequence}')
            buf.write('.ts\n')
        resp = Response(
            buf.getvalue(), mimetype='application/vnd.apple.mpegurl'
        )
        resp.cache_control.max_age = 1
        return resp

    @app.route('/hls/stream/<string:vid>-<int:sequence>.ts')
    def hls_stream_ts(vid, sequence):
        segment = HlsStream.get(vid).segments[sequence]
        return Response(segment.ts, mimetype=segment.mimetype)

    getLogger().info('Example URL: http://<host-fqdn>:<port>/jfKfPfyJRdk')
    return app
