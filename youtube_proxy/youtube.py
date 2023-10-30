#
#
#

from logging import getLogger
from time import sleep, time

from requests import Session
from pytube import YouTube as _YouTube

from .mp4 import Mp4


class YouTube(_YouTube):
    PRE_SERVE = 0.5
    RE_FETCH_DELAY = 1.0

    def __init__(self, id):
        self.log = getLogger(f'YouTube[{id}]')
        self.log.info('__init__: id=%s', id)

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
