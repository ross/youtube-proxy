package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"os"
	"os/signal"
	"syscall"

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

func (t *Transcoder)processAtom(buf []byte, ch chan<- []byte) {
	for ; len(buf) > 0; {
		// atom size
		atomSize := binary.BigEndian.Uint32(buf[:4])
		fmt.Printf("atomSize=%d\n", atomSize)

		// atom type
		atomType := buf[4:8]
		fmt.Printf("atomType=%s\n", atomType)

		// read atom data
		atomData := buf[8:atomSize]
		fmt.Printf("atomData=%x\n", atomData)

		if bytes.Compare(atomType, []byte{'m', 'o', 'o', 'f'}) == 0 {
			t.processAtom(atomData, ch)
		} else if bytes.Compare(atomType, []byte{'t', 'r', 'a', 'f'}) == 0 {
			t.processAtom(atomData, ch)
		} else if bytes.Compare(atomType, []byte{'t', 'r', 'u', 'n'}) == 0 {
			// get our frame sizes
			sampleCount := binary.BigEndian.Uint32(atomData[4:8])
			fmt.Printf("trun.sampleCount=%d\n", sampleCount)
			t.frameSizes = make([]uint32, sampleCount)
			expected := uint32(0)
			// skip to the frames section
			atomData = atomData[12:]
			for i := uint32(0); i < sampleCount; i++ {
				t.frameSizes[i] = binary.BigEndian.Uint32(atomData[i*4:i*4+4])
				expected += t.frameSizes[i]
				fmt.Printf("  trun.frameSizes[%d]=%d\n", i, t.frameSizes[i])
			}
			fmt.Printf("  trun.expected=%d\n", expected)
		} else if bytes.Compare(atomType, []byte{'m', 'd', 'a', 't'}) == 0 {
			// emit our frames
			frameBuf := atomData
			fmt.Printf("frameBuf=%d\n", len(frameBuf))
			used := uint32(0)
			for i, frameSize := range t.frameSizes {
				frame := frameBuf[:frameSize]
				fmt.Printf("  frame[%d]=*** %d/%d\n", i, frameSize, len(frame))
				used += frameSize
				ch <- frame
				frameBuf = frameBuf[frameSize:]
				fmt.Printf("    %d left=%d, used=%d\n", i, len(frameBuf), used)
			}
		}

		buf = buf[atomSize:]
	}
}

func (t *Transcoder)atomize(buf []byte, ch chan<- []byte) {
	defer close(ch)
	t.processAtom(buf, ch)
}

func (t *Transcoder)Transcode(filename string) {
	buf, err := os.ReadFile(filename)
    check(err)

	// skip the header
	headerSize := binary.BigEndian.Uint32(buf[0:4])
	fmt.Printf("headerSize=%d\n", headerSize)
	fmt.Printf("before=%x\n", buf[:32])
	buf = buf[headerSize:]
	fmt.Printf("after=%x\n", buf[:32])

	frameCh := make(chan []byte, 5)

	go func() {
		t.atomize(buf, frameCh)
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
	err = mx.AddElementaryStream(astits.PMTElementaryStream{
		ElementaryPID: 256,
		StreamType:    astits.StreamTypeAACAudio,
	})
	check(err)

	mx.SetPCRPID(256)

	// Write tables
	// Using that function is not mandatory, WriteData will retransmit tables from time to time
	mx.WriteTables()

	pcr := int64(1 * 90000)
	af := &astits.PacketAdaptationField{
		RandomAccessIndicator: true,
		HasPCR: true,
		PCR: &astits.ClockReference{Base: pcr},
	}

	faac, err := os.Create("tmp/stream.aac")
    check(err)
	defer faac.Close()

	tpf := 4.97 / 215.0
	fmt.Printf("tpf=%f\n", tpf)
	i := 0

	data := make([]byte, 0)
	for frame := range frameCh {
		n := len(frame)
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
		fmt.Printf("before header=%0x %0b %d\n", header, header, n)
		header[3] |= byte(n >> 11) & 0x3
		header[4] |= byte(n >> 3)
		header[5] |= byte(n << 5)
		fmt.Printf(" after header=%0x %0b\n", header, header)

		n, err := faac.Write(header)
		check(err)
		n, err = faac.Write(frame)
		check(err)

		fmt.Printf("%x + %x = %x\n", header, frame[:4], append(header, frame[:4]...))

		// Write data
		pts := tpf * float64(i)
		fmt.Printf("pts=%f\n", pts)
		data = append(header, frame...)
		n, err = mx.WriteData(&astits.MuxerData{
			PID: 256,
			AdaptationField: af,
			PES: &astits.PESData{
				Header: &astits.PESHeader{
					OptionalHeader: &astits.PESOptionalHeader{
						MarkerBits:      2,
						PTSDTSIndicator: astits.PTSDTSIndicatorOnlyPTS,
						PTS:             &astits.ClockReference{Base: int64(pts * 90000) + pcr},
					},
					StreamID:     192, // = audio
				},
				Data: data,
			},
		})
		check(err)
		fmt.Printf("  wrote %d\n", n)
		i += 1
	}
}

func main() {
	t := NewTranscoder()
	t.Transcode("tmp/stream.mp4")
}
