package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/abema/go-mp4"
	"github.com/asticode/go-astits"
)

func check(e error) {
	if e != nil {
		panic(e)
	}
}

type Transcoder struct {
	frameSizes []uint32
}

func NewTranscoder() *Transcoder {
	return &Transcoder{}
}

type Frame struct {
	Time float64
	Data []byte
}

func (t *Transcoder) read(filename string, ch chan Frame) {
	defer close(ch)

	f, err := os.Open(filename)
	check(err)

	// find the sample rate
	boxes, err := mp4.ExtractBoxWithPayload(f, nil, mp4.BoxPath{mp4.BoxTypeMoov(), mp4.BoxTypeMvhd()})
	check(err)
	tkhd := boxes[0].Payload.(*mp4.Mvhd)
	timescale := tkhd.Timescale
	fmt.Printf("timescale=%d\n", timescale)

	// find default sample duration
	boxes, err = mp4.ExtractBoxWithPayload(f, nil, mp4.BoxPath{mp4.BoxTypeMoof(), mp4.BoxTypeTraf(), mp4.BoxTypeTfhd()})
	check(err)
	tfhd := boxes[0].Payload.(*mp4.Tfhd)
	defaultSampleDuration := tfhd.DefaultSampleDuration
	fmt.Printf("defaultSampleDuration=%d\n", defaultSampleDuration)

	// find base media decode time
	boxes, err = mp4.ExtractBoxWithPayload(f, nil, mp4.BoxPath{mp4.BoxTypeMoof(), mp4.BoxTypeTraf(), mp4.BoxTypeTfdt()})
	check(err)
	tfdt := boxes[0].Payload.(*mp4.Tfdt)
	baseMediaDecodeTime := tfdt.BaseMediaDecodeTimeV1
	fmt.Printf("baseMediaDecodeTime=%d\n", baseMediaDecodeTime)

	// grab the audio data
	boxes, err = mp4.ExtractBoxWithPayload(f, nil, mp4.BoxPath{mp4.BoxTypeMdat()})
	check(err)
	mdat := boxes[0].Payload.(*mp4.Mdat)

	// find sample count and entries
	boxes, err = mp4.ExtractBoxWithPayload(f, nil, mp4.BoxPath{mp4.BoxTypeMoof(), mp4.BoxTypeTraf(), mp4.BoxTypeTrun()})
	check(err)
	trun := boxes[0].Payload.(*mp4.Trun)
	sampleCount := trun.SampleCount
	fmt.Printf("sampleCount=%d\n", sampleCount)
	entries := trun.Entries
	fmt.Printf("entries=%v\n", entries)

	// walk the entries turning them into their time & (aac) data
	// TODO: handle non-default duration, entry.SampleDuration?
	entryDuration := float64(defaultSampleDuration) / float64(timescale)
	fmt.Printf("entryDuration=%f\n", entryDuration)
	baseDecodeTime := float64(baseMediaDecodeTime) / float64(timescale)
	fmt.Printf("baseDecodeTime=%f\n", baseDecodeTime)
	data := mdat.Data
	for i, entry := range entries {
		time := baseDecodeTime + entryDuration*float64(i)
		fmt.Printf("entry[%d].=%+v\n  time=%f\n", i, entry, time)
		frame := data[0:entry.SampleSize]
		fmt.Printf("  frame=%x\n", frame[:8])
		data = data[entry.SampleSize:]
		ch <- Frame{
			Time: time,
			Data: frame,
		}
	}
}

func (t *Transcoder) Transcode(filename string) {
	frameCh := make(chan Frame, 5)

	go func() {
		t.read(filename, frameCh)
	}()

	// Create a cancellable context in case you want to stop writing packets/data any time you want
	ctx, cancel := context.WithCancel(context.Background())

	// Handle SIGTERM signal
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, syscall.SIGTERM)
	go func() {
		<-ch
		cancel()
	}()

	// Create your file or initialize any kind of io.Writer
	// Buffering using bufio.Writer is recommended for performance
	f, _ := os.Create("tmp/stream.ts")
	defer f.Close()

	// Create the muxer
	mx := astits.NewMuxer(ctx, f)

	// Add an elementary stream
	err := mx.AddElementaryStream(astits.PMTElementaryStream{
		ElementaryPID: 256,
		StreamType:    astits.StreamTypeAACAudio,
	})
	check(err)

	mx.SetPCRPID(256)

	// Write tables
	// Using that function is not mandatory, WriteData will retransmit tables from time to time
	mx.WriteTables()

	af := &astits.PacketAdaptationField{
		RandomAccessIndicator: true,
		HasPCR:                true,
		// PCR set when we see the first frame below
	}

	faac, err := os.Create("tmp/stream.aac")
	check(err)
	defer faac.Close()

	firstFrame := true
	for frame := range frameCh {
		if firstFrame {
			af.PCR = &astits.ClockReference{Base: int64(frame.Time * 90000)}
			firstFrame = false
		}
		n := len(frame.Data)
		fmt.Printf("received frame=*** %d\n", n)
		// include the header size
		n += 7

		header := []byte{
			// 1111 1111 - 0
			0xff,
			// 1111 0001 - 1
			0xf1,
			// 0101 0000 - 2
			0x50,
			// 1000 00xx - 3
			0x80,
			// xxxx xxxx - 4
			0x00,
			// xxx1 1111 - 5
			0x1f,
			// 1111 1100 - 6
			0xfc,
		}
		header[3] |= byte(n>>11) & 0x3
		header[4] |= byte(n >> 3)
		header[5] |= byte(n << 5)

		n, err := faac.Write(header)
		check(err)
		n, err = faac.Write(frame.Data)
		check(err)

		// Write data
		data := append(header, frame.Data...)
		n, err = mx.WriteData(&astits.MuxerData{
			PID:             256,
			AdaptationField: af,
			PES: &astits.PESData{
				Header: &astits.PESHeader{
					OptionalHeader: &astits.PESOptionalHeader{
						MarkerBits:      2,
						PTSDTSIndicator: astits.PTSDTSIndicatorOnlyPTS,
						PTS:             &astits.ClockReference{Base: int64(frame.Time * 90000)},
					},
					StreamID: 192, // = audio
				},
				Data: data,
			},
		})
		check(err)
	}
}

func main() {
	t := NewTranscoder()
	t.Transcode("tmp/stream.mp4")
}
