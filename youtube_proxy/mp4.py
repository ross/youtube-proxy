#
#
#

from io import StringIO
from logging import getLogger


class Box:
    _TYPES = {}

    log = getLogger('Box')

    def __init_subclass__(cls):
        try:
            cls._TYPES[cls._type] = cls
        except AttributeError:
            pass

    @classmethod
    def new(cls, size, _type, data):
        try:
            return cls._TYPES[_type](size, _type, data)
        except KeyError:
            cls.log.warning('new: unhandled _type=%s', _type)
            return None

    def __init__(self, size, _type, data):
        self.size = size

    def __repr__(self, prefix=''):
        return f'{prefix}[{self._type}] size={self.size}'


class FullBox(Box):
    def __init__(self, size, _type, data):
        super().__init__(size, _type, data)
        self.version = int(data[0])
        self.flags = int.from_bytes(data[1:4], 'big')

    def __repr__(self, prefix=''):
        return f'{super().__repr__(prefix)} version={self.version} flags={self.flags:06x}'


class FileTypeBox(Box):
    _type = 'ftyp'

    def __init__(self, size, _type, data):
        super().__init__(size, _type, data)
        self.major_brand = data[0:4].decode('ascii')
        self.minor_version = int.from_bytes(data[4:8], 'big')
        data = data[8:]
        self.compatible_brands = []
        while data:
            self.compatible_brands.append(data[:4].decode('ascii'))
            data = data[4:]

    def __repr__(self, prefix=''):
        compatible_brands = f'\n{prefix} - compatibleBrand: '.join(
            self.compatible_brands
        )
        return f'''{super().__repr__(prefix)}
{prefix} - majorBrand: {self.major_brand}
{prefix} - minorVersion: {self.minor_version}
{prefix} - compatibleBrand: {compatible_brands}'''


class _ContainerMixin:
    def __init__(self, data):
        self.boxes = box_up(data)

    def get(self, _type):
        for box in self.boxes:
            if box._type == _type:
                yield box


class ContainerBox(_ContainerMixin, Box):
    def __init__(self, size, _type, data):
        Box.__init__(self, size, _type, data)
        _ContainerMixin.__init__(self, data)

    def __repr__(self, prefix=''):
        if not self.boxes:
            return super().__repr__(prefix)
        boxes = '\n'.join(c.__repr__(f'{prefix}  ') for c in self.boxes)
        return f'''{super().__repr__(prefix)}
{prefix}{boxes}'''


class MovieBox(ContainerBox):
    _type = 'moov'


class MovieHeaderBox(FullBox):
    _type = 'mvhd'

    def __init__(self, size, _type, data):
        super().__init__(size, _type, data)
        # TODO: kind of annoying they'll all have to do this...
        data = data[4:]
        word_size = 8 if self.version == 1 else 4
        self.creation_time = int.from_bytes(data[:word_size], 'big')
        data = data[word_size:]
        self.modification_time = int.from_bytes(data[:word_size], 'big')
        data = data[word_size:]
        self.timescale = int.from_bytes(data[:word_size], 'big')
        data = data[word_size:]
        self.duration = int.from_bytes(data[:word_size], 'big')

    def __repr__(self, prefix=''):
        return f'''{super().__repr__(prefix)}
{prefix} - timeScale: {self.timescale}
{prefix} - duration: {self.duration}
{prefix} - creation time: {self.creation_time}
{prefix} - modification time: {self.modification_time}'''


class MovieExtendsBox(ContainerBox):
    _type = 'mvex'


class TrackExtendsBox(Box):
    _type = 'trex'


class TrackBox(Box):
    _type = 'trak'


class Emsg(Box):
    _type = 'emsg'

    # TODO: ???


class MovieFragmentBox(ContainerBox):
    _type = 'moof'


class MovieFragmentHeaderBox(FullBox):
    _type = 'mfhd'

    def __init__(self, size, _type, data):
        super().__init__(size, _type, data)
        # TODO: kind of annoying they'll all have to do this...
        data = data[4:]
        self.sequence_number = int.from_bytes(data[:4], 'big')

    def __repr__(self, prefix=''):
        return f'''{super().__repr__(prefix)}
{prefix} - sequenceNumber: {self.sequence_number}'''


class TrackFragmentBox(ContainerBox):
    _type = 'traf'


class TrackFragmentHeaderBox(FullBox):
    _type = 'tfhd'

    def __init__(self, size, _type, data):
        super().__init__(size, _type, data)
        # TODO: kind of annoying they'll all have to do this...
        data = data[4:]
        self.track_id = int.from_bytes(data[:4], 'big')
        data = data[4:]

        if self.flags & 0x000001:
            self.base_data_offset = int.from_bytes(data[:8], 'big')
            data = data[8:]
        else:
            self.base_data_offset = None

        if self.flags & 0x000002:
            self.sample_description_index = int.from_bytes(data[:4], 'big')
            data = data[4:]
        else:
            self.sample_description_index = None

        if self.flags & 0x000008:
            self.default_sample_duration = int.from_bytes(data[:4], 'big')
            data = data[4:]
        else:
            self.default_sample_duration = None

        if self.flags & 0x000010:
            self.default_sample_size = int.from_bytes(data[:4], 'big')
            data = data[4:]
        else:
            self.default_sample_size = None

        if self.flags & 0x000020:
            self.default_sample_flags = int.from_bytes(data[:4], 'big')
            data = data[4:]
        else:
            self.default_sample_flags = None

        self.duration_is_empty = self.flags & 0x010000

    def __repr__(self, prefix=''):
        buf = StringIO()
        opt_prefix = f'\n{prefix} - '

        if self.base_data_offset is not None:
            buf.write(opt_prefix)
            buf.write('baseDataOffset: ')
            buf.write(str(self.base_data_offset))

        if self.sample_description_index is not None:
            buf.write(opt_prefix)
            buf.write('sampleDescriptionIndex: ')
            buf.write(str(self.sample_description_index))

        if self.default_sample_duration is not None:
            buf.write(opt_prefix)
            buf.write('defaultSampleDuration: ')
            buf.write(str(self.default_sample_duration))

        if self.default_sample_flags is not None:
            buf.write(opt_prefix)
            # TODO: real values worked out
            buf.write(
                f'defaultSampleFlags: {self.default_sample_flags:08x} (isLeading=0, dependsOn=0, isDependedOn=0 hasRedundancy=0 padding=0 isNonSync=false degradationPriority=0)'
            )

        return f'''{super().__repr__(prefix)}
{prefix} - trackID: {self.track_id}
{prefix} - defaultBaseIsMoof: {not self.duration_is_empty}{buf.getvalue()}'''


class TrackFragmentBaseMediaDecodeTimeBox(FullBox):
    _type = 'tfdt'

    def __init__(self, size, _type, data):
        super().__init__(size, _type, data)
        # TODO: kind of annoying they'll all have to do this...
        data = data[4:]
        self.base_media_decode_time = int.from_bytes(data[:8], 'big')

    def __repr__(self, prefix=''):
        return f'''{super().__repr__(prefix)}
{prefix} - baseMediaDecodeTime: {self.base_media_decode_time}'''


class TrackFragmentRunBox(FullBox):
    _type = 'trun'

    def __init__(self, size, _type, data):
        super().__init__(size, _type, data)
        # TODO: kind of annoying they'll all have to do this...
        data = data[4:]
        self.sample_count = int.from_bytes(data[:4], 'big')
        data = data[4:]

        if self.flags & 0x000001:
            self.data_offset = int.from_bytes(data[:4], 'big')
            data = data[4:]
        else:
            self.data_offset = None

        if self.flags & 0x000004:
            self.first_sample_flags = int.from_bytes(data[:4], 'big')
            data = data[4:]
        else:
            self.first_sample_flags = None

        self.sample_duration_present = self.flags & 0x000100
        self.sample_size_present = self.flags & 0x000200
        self.sample_flags_present = self.flags & 0x000400
        self.sample_composition_time_offset_present = self.flags & 0x000800

        self.sample_durations = []
        self.sample_sizes = []
        self.sample_flags = []
        self.sample_composition_time_offsets = []

        while data:
            if self.sample_duration_present:
                self.sample_durations.append(int.from_bytes(data[:4], 'big'))
                data = data[4:]
            if self.sample_size_present:
                self.sample_sizes.append(int.from_bytes(data[:4], 'big'))
                data = data[4:]
            if self.sample_flags_present:
                self.sample_flags.append(int.from_bytes(data[:4], 'big'))
                data = data[4:]
            if self.sample_composition_time_offset_present:
                self.sample_composition_time_offsets.append(
                    int.from_bytes(data[:4], 'big')
                )
                data = data[4:]

    def __repr__(self, prefix=''):
        # TODO: print if present
        return f'''{super().__repr__(prefix)}
{prefix} - sampleCount: {self.sample_count}
{prefix} - dataOffset: {self.data_offset}
{prefix} - sampleSizes: {self.sample_sizes}'''


class MediaDataBox(Box):
    _type = 'mdat'

    def __init__(self, size, _type, data):
        super().__init__(size, _type, data)
        self.data = data

    def frames(self, frame_sizes):
        # walk trun's list of frame sizes, picking up where we left off each
        # time
        s = 0
        for n in frame_sizes:
            yield self.data[s : s + n]
            s = s + n


def box_up(data):
    boxes = []

    while data:
        box_size = int.from_bytes(data[0:4], 'big')
        box_type = data[4:8].decode()

        box = Box.new(box_size, box_type, data[8:box_size])
        if box:
            boxes.append(box)

        data = data[box_size:]

    return boxes


class Mp4(_ContainerMixin):
    log = getLogger('Mp4')

    def __init__(self, data):
        _ContainerMixin.__init__(self, data)
        self._time = None
        self._duration = None

    @property
    def time(self):
        if self._time is None:
            # TODO: multiple support?
            timescale = next(next(self.get('moov')).get('mvhd')).timescale
            moof = next(self.get('moof'))
            traf = next(moof.get('traf'))
            tfdt = next(traf.get('tfdt'))
            self._time = tfdt.base_media_decode_time / float(timescale)
        return self._time

    @property
    def frames(self):
        truns = []
        for moof in self.get('moof'):
            for traf in moof.get('traf'):
                truns.extend(traf.get('trun'))

        # TODO: what does multiple truns/mdats look like
        trun = truns[0]
        mdat = next(self.get('mdat'))
        s = 0
        for n in trun.sample_sizes:
            yield mdat.data[s : s + n]
            s += n

    @property
    def duration(self):
        if self._duration is None:
            # TODO: what about multiples
            timescale = next(next(self.get('moov')).get('mvhd')).timescale
            traf = next(next(self.get('moof')).get('traf'))
            sample_duration = next(traf.get('tfhd')).default_sample_duration
            sample_count = next(traf.get('trun')).sample_count
            self._duration = float(sample_count * sample_duration) / float(
                timescale
            )
        return self._duration

    def __repr__(self):
        return '\n'.join(str(b) for b in self.boxes)
