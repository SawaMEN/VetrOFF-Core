package config

import (
	"encoding/json"
	"testing"

	"github.com/sagernet/sing-box/option"
)

func TestVetrOFFWarpMTUDiscoveryPolicy(t *testing.T) {
	enabled, disabled := true, false
	for _, tt := range []struct {
		name string
		flag *bool
	}{
		{"unset", nil}, {"enabled", &enabled}, {"disabled", &disabled},
	} {
		t.Run(tt.name, func(t *testing.T) {
			opts := &option.WireGuardEndpointOptions{MTU: 1420, MTUDiscovery: tt.flag}
			converted := wireGuardToWarpEndpoint(opts, "")
			if converted.MTU != 1420 {
				t.Fatalf("MTU changed to %d", converted.MTU)
			}
			if converted.MTUDiscovery != tt.flag {
				t.Fatal("MTU discovery policy was not preserved")
			}
			raw, err := json.Marshal(converted)
			if err != nil {
				t.Fatal(err)
			}
			var fields map[string]json.RawMessage
			if err := json.Unmarshal(raw, &fields); err != nil {
				t.Fatal(err)
			}
			value, exists := fields["mtu_discovery"]
			if tt.flag == nil {
				if exists {
					t.Fatalf("unset policy serialized as %s", value)
				}
			} else {
				var got bool
				if !exists || json.Unmarshal(value, &got) != nil || got != *tt.flag {
					t.Fatalf("policy serialized as %s", value)
				}
			}
		})
	}
}
