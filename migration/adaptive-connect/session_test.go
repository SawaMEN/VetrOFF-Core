package adaptiveconnect

import (
	"testing"
	"time"
)

func TestSessionNetworkChange(t *testing.T) {
	p := DefaultPolicy()
	p.RecoveryThreshold = 1
	p.Cooldown = 0
	s, err := NewSession(p, []Candidate{{ID:"a"}, {ID:"b",Priority:1}}, true)
	if err != nil { t.Fatal(err) }
	now := time.Unix(1000,0)
	g := s.Generation()
	if !s.Record(g, now, Sample{ID:"b",At:now,Success:true}) { t.Fatal("record rejected") }
	if d:=s.Decide(now); d.Target!="b" { t.Fatal(d) }
	next,err:=s.ResetNetwork(now.Add(time.Second),"a")
	if err!=nil || next==g { t.Fatalf("%v %v", next,err) }
	if s.Record(g,now.Add(time.Second),Sample{ID:"b",At:now.Add(time.Second),Success:true}) { t.Fatal("old generation accepted") }
	if s.ConfirmActive(g,now.Add(time.Second),"b") { t.Fatal("old confirmation accepted") }
	if d:=s.Decide(now.Add(time.Second)); d.Target!="" || d.Current!="a" { t.Fatal(d) }
}

func TestSessionManualAndDisabled(t *testing.T) {
	p:=DefaultPolicy()
	p.RecoveryThreshold=1
	now:=time.Unix(1000,0)
	for _, enabled:=range []bool{false,true} {
		s,err:=NewSession(p,[]Candidate{{ID:"a"}},enabled)
		if err!=nil { t.Fatal(err) }
		g:=s.Generation()
		s.SetManual(true)
		if ok:=s.Record(g,now,Sample{ID:"a",At:now,Success:true}); ok!=enabled { t.Fatal(ok) }
		if d:=s.Decide(now); d.Target!="" { t.Fatal(d) }
		s.SetManual(false)
		if d:=s.Decide(now); (d.Target=="a")!=enabled { t.Fatal(d) }
	}
}

func TestSessionInvalidNetworkReset(t *testing.T) {
	s,err:=NewSession(DefaultPolicy(),[]Candidate{{ID:"a"}},true)
	if err!=nil { t.Fatal(err) }
	g:=s.Generation()
	if _,err=s.ResetNetwork(time.Now(),"unknown"); err==nil { t.Fatal("unknown candidate accepted") }
	if s.Generation()!=g { t.Fatal("failed reset changed generation") }
}
