#
#
#

from dataclasses import dataclass, field
from logging import getLogger
from queue import Queue
from threading import Thread
from time import sleep, time

from requests import Session
from pytube import YouTube as _YouTube

from .mp4 import Mp4


@dataclass(order=True)
class _Chunk:
    seq_num: int
    seen_at: int = field(compare=False)
    content: bytes = field(compare=False)

    @property
    def mp4(self):
        if not hasattr(self, '_mp4'):
            self._mp4 = Mp4(self.content)
        return self._mp4

    def __repr__(self):
        mp4 = getattr(self, '_mp4', None) is not None
        return f'Chunk(seq_num={self.seq_num}, seen_at={self.seen_at}, content=***, mp4={mp4})'


class YouTubeStreamer(Thread):
    def __init__(self, youtube, duration=5.0):
        name = f'YouTubeStreamer[{youtube.id}]'
        super().__init__(name=name)
        self.log = getLogger(name)
        self.log.info('__init__:')

        self.youtube = youtube
        self.duration = duration
        self.searching_wait = duration / 4

        self.running = False
        self.queue = Queue()

        sess = Session()
        sess.headers = {
            'accept-language': 'en-US,en',
            'content-type': 'application/json',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.87 Safari/537.36',
        }
        self._sess = sess

    def start(self):
        self.log.info('start: ')
        self.running = True
        super().start()

    def stop(self):
        self.log.info('stop: emptying queue')
        self.running = False
        while not self.queue.empty():
            self.queue.get(block=False)

    def fetch(self, url):
        resp = self._sess.get(url, timeout=self.duration / 2.0)
        # TODO: retries?
        resp.raise_for_status()
        return _Chunk(
            seq_num=int(resp.headers['x-sequence-num']),
            seen_at=int(resp.headers['x-walltime-ms']) / 1000.0,
            content=resp.content,
        )

    def run(self):
        self.log.info('run: ')
        self.running = True

        audio = list(
            self.youtube.streams.filter(adaptive=True, only_audio=True)
        )
        audio.sort(key=lambda s: int(s.abr.replace('kbps', '')), reverse=True)
        best = audio[0]
        url = best.url
        self.log.debug('run: url=%s', url)

        # grab our first chunk
        chunk = self.fetch(url)
        self.log.debug('run: first chunk=%s', chunk)
        self.queue.put(chunk)

        wait = self.searching_wait
        while self.running:
            if self.queue.qsize() > 2:
                self.log.info('run: assuming consumer is gone')
                self.stop()
                continue
            if wait > 0:
                self.log.debug('run: waiting=%f', wait)
                sleep(wait)

            # fetch a new candidate
            start = time()
            candidate = self.fetch(url)
            elapsed = time() - start
            self.log.debug(
                'run:   candidate chunk=%s, elapsed=%f', candidate, elapsed
            )

            seq_diff = candidate.seq_num - chunk.seq_num
            if seq_diff > 0:
                self.log.debug('run:   new chunk')
                # we were searching, and we're done
                # we should be at most searching_wait after a change, next
                # one should come before duration
                wait = candidate.mp4.duration
                # make it our new chunk and add it to the set
                chunk = candidate
                self.queue.put(chunk)
            else:
                self.log.debug('run:   duplicate chunk')
                # we're off track, go back to searching
                wait = self.searching_wait

            wait -= elapsed

        self.log.info('run: exiting')


class YouTube(_YouTube):
    PRE_SERVE = 0.5
    RE_FETCH_DELAY = 1.0

    def __init__(self, id):
        self.log = getLogger(f'YouTube[{id}]')
        self.log.info('__init__:')

        self.id = id

        # annoying there's not a session on the _YouTube object
        sess = Session()
        sess.headers = {
            'accept-language': 'en-US,en',
            'content-type': 'application/json',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.87 Safari/537.36',
        }
        self._sess = sess

        super().__init__(f'https://youtu.be/{id}')

    def check_availability(self):
        # parent class blows up b/c of live stream, that's what we're expecting
        # here ...
        pass

    # TODO: timeout based on duration
    def fetch(self, url, timeout=2.5):
        resp = self._sess.get(url, timeout=timeout)
        resp.raise_for_status()
        return Mp4(resp.content)

    # https://docs.fileformat.com/video/mp4/#:~:text=Here%20is%20a%20list%20of%20second%2Dlevel%20atoms%20used%20in,the%20user%20and%20track%20information.
    def stream_best_audio_mp4(self):
        self.log.debug('stream_best_audio_mp4: ')

        audio = list(self.streams.filter(adaptive=True, only_audio=True))
        audio.sort(key=lambda s: int(s.abr.replace('kbps', '')), reverse=True)
        best = audio[0]
        url = best.url

        # TODO: exit when we no longer have a client
        clock = None
        while True:
            start = time()
            self.log.debug('stream_best_audio_mp4: request start=%f', start)

            mp4 = self.fetch(url)
            while mp4.time == clock:
                self.log.info(
                    'stream_best_audio_mp4:   early fetch, redoing after delay=%f',
                    self.RE_FETCH_DELAY,
                )
                sleep(self.RE_FETCH_DELAY)
                start = time()
                mp4 = self.fetch(url)

            elapsed = time() - start
            self.log.debug('stream_best_audio_mp4:   elapsed=%f', elapsed)

            # TODO: average elapsed
            if clock is None:
                # and wait half of the mp4's duration so we're playing it with a
                # bit of breating room to get the next
                pre_wait = mp4.duration * 0.75 - self.PRE_SERVE
                post_wait = 0
            else:
                # we want to wait the time between mp4s with enough time to make
                # the next request
                pre_wait = mp4.time - clock - elapsed - self.PRE_SERVE
                post_wait = self.PRE_SERVE
            self.log.debug(
                'stream_best_audio_mp4:   pre_wait=%f, post_wait=%f, total=%f',
                pre_wait,
                post_wait,
                pre_wait + post_wait,
            )
            if pre_wait > 0:
                sleep(pre_wait)

            # hand out the mp4
            self.log.debug('stream_best_audio_mp4:   yield')
            yield mp4

            if post_wait > 0:
                sleep(post_wait)

            self.log.debug('stream_best_audio_mp4:   done')

            # set our clock to the time mp4 we just played
            clock = mp4.time

        # assume 5s chunks
        duration = 5
        # we want the first chunk now
        expected = time()
        while True:
            start = time()
            self.log.debug('stream_best_audio_mp4: request start=%f', start)

            resp = self._sess.get(url, stream=True, timeout=duration / 2)
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=None):
                self.log.debug('stream_best_audio_mp4: chunk=%d', len(chunk))
                if chunk:
                    yield chunk
                    with open(f'/tmp/chunk.{start}.mp4', 'wb') as fh:
                        fh.write(chunk)

            # how long did the request take
            now = time()
            elapsed = now - start
            self.log.debug(
                'stream_best_audio_mp4:   now=%f, elapsed=%f', now, elapsed
            )

            # we expect the next chunk a duration into the future
            expected += duration
            # how far are we from the point we expect we'll need another chunk
            needed = expected - now
            # adjust needed for the time the request is likely to take
            needed -= elapsed + 1
            self.log.debug(
                'stream_best_audio_mp4:   expected=%f, needed=%f',
                expected,
                needed,
            )

            if needed > 0:
                sleep(needed)
