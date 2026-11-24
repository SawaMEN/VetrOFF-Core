package adaptiveconnect

import (
	"testing"
	"time"
)

func TestInvalidationRejectsReplay(t *testing.T) {
	p:=DefaultPolicy()
	p.RecoveryThreshold=1
	s,err:=NewSession(p,[]Candidate{{ID:"a"}},true)
	if err!=nil { t.Fatal(err) }
	now:=time.Unix(1000,0)
	g:=s.Generation()
	sample:=Sample{ID:"a",At:now,Success:true}
	if !s.Record(g,now,sample) { t.Fatal("initial record rejected") }
	s.Invalidate(g,"a")
	if s.Record(g,now,sample) { t.Fatal("replayed record accepted") }
	if d:=s.Decide(now);d.Target!="" { t.Fatal(d) }
	now=now.Add(time.Second)
	sample.At=now
	if !s.Record(g,now,sample) { t.Fatal("fresh record rejected") }
	if d:=s.Decide(now);d.Target!="a" { t.Fatal(d) }
}
