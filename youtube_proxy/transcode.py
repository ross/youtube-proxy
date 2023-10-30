#
#
#

from logging import getLogger


class Transcoder:
    log = getLogger('Transcoder')

    def __init__(self, youtube):
        self.log.info('__init__:')
        self.youtube = youtube

    def acc_audio(self):
        # TODO: figure out how to stuff title, author, image url etc in here if
        # possible
        self.log.info('acc_audio: ')
        for mp4 in self.youtube.stream_best_audio_mp4():
            for frame in mp4.frames:
                n = len(frame) + 7
                header = f'111111111111000101010000100000{n:013b}1111111111100'
                header = int(header, 2).to_bytes(7, byteorder='big')
                yield header + frame
