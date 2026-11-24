package adaptiveconnect

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"testing"
)

type addressCheckPath struct {
	generation uint64
	dials,lookups int
	destination string
	change bool
}
func (p *addressCheckPath) Scope() DiagnosticScope{return ScopeTunnel}
func (p *addressCheckPath) Generation() uint64{return p.generation}
func (p *addressCheckPath) LookupNetIP(context.Context,string,string)([]netip.Addr,error){p.lookups++;return nil,errors.New("unexpected DNS")}
func (p *addressCheckPath) DialContext(_ context.Context,network,address string)(net.Conn,error){
	p.dials++
	p.destination=address
	if p.change {p.generation++}
	return nil,errors.New("synthetic failure")
}
func TestCheckAddressPinnedNoDNS(t *testing.T){
	p:=&addressCheckPath{generation:1}
	r:=AddressRequest{Host:"owned.example",IP:netip.MustParseAddr("1.1.1.1")}
	tester:=new(SiberianTester)
	if _,err:=tester.CheckAddress(context.Background(),p,r);err==nil {t.Fatal("consent omitted")}
	r.Consent=true
	report,err:=tester.CheckAddress(context.Background(),p,r)
	if err!=nil||!report.Complete||report.Address==nil||report.Address.TCP.Outcome!="error"||p.lookups!=0||p.dials!=1||p.destination!="1.1.1.1:443" {t.Fatal(report,err,p)}
	if _,err=tester.CheckAddress(context.Background(),p,r);err==nil {t.Fatal("cooldown bypass")}
	r.IP=netip.MustParseAddr("127.0.0.1")
	if _,err=new(SiberianTester).CheckAddress(context.Background(),p,r);err==nil||p.dials!=1 {t.Fatal("private address accepted")}
	r.IP=netip.MustParseAddr("1.1.1.1")
	p.change=true
	report,err=new(SiberianTester).CheckAddress(context.Background(),p,r)
	if err==nil||report.Complete {t.Fatal("stale report marked complete")}
}
