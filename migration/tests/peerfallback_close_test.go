package peerfallback

import (
    "net/netip"
    "testing"
    "time"

    "github.com/sagernet/sing/common/logger"
)

// A stalled device operation must not hold up shutdown. The timing values
// are instance-local and may be adjusted without changing global test knobs.
func TestVetrOFFCloseDuringMove(t *testing.T) {
    entered := make(chan struct{})
    release := make(chan struct{})
    finished := make(chan struct{})
    defer close(release)
    f := New(Options{
        Logger: logger.NOP(),
        Addresses: []netip.AddrPort{
            netip.MustParseAddrPort("127.0.0.1:10001"),
            netip.MustParseAddrPort("127.0.0.1:10002"),
        },
        Status: func() (time.Time, uint64) { return time.Time{}, 1 },
        Move: func(netip.AddrPort) bool {
            close(entered)
            <-release
            return true
        },
    })
    f.wait = 0
    go func() { defer close(finished); f.Check() }()
    select {
    case <-entered:
    case <-time.After(2 * time.Second):
        t.Fatal("fallback did not start")
    }
    closed := make(chan struct{})
    go func() { f.Close(); close(closed) }()
    select {
    case <-closed:
    case <-time.After(2 * time.Second):
        t.Fatal("Close waited for a blocked move")
    }
}
