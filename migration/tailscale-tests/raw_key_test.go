package key

import (
	"testing"

	"go4.org/mem"
)

func TestVetrOFFRawKeyRoundTrip(t *testing.T) {
	node := NewNode()
	raw := node.Raw32()
	restored := NodePrivateFromRaw32(mem.B(raw[:]))
	if !node.Equal(restored) || node.Public() != restored.Public() {
		t.Fatal("node private key round trip changed the key")
	}
	// Synthetic test bytes, not a real credential.
	var input [32]byte
	for i := range input {
		input[i] = byte(i + 1)
	}
	a := DiscoPrivateFromRaw32(mem.B(input[:]))
	b := DiscoPrivateFromRaw32(mem.B(input[:]))
	if a.IsZero() || !a.Equal(b) || a.Public() != b.Public() {
		t.Fatal("raw disco key conversion is not deterministic")
	}
	input[0] ^= 8
	if a.Equal(DiscoPrivateFromRaw32(mem.B(input[:]))) {
		t.Fatal("different raw keys were treated as identical")
	}
}
