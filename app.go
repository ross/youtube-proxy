package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"os"
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

	for frame := range frameCh {
		fmt.Printf("received frame=*** %d\n", len(frame))
	}
}

func main() {
	t := NewTranscoder()
	t.Transcode("tmp/stream.mp4")
}
