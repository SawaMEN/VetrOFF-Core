package adaptiveconnect

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"testing"
	"time"
)

type cancellationDiagnosticPath struct {
	cancel context.CancelFunc
	wait bool
	dials int
}

func (p *cancellationDiagnosticPath) Scope() DiagnosticScope {return ScopeTunnel}
func (p *cancellationDiagnosticPath) Generation() uint64 {return 1}
func (p *cancellationDiagnosticPath) LookupNetIP(context.Context,string,string)([]netip.Addr,error) {
	return []netip.Addr{netip.MustParseAddr("1.1.1.1")},nil
}
func (p *cancellationDiagnosticPath) DialContext(ctx context.Context,_,_ string)(net.Conn,error) {
	p.dials++
	if p.cancel!=nil {p.cancel()}
	if p.wait {<-ctx.Done()}
	// Model a transport returning a generic socket error after cancellation.
	return nil,errors.New("socket closed")
}

func TestAddressParentCancellationIsIncomplete(t *testing.T) {
	ctx,cancel:=context.WithCancel(context.Background())
	defer cancel()
	p:=&cancellationDiagnosticPath{cancel:cancel}
	report,err:=new(SiberianTester).CheckAddress(ctx,p,AddressRequest{Host:"owned.example",IP:netip.MustParseAddr("1.1.1.1"),Consent:true})
	if !errors.Is(err,context.Canceled)||report.Complete||report.Address==nil||report.Address.TCP.Outcome!="canceled"||p.dials!=1 {
		t.Fatal(report,err,p.dials)
	}
}

func TestAddressParentDeadlineIsIncomplete(t *testing.T) {
	ctx,cancel:=context.WithTimeout(context.Background(),100*time.Millisecond)
	defer cancel()
	p:=&cancellationDiagnosticPath{wait:true}
	report,err:=new(SiberianTester).CheckAddress(ctx,p,AddressRequest{Host:"owned.example",IP:netip.MustParseAddr("1.1.1.1"),Consent:true})
	if !errors.Is(err,context.DeadlineExceeded)||report.Complete||report.Address==nil||report.Address.TCP.Outcome!="timeout" {
		t.Fatal(report,err)
	}
}

func TestConnectivityCancellationDoesNotContinue(t *testing.T) {
	ctx,cancel:=context.WithCancel(context.Background())
	defer cancel()
	p:=&cancellationDiagnosticPath{cancel:cancel}
	report,err:=new(SiberianTester).CheckConnectivity(ctx,p,ConnectivityRequest{Host:"owned.example",Consent:true})
	if !errors.Is(err,context.Canceled)||report.Complete||p.dials!=1||len(report.Families)!=1||
		len(report.Families[0].Addresses)!=1||report.Families[0].Addresses[0].TCP.Outcome!="canceled" {
		t.Fatal(report,err,p.dials)
	}
}

func TestDiagnosticOperationErrorPreservesSuccess(t *testing.T) {
	ctx,cancel:=context.WithCancel(context.Background())
	cancel()
	if diagnosticOperationError(ctx,nil)!=nil {t.Fatal("success rewritten")}
	if !errors.Is(diagnosticOperationError(ctx,errors.New("closed")),context.Canceled) {t.Fatal("lost cancellation")}
}
