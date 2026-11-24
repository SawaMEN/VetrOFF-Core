package adaptiveconnect

import (
	"errors"
	"strings"
)

// MuxProfile describes an already constructed, validated outbound. The core
// adapter must derive PairKey from the actual effective endpoint, credential,
// security and transport configuration with only the mux options excluded.
// It is opaque, local-only, and must never be exported or logged.
type MuxProfile struct {
	Outbound Outbound
	PairKey string
	Protocol string
	Flow string
	Multiplex bool
	MultiplexProtocol string
	TransportMultiplex bool
	ServerCompatible bool
	// SpecialMode includes reverse, testpre, XDrive and other modes whose
	// compatibility has not been reviewed. Unknown modes must be excluded.
	SpecialMode bool
}

type MuxPolicy struct {
	Enabled bool
	PreferMultiplex bool
	Health Config
}

func DefaultMuxPolicy() MuxPolicy {
	return MuxPolicy{Health:DefaultConfig()}
}

func muxProfilePair(a,b MuxProfile) error {
	if a.PairKey=="" || a.PairKey!=b.PairKey ||
		a.Outbound.Tag==b.Outbound.Tag ||
		a.Protocol!=b.Protocol || a.Flow!=b.Flow ||
		a.Multiplex==b.Multiplex {
		return errors.New("expected two matched, distinct mux profiles")
	}
	for _,p:=range []MuxProfile{a,b} {
		if strings.TrimSpace(p.Outbound.Tag)=="" ||
			!p.Outbound.Tunnel || p.Outbound.Hidden ||
			strings.HasPrefix(p.Outbound.Tag,"§hide§") ||
			p.TransportMultiplex || p.SpecialMode || p.Flow!="" {
			return errors.New("unsupported multiplex profile")
		}
		switch p.Protocol {
		case "vless","vmess","trojan","shadowsocks":
		default: return errors.New("unreviewed multiplex protocol")
		}
		if p.Multiplex {
			if !p.ServerCompatible { return errors.New("server multiplex compatibility not established") }
			switch p.MultiplexProtocol {
			case "h2mux","smux","yamux","rmux":
			default: return errors.New("unreviewed multiplex implementation")
			}
		} else if p.MultiplexProtocol!="" {
			return errors.New("non-multiplex profile contains multiplex protocol")
		}
	}
	return nil
}

// NewMuxRuntime reuses the existing Adaptive Connect state machine and
// verified switch interface. The mux pair is a single logical endpoint;
// do not run another independent controller for the same selector.
// Both profiles must already exist in the core and be validated locally.
// It never edits a live mux session, stream limits, flow or transport.
// Automatic operation requires both policy flags and explicit local consent.
func NewMuxRuntime(policy MuxPolicy,localOptIn bool,a,b MuxProfile,network string,switcher Switcher)(*Runtime,error) {
	health,err:=policy.Health.Policy()
	if err!=nil { return nil,err }
	if !policy.Enabled || !policy.Health.Enabled || !localOptIn { return nil,nil }
	if err=muxProfilePair(a,b);err!=nil { return nil,err }
	// This first candidate is TCP-only. UDP multiplex behavior requires
	// independent end-to-end validation before it can be enabled.
	if network!="tcp" { return nil,errors.New("adaptive multiplex currently supports TCP only") }
	outbounds:=[]Outbound{a.Outbound,b.Outbound}
	for i,p:=range []MuxProfile{a,b} {
		if p.Multiplex==policy.PreferMultiplex { outbounds[i].Priority=0 } else { outbounds[i].Priority=1 }
		found:=false
		for _,n:=range p.Outbound.Networks { if n==network { found=true;break } }
		if !found { return nil,errors.New("both profiles must support TCP") }
	}
	return NewRuntime(health,outbounds,network,true,switcher)
}
