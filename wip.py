#!/usr/bin/env python3

# Based loosely off of https://www.deadf00d.com/post/how-i-hacked-sonos-and-youtube-the-same-day.html
# and pytube

from flask import Flask, Response
from http.client import HTTPConnection  # py3
from json import dumps
from logging import getLogger
from logging.config import dictConfig
from os import environ
from requests import Session
from struct import unpack
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
            resp = self._sess.post(url, params=params, data=context)
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
            resp = self._sess.get(url)
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


level = environ.get('LOGGING', 'INFO')
if level == 'DEBUG':
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

app = Flask('sonos-proxy')


@app.route('/<string:vid>')
def youtube(vid):
    yt = YouTube(vid)
    return Response(Transcoder(yt).acc_audio(), mimetype='audio/aac')


if __name__ == '__main__':
    port = int(environ.get('PORT', 9182))
    getLogger().info('Example URL: http://<host-fqdn>:%d/jfKfPfyJRdk', port)
    app.run(host='0.0.0.0', port=port)
