package adaptiveconnect

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"testing"
)

type fingerprintDiagnosticPath struct {calls int}

func (p *fingerprintDiagnosticPath) Scope() DiagnosticScope {return ScopeTunnel}
func (p *fingerprintDiagnosticPath) Generation() uint64 {return 1}
func (p *fingerprintDiagnosticPath) LookupNetIP(context.Context,string,string)([]netip.Addr,error) {
	return []netip.Addr{netip.MustParseAddr("1.1.1.1")},nil
}
func (p *fingerprintDiagnosticPath) DialContext(context.Context,string,string)(net.Conn,error) {
	p.calls++
	return nil,errors.New("synthetic dial failure")
}

func TestDiagnosticFingerprintExplicitAdapter(t *testing.T) {
	p:=new(fingerprintDiagnosticPath)
	req:=SiberianRequest{Host:"owned.example",ControlHost:"control.example",Consent:true,Fingerprint:"utls-firefox-auto"}
	tester:=new(SiberianTester)
	if _,err:=tester.Check(context.Background(),p,req);err==nil||p.calls!=0 {t.Fatal("fallback to standard TLS")}
	report,err:=tester.CheckWithHandshake(context.Background(),p,req,func(context.Context,net.Conn,string)error{return nil})
	if err!=nil||report.Fingerprint!=req.Fingerprint||report.Evidence!="inconclusive"||p.calls!=2 {t.Fatal(report,err,p.calls)}
	req.Fingerprint="unknown"
	if _,err:=new(SiberianTester).CheckWithHandshake(context.Background(),p,req,func(context.Context,net.Conn,string)error{return nil});err==nil {t.Fatal("unknown profile accepted")}
}
