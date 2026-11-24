package tstun

import (
	"errors"
	"testing"
)

type vetroffReturnPath struct {
	headroom int
}

func (p *vetroffReturnPath) ReturnHeadroom() int { return p.headroom }
func (*vetroffReturnPath) ReturnPackets(packets [][]byte) [][]byte { return packets }

func TestVetrOFFReturnPathValidation(t *testing.T) {
	var wrapper Wrapper
	for _, headroom := range []int{0, PacketStartOffset} {
		valid := &vetroffReturnPath{headroom: headroom}
		if err := wrapper.SetReturnPath(valid); err != nil {
			t.Fatalf("valid headroom rejected: %v", err)
		}
		previous := wrapper.returnPath.Load()
		if previous == nil || previous.returnPath != valid || previous.headroom != headroom {
			t.Fatal("return path not published correctly")
		}
		if err := wrapper.SetReturnPath(&vetroffReturnPath{headroom: PacketStartOffset + 1}); err == nil {
			t.Fatal("oversized headroom accepted")
		}
		if wrapper.returnPath.Load() != previous {
			t.Fatal("failed replacement changed active return path")
		}
	}
}

func TestVetrOFFInputRequiresDevice(t *testing.T) {
	var wrapper Wrapper
	if _, err := wrapper.InputPackets(nil); !errors.Is(err, errNoInputDevice) {
		t.Fatalf("expected missing input device error, got %v", err)
	}
}
