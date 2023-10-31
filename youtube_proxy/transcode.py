#
#
#

from logging import getLogger
from time import sleep


class Transcoder:
    log = getLogger('Transcoder')

    def __init__(self, youtube_streamer, startup_delay=5.0):
        self.log.info(
            '__init__: youtube_streamer=%s, startup_delay=%f',
            youtube_streamer,
            startup_delay,
        )
        self.youtube_streamer = youtube_streamer
        self.startup_delay = startup_delay

    def acc_audio(self):
        self.log.info('acc_audio: ')
        yts = self.youtube_streamer

        yts.start()
        sleep(self.startup_delay)

        # TODO: figure out how to stuff title, author, image url etc in here if
        # possible
        while chunk := yts.queue.get(timeout=30):
            for frame in chunk.mp4.frames:
                n = len(frame) + 7
                header = f'111111111111000101010000100000{n:013b}1111111111100'
                header = int(header, 2).to_bytes(7, byteorder='big')
                yield header + frame
