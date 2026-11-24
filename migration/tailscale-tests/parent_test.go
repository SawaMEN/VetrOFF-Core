package dnsname

import "testing"

func TestVetrOFFFQDNParent(t *testing.T) {
	for _, tc := range []struct {
		name FQDN
		want FQDN
	}{
		{"host.example.", "example."},
		{"a.b.example.", "b.example."},
		{"example.", ""},
		{".", ""},
		{"", ""},
	} {
		if got := tc.name.Parent(); got != tc.want {
			t.Errorf("Parent(%q) = %q, want %q", tc.name, got, tc.want)
		}
	}
}
