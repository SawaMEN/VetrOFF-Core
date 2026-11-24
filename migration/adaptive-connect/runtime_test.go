package adaptiveconnect

import (
	"context"
	"errors"
	"testing"
	"time"
)

type fakeSwitcher struct {
	active string
	fail bool
	calls int
}

func (s *fakeSwitcher) SwitchVerified(ctx context.Context, expected, target string) error {
	s.calls++
	if ctx.Err()!=nil { return ctx.Err() }
	if s.active != expected || s.fail { return errors.New("activation rejected") }
	s.active=target
	return nil
}

func runtimeFixture(t *testing.T, enabled bool) (*Runtime,*fakeSwitcher,time.Time) {
	t.Helper()
	p:=DefaultPolicy()
	p.Cooldown=0
	p.RecoveryThreshold=1
	s:=&fakeSwitcher{active:"primary"}
	r,err:=NewRuntime(p,[]Outbound{
		{Tag:"primary",Tunnel:true,Networks:[]string{"tcp"}},
		{Tag:"backup",Priority:1,Tunnel:true,Networks:[]string{"tcp"}},
		{Tag:"direct",Networks:[]string{"tcp"}},
		{Tag:"hidden",Tunnel:true,Hidden:true,Networks:[]string{"tcp"}},
		{Tag:"udp",Tunnel:true,Networks:[]string{"udp"}},
	},"tcp",enabled,s)
	if err!=nil { t.Fatal(err) }
	now:=time.Unix(1000,0)
	if _,err=r.ResetNetwork(now,"primary");err!=nil { t.Fatal(err) }
	return r,s,now
}

func recordRuntime(t *testing.T,r *Runtime,now time.Time,tag string,outcome ProbeOutcome) {
	t.Helper()
	if !r.Record(now,ProbeResult{r.Generation(),tag,"tcp",now,outcome}) { t.Fatal("record rejected",tag) }
}

func TestRuntimeVerifiedFailover(t *testing.T) {
	r,s,now:=runtimeFixture(t,true)
	for _, tag:=range []string{"direct","hidden","udp"} {
		if r.Record(now,ProbeResult{r.Generation(),tag,"tcp",now,ProbePassed}) { t.Fatal("ineligible outbound",tag) }
	}
	if r.Record(now,ProbeResult{r.Generation(),"primary","tcp",now,ProbeCanceled}) { t.Fatal("canceled probe accepted") }
	if r.Record(now,ProbeResult{r.Generation(),"primary","udp",now,ProbeFailed}) { t.Fatal("wrong network accepted") }
	recordRuntime(t,r,now,"backup",ProbePassed)
	recordRuntime(t,r,now,"primary",ProbeFailed)
	if d,err:=r.Tick(context.Background(),now);err!=nil||d.Target!="" { t.Fatal(d,err) }
	now=now.Add(time.Second)
	recordRuntime(t,r,now,"primary",ProbeFailed)
	d,err:=r.Tick(context.Background(),now)
	if err!=nil||d.Target!="backup"||s.active!="backup"||s.calls!=1 { t.Fatal(d,err,s) }
	if d,err=r.Tick(context.Background(),now);err!=nil||d.Current!="backup"||d.Target!="" { t.Fatal(d,err) }
}

func TestRuntimeRejectedActivation(t *testing.T) {
	r,s,now:=runtimeFixture(t,true)
	recordRuntime(t,r,now,"backup",ProbePassed)
	recordRuntime(t,r,now,"primary",ProbeFailed)
	now=now.Add(time.Second)
	recordRuntime(t,r,now,"primary",ProbeFailed)
	s.fail=true
	if _,err:=r.Tick(context.Background(),now);err==nil { t.Fatal("failure ignored") }
	if s.active!="primary" { t.Fatal(s.active) }
	if d,err:=r.Tick(context.Background(),now);err!=nil||d.Target!=""||s.calls!=1 { t.Fatal(d,err,s) }
	now=now.Add(time.Second)
	recordRuntime(t,r,now,"backup",ProbePassed)
	s.fail=false
	if _,err:=r.Tick(context.Background(),now);err!=nil||s.active!="backup" { t.Fatal(err,s) }
}

func TestRuntimeManualAndReset(t *testing.T) {
	r,s,now:=runtimeFixture(t,true)
	old:=r.Generation()
	if !r.ManualSelection(now,"primary") { t.Fatal("manual selection rejected") }
	recordRuntime(t,r,now,"backup",ProbePassed)
	recordRuntime(t,r,now,"primary",ProbeFailed)
	now=now.Add(time.Second)
	recordRuntime(t,r,now,"primary",ProbeFailed)
	if d,err:=r.Tick(context.Background(),now);err!=nil||d.Target!=""||s.calls!=0 { t.Fatal(d,err,s) }
	if err:=r.ResumeAutomatic(now,"primary");err!=nil { t.Fatal(err) }
	if r.Record(now,ProbeResult{old,"backup","tcp",now,ProbePassed}) { t.Fatal("old generation accepted") }
	if d,err:=r.Tick(context.Background(),now);err!=nil||d.Target!="" { t.Fatal(d,err) }
}

func TestRuntimeDisabled(t *testing.T) {
	r,s,now:=runtimeFixture(t,false)
	if r.Record(now,ProbeResult{r.Generation(),"backup","tcp",now,ProbePassed}) { t.Fatal("disabled accepted sample") }
	if d,err:=r.Tick(context.Background(),now);err!=nil||d.Reason!="disabled"||s.calls!=0 { t.Fatal(d,err,s) }
}
