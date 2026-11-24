package adaptiveconnect

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"sync/atomic"
	"testing"
)

type diagnosticFake struct {
	scope DiagnosticScope
	generation atomic.Uint64
	lookups atomic.Int32
	dials atomic.Int32
	changeOnLookup bool
}

func (p *diagnosticFake) Scope() DiagnosticScope { return p.scope }
func (p *diagnosticFake) Generation() uint64 { return p.generation.Load() }
func (p *diagnosticFake) LookupNetIP(ctx context.Context,network,host string)([]netip.Addr,error) {
	p.lookups.Add(1)
	if p.changeOnLookup { p.generation.Add(1) }
	return []netip.Addr{netip.MustParseAddr("1.1.1.1")},nil
}
func (p *diagnosticFake) DialContext(ctx context.Context,network,address string)(net.Conn,error) {
	p.dials.Add(1)
	return nil,errors.New("synthetic dial failure")
}

func TestDiagnosticConsentAndScope(t *testing.T) {
	p:=&diagnosticFake{scope:ScopePhysical}
	p.generation.Store(1)
	var tester SiberianTester
	req:=SiberianRequest{Host:"test.example.com",ControlHost:"control.example.com"}
	if _,err:=tester.Check(context.Background(),p,req);err==nil { t.Fatal("missing consent accepted") }
	req.Consent=true
	p.scope="unknown"
	if _,err:=tester.Check(context.Background(),p,req);err==nil { t.Fatal("unknown scope accepted") }
	if p.lookups.Load()!=0 || p.dials.Load()!=0 { t.Fatal("unexpected network I/O") }
	p.scope=ScopeTunnel
	r,err:=tester.Check(context.Background(),p,req)
	if err!=nil || r.Scope!=ScopeTunnel || r.Evidence!="inconclusive" || len(r.Baseline)!=2 || len(r.Burst)!=0 { t.Fatal(r,err) }
	if p.dials.Load()!=2 { t.Fatal("burst ran after baseline failure") }
	if _,err=tester.Check(context.Background(),p,req);err==nil { t.Fatal("cooldown not enforced") }
}

func TestDiagnosticPathChange(t *testing.T) {
	p:=&diagnosticFake{scope:ScopePhysical,changeOnLookup:true}
	p.generation.Store(1)
	var tester SiberianTester
	r,err:=tester.Check(context.Background(),p,SiberianRequest{Host:"a.example.com",ControlHost:"b.example.com",Consent:true})
	if err==nil || r.Evidence!="inconclusive" || p.dials.Load()!=0 { t.Fatal(r,err) }
}

func TestDiagnosticClassification(t *testing.T) {
	ok:=DiagnosticAttempt{Stage:"tls",Outcome:"ok"}
	timeout:=DiagnosticAttempt{Stage:"tls",Outcome:"timeout"}
	r:=SiberianReport{Baseline:[]DiagnosticAttempt{ok,ok},Burst:[]DiagnosticAttempt{ok,ok,ok,ok},Control:&ok}
	if classifyDiagnostic(r)!="no-signal" { t.Fatal(r) }
	r.Burst[0]=timeout
	if classifyDiagnostic(r)!="asymmetry" { t.Fatal(r) }
	r.Control=&timeout
	if classifyDiagnostic(r)!="inconclusive" { t.Fatal(r) }
	r.Control=&ok
	r.Burst[1]=DiagnosticAttempt{Stage:"tcp",Outcome:"timeout"}
	if classifyDiagnostic(r)!="inconclusive" { t.Fatal(r) }
}

func TestDiagnosticPublicAddress(t *testing.T) {
	pub:=netip.MustParseAddr("1.1.1.1")
	other:=netip.MustParseAddr("8.8.8.8")
	for _,private:=range []string{"127.0.0.1","10.0.0.1","100.64.1.1","::1","fe80::1","192.0.2.1","::ffff:127.0.0.1"} {
		if _,err:=sharedDiagnosticIP([]netip.Addr{pub,netip.MustParseAddr(private)},[]netip.Addr{pub});err==nil { t.Fatal("accepted",private) }
	}
	if ip,err:=sharedDiagnosticIP([]netip.Addr{pub},[]netip.Addr{pub});err!=nil || ip!=pub { t.Fatal(ip,err) }
	if _,err:=sharedDiagnosticIP([]netip.Addr{pub,other},[]netip.Addr{pub,other});err==nil { t.Fatal("ambiguous IP accepted") }
}
