package adapter

import "testing"

// An existing fork outbound may implement IsReady only. Do not call a
// detailed status method through the embedded (unimplemented) interface.
type vetroffLegacyOutbound struct {
	Outbound
	ready bool
}

func (o *vetroffLegacyOutbound) IsReady() bool { return o.ready }

type vetroffDetailedOutbound struct {
	vetroffLegacyOutbound
	status Status
}

func (o *vetroffDetailedOutbound) Status() Status { return o.status }

func TestVetrOFFPathStatusCompatibility(t *testing.T) {
	ready := &vetroffLegacyOutbound{ready: true}
	connecting := &vetroffLegacyOutbound{}
	searching := &vetroffDetailedOutbound{
		vetroffLegacyOutbound: vetroffLegacyOutbound{ready: true},
		status: StatusSearching,
	}
	for _, tc := range []struct {
		path []Outbound
		want Status
		first Outbound
	}{
		{nil, StatusReady, nil},
		{[]Outbound{ready}, StatusReady, nil},
		{[]Outbound{ready, connecting, searching}, StatusConnecting, connecting},
		{[]Outbound{ready, searching, connecting}, StatusSearching, searching},
	} {
		got, first := PathStatus(tc.path)
		if got != tc.want || first != tc.first {
			t.Fatalf("PathStatus = %v, %T; want %v, %T", got, first, tc.want, tc.first)
		}
	}
}
