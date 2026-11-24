package derpserver

import (
	"context"
	"net/netip"
	"testing"

	"github.com/sagernet/tailscale/types/key"
)

func TestVetrOFFStaticClientAdmission(t *testing.T) {
	allowed := key.NewNode().Public()
	other := key.NewNode().Public()
	var server Server
	ctx := context.Background()
	ip := netip.MustParseAddr("192.0.2.1")

	// An empty policy must preserve the upstream public-server behavior.
	if err := server.verifyClient(ctx, other, nil, ip); err != nil {
		t.Fatalf("unexpected rejection with no policy: %v", err)
	}
	server.SetVerifyClientKeys([]key.NodePublic{allowed})
	if err := server.verifyClient(ctx, allowed, nil, ip); err != nil {
		t.Fatalf("listed client denied: %v", err)
	}
	if err := server.verifyClient(ctx, other, nil, ip); err == nil {
		t.Fatal("unlisted client admitted despite static allowlist")
	}
	server.SetVerifyClientKeys([]key.NodePublic{other})
	if err := server.verifyClient(ctx, allowed, nil, ip); err == nil {
		t.Fatal("removed client still admitted")
	}
	if err := server.verifyClient(ctx, other, nil, ip); err != nil {
		t.Fatalf("newly listed client denied: %v", err)
	}
}
