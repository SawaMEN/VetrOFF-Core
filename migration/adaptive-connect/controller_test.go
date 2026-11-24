package adaptiveconnect

import (
	"context"
	"errors"
	"testing"
	"time"
)

func controllerFixture(t *testing.T)(*Controller,*fakeSwitcher,*fakeSwitcher,time.Time) {
	t.Helper()
	cfg:=DefaultControllerConfig()
	cfg.Connect.Enabled=true
	cfg.Connect.CooldownSeconds=0
	cfg.Connect.RecoveryThreshold=1
	cfg.Pool=PoolPolicy{Enabled:true,MaxTotal:2,MaxPerProfile:2,MaxPending:2}
	cfg.History.Enabled=true
	tcp,udp:=&fakeSwitcher{active:"a"},&fakeSwitcher{active:"b"}
	out:=[]Outbound{{Tag:"a",Tunnel:true,Networks:[]string{"tcp","udp"}},{Tag:"b",Tunnel:true,Priority:1,Networks:[]string{"tcp","udp"}}}
	c,err:=NewController(cfg,ControllerConsent{Connect:true,Pool:true,History:true},[]RouteBinding{
		{Network:"tcp",Outbounds:out,Switcher:tcp,ProfileKeys:map[string]HistoryKey{"a":hk(1),"b":hk(2)}},
		{Network:"udp",Outbounds:out,Switcher:udp,ProfileKeys:map[string]HistoryKey{"a":hk(1),"b":hk(2)}},
	})
	if err!=nil { t.Fatal(err) }
	return c,tcp,udp,time.Unix(1000,0)
}

func TestControllerLifecycle(t *testing.T) {
	c,tcp,udp,now:=controllerFixture(t)
	if _,err:=c.BeginProbe("tcp");!errors.Is(err,ErrControllerInactive) { t.Fatal(err) }
	active:=map[string]string{"tcp":"a","udp":"b"}
	g,err:=c.ResetNetwork(now,hk(9),active)
	if err!=nil { t.Fatal(err) }
	ticket,err:=c.BeginProbe("tcp")
	if err!=nil { t.Fatal(err) }
	lease,err:=c.Reserve(g,"tcp","a")
	if err!=nil { t.Fatal(err) }
	second,err:=c.Reserve(g,"udp","a")
	if err!=nil { t.Fatal(err) }
	if _,err=c.Reserve(g,"udp","b");!errors.Is(err,ErrPoolLimit) { t.Fatal(err) }
	if _,err=c.ResetNetwork(now,hk(8),map[string]string{"tcp":"b","udp":"invalid"});err==nil { t.Fatal("bad reset accepted") }
	if c.State().Generation!=g { t.Fatal("partial reset") }
	if !c.Record(now,ticket,"b",now,ProbePassed)||!c.Record(now,ticket,"a",now,ProbeFailed) { t.Fatal("record rejected") }
	now=now.Add(time.Second)
	if !c.Record(now,ticket,"a",now,ProbeFailed) { t.Fatal("second failure rejected") }
	d,err:=c.Tick(context.Background(),"tcp",now)
	if err!=nil||d.Target!="b"||tcp.active!="b"||udp.calls!=0 { t.Fatal(d,err,tcp,udp) }
	next,err:=c.ResetNetwork(now,hk(8),map[string]string{"tcp":"b","udp":"b"})
	if err!=nil||next==g { t.Fatal(next,err) }
	if c.Record(now,ticket,"b",now,ProbePassed) { t.Fatal("old result accepted") }
	if err=lease.Commit();!errors.Is(err,ErrPoolStale) { t.Fatal(err) }
	if c.State().Pool.Total!=2 { t.Fatal("old reservations lost") }
	lease.Release();second.Release()
	if _,err=c.Reserve(g,"tcp","a");!errors.Is(err,ErrPoolStale) { t.Fatal(err) }
	if c.history.Summary(now,hk(9),hk(2),"tcp").Samples!=1||c.history.Summary(now,hk(8),hk(2),"tcp").Samples!=0 { t.Fatal("history contamination") }
	c.Close()
	c.Close()
	if c.State().Ready||!c.State().Closed||c.State().HistoryEnabled { t.Fatal(c.State()) }
	if _,err=c.BeginProbe("tcp");!errors.Is(err,ErrControllerInactive) { t.Fatal(err) }
}

func TestControllerManualResumeAndTicketOwner(t *testing.T) {
	c,_,_,now:=controllerFixture(t)
	other,_,_,_:=controllerFixture(t)
	active:=map[string]string{"tcp":"a","udp":"b"}
	for _,x:=range []*Controller{c,other} {
		if _,err:=x.ResetNetwork(now,hk(9),active);err!=nil { t.Fatal(err) }
	}
	old,err:=c.BeginProbe("tcp")
	if err!=nil { t.Fatal(err) }
	if other.Record(now,old,"b",now,ProbePassed) { t.Fatal("foreign ticket accepted") }
	if !c.ManualSelection("tcp",now,"a") { t.Fatal("manual rejected") }
	if d,err:=c.Tick(context.Background(),"tcp",now);err!=nil||d.Reason!="manual-selection" { t.Fatal(d,err) }
	if err=c.ResumeAutomatic("tcp",now,"a");err!=nil { t.Fatal(err) }
	if c.Record(now,old,"b",now,ProbePassed) { t.Fatal("old runtime generation accepted") }
	fresh,err:=c.BeginProbe("tcp")
	if err!=nil||!c.Record(now,fresh,"b",now,ProbePassed) { t.Fatal(err) }
	c.ClearHistory()
	if c.history.Summary(now,hk(9),hk(2),"tcp").Samples!=0 { t.Fatal("history retained") }
}

func TestControllerOptInAndMux(t *testing.T) {
	cfg:=DefaultControllerConfig()
	if c,err:=NewController(cfg,ControllerConsent{Connect:true},nil);c!=nil||err!=nil { t.Fatal(c,err) }
	cfg.Connect.Enabled=true
	if c,err:=NewController(cfg,ControllerConsent{},nil);c!=nil||err!=nil { t.Fatal(c,err) }
	_,a,b:=muxFixture()
	cfg.MuxEnabled=true
	cfg.PreferMultiplex=true
	cfg.Connect.RecoveryThreshold=1
	s:=&fakeSwitcher{}
	c,err:=NewController(cfg,ControllerConsent{Connect:true,Mux:true},[]RouteBinding{
		{Network:"tcp",MuxPair:&[2]MuxProfile{a,b},Switcher:s},
	})
	if err!=nil { t.Fatal(err) }
	now:=time.Unix(1000,0)
	g,err:=c.ResetNetwork(now,HistoryKey{},map[string]string{"tcp":""})
	if err!=nil { t.Fatal(err) }
	ticket,err:=c.BeginProbe("tcp")
	if err!=nil { t.Fatal(err) }
	for _,tag:=range []string{"mux","plain"} {
		if !c.Record(now,ticket,tag,now,ProbePassed) { t.Fatal(tag) }
	}
	if d,err:=c.Tick(context.Background(),"tcp",now);err!=nil||d.Target!="mux" { t.Fatal(d,err) }
	if l,err:=c.Reserve(g,"tcp","mux");l!=nil||err!=nil { t.Fatal(l,err) }
}
