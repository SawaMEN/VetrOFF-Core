package adaptiveconnect

import (
	"context"
	"testing"
	"time"
)

func muxFixture() (MuxPolicy,MuxProfile,MuxProfile) {
	p:=DefaultMuxPolicy()
	p.Enabled=true
	p.Health.Enabled=true
	p.Health.CooldownSeconds=0
	p.Health.RecoveryThreshold=2
	a:=MuxProfile{Outbound:Outbound{Tag:"plain",Tunnel:true,Networks:[]string{"tcp"}},PairKey:"local-pair",Protocol:"vless"}
	b:=MuxProfile{Outbound:Outbound{Tag:"mux",Tunnel:true,Networks:[]string{"tcp"}},PairKey:"local-pair",Protocol:"vless",Multiplex:true,MultiplexProtocol:"yamux",ServerCompatible:true}
	return p,a,b
}

func TestMuxOptInAndCompatibility(t *testing.T) {
	p,a,b:=muxFixture()
	s:=&fakeSwitcher{}
	if r,err:=NewMuxRuntime(p,false,a,b,"tcp",s);r!=nil||err!=nil { t.Fatal(r,err) }
	disabled:=p
	disabled.Health.Enabled=false
	if r,err:=NewMuxRuntime(disabled,true,a,b,"tcp",s);r!=nil||err!=nil { t.Fatal(r,err) }
	for _,change:=range []func(*MuxProfile){
		func(v *MuxProfile){v.PairKey="different"},
		func(v *MuxProfile){v.Flow="xtls-rprx-vision"},
		func(v *MuxProfile){v.TransportMultiplex=true},
		func(v *MuxProfile){v.SpecialMode=true},
		func(v *MuxProfile){v.ServerCompatible=false},
		func(v *MuxProfile){v.MultiplexProtocol="unknown"},
		func(v *MuxProfile){v.Outbound.Hidden=true},
		func(v *MuxProfile){v.Outbound.Tunnel=false},
		func(v *MuxProfile){v.Outbound.Networks=[]string{"udp"}},
		func(v *MuxProfile){v.Outbound.Tag="plain"},
	} {
		bad:=b
		change(&bad)
		if r,err:=NewMuxRuntime(p,true,a,bad,"tcp",s);r!=nil||err==nil { t.Fatal("accepted incompatible profile",bad) }
	}
	if r,err:=NewMuxRuntime(p,true,a,b,"udp",s);r!=nil||err==nil { t.Fatal("accepted UDP") }
}

func TestMuxVerifiedAlternativeAndManual(t *testing.T) {
	p,a,b:=muxFixture()
	p.PreferMultiplex=true
	s:=&fakeSwitcher{active:"mux"}
	r,err:=NewMuxRuntime(p,true,a,b,"tcp",s)
	if err!=nil { t.Fatal(err) }
	now:=time.Unix(1000,0)
	if _,err=r.ResetNetwork(now,"mux");err!=nil { t.Fatal(err) }
	for i:=0;i<2;i++ {
		at:=now.Add(time.Duration(i)*time.Second)
		recordRuntime(t,r,at,"mux",ProbeFailed)
		recordRuntime(t,r,at,"plain",ProbePassed)
	}
	at:=now.Add(time.Second)
	d,err:=r.Tick(context.Background(),at)
	if err!=nil||d.Target!="plain"||s.active!="plain" { t.Fatal(d,err,s) }
	if !r.ManualSelection(at,"plain") { t.Fatal("manual rejected") }
	if d,err=r.Tick(context.Background(),at);err!=nil||d.Reason!="manual-selection"||s.calls!=1 { t.Fatal(d,err,s) }
}

func TestMuxInitialPreferenceAndGeneration(t *testing.T) {
	for _,prefer:=range []bool{false,true} {
		p,a,b:=muxFixture()
		p.PreferMultiplex=prefer
		s:=&fakeSwitcher{}
		r,err:=NewMuxRuntime(p,true,a,b,"tcp",s)
		if err!=nil { t.Fatal(err) }
		now:=time.Unix(1000,0)
		g:=r.Generation()
		for i:=0;i<2;i++ {
			at:=now.Add(time.Duration(i)*time.Second)
			recordRuntime(t,r,at,"plain",ProbePassed)
			recordRuntime(t,r,at,"mux",ProbePassed)
		}
		at:=now.Add(time.Second)
		if _,err=r.ResetNetwork(at,"");err!=nil { t.Fatal(err) }
		if r.Record(at,ProbeResult{g,"mux","tcp",at,ProbePassed}) { t.Fatal("stale accepted") }
		if d,err:=r.Tick(context.Background(),at);err!=nil||d.Target!="" { t.Fatal(d,err) }
		for i:=1;i<=2;i++ {
			at=now.Add(time.Duration(i+1)*time.Second)
			recordRuntime(t,r,at,"plain",ProbePassed)
			recordRuntime(t,r,at,"mux",ProbePassed)
		}
		want:="plain"
		if prefer { want="mux" }
		if d,err:=r.Tick(context.Background(),at);err!=nil||d.Target!=want||s.active!=want { t.Fatal(d,err,s) }
	}
}
