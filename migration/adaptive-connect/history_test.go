package adaptiveconnect

import (
	"context"
	"sync"
	"testing"
	"time"
)

func hk(n byte)HistoryKey { return HistoryKey{n} }

func historyFixture(t *testing.T,maxSeries,maxSamples int)*AvailabilityHistory {
	t.Helper()
	h,err:=NewAvailabilityHistory(HistoryPolicy{Enabled:true,Retention:time.Hour,MaxSeries:maxSeries,MaxSamples:maxSamples},true)
	if err!=nil { t.Fatal(err) }
	return h
}

func TestHistoryLimitsAndRetention(t *testing.T) {
	h:=historyFixture(t,2,3)
	now:=time.Unix(10000,0)
	for i:=0;i<5;i++ {
		at:=now.Add(time.Duration(i)*time.Second)
		if !h.record(at,hk(1),hk(2),"tcp",at,i%2==0) { t.Fatal("record rejected") }
	}
	at:=now.Add(5*time.Second)
	s:=h.Summary(at,hk(1),hk(2),"tcp")
	if s.Samples!=3 || s.Passed!=2 || s.Failed!=1 { t.Fatal(s) }
	if h.record(at,hk(1),hk(2),"tcp",now,true) { t.Fatal("old sample accepted") }
	if h.record(at,hk(1),hk(2),"tcp",at.Add(time.Second),true) { t.Fatal("future sample accepted") }
	if !h.record(at,hk(1),hk(3),"tcp",at,true) { t.Fatal("second series rejected") }
	at=at.Add(time.Second)
	if !h.record(at,hk(2),hk(2),"udp",at,true) { t.Fatal("third series rejected") }
	if h.Summary(at,hk(1),hk(2),"tcp").Samples!=0 { t.Fatal("oldest not evicted") }
	if h.Summary(at,hk(2),hk(2),"tcp").Samples!=0 { t.Fatal("protocol isolation failed") }
	if h.Summary(at.Add(time.Hour+time.Second),hk(2),hk(2),"udp").Samples!=0 { t.Fatal("expired data retained") }
}

func TestHistoryProbeOrderAndClear(t *testing.T) {
	h:=historyFixture(t,8,8)
	now:=time.Unix(10000,0)
	for i:=0;i<3;i++ {
		at:=now.Add(time.Duration(i)*time.Second)
		h.record(at,hk(1),hk(2),"tcp",at,false)
		h.record(at,hk(1),hk(3),"tcp",at,true)
	}
	now=now.Add(3*time.Second)
	input:=[]HistoryKey{hk(2),hk(4),hk(3)}
	order:=h.ProbeOrder(now,hk(1),"tcp",input)
	if order[0]!=hk(3)||order[1]!=hk(4)||order[2]!=hk(2)||input[0]!=hk(2) { t.Fatal(order,input) }
	order=h.ProbeOrder(now,hk(9),"tcp",input)
	if order[0]!=hk(2)||order[2]!=hk(3) { t.Fatal("other network affected",order) }
	h.Clear()
	if h.Summary(now,hk(1),hk(3),"tcp").Samples!=0 { t.Fatal("not cleared") }
}

func TestHistoryRuntimeAcceptedSamplesOnly(t *testing.T) {
	r,s,now:=runtimeFixture(t,true)
	h:=historyFixture(t,8,8)
	old:=r.Generation()
	for _,outcome:=range []ProbeOutcome{ProbeCanceled,ProbeSkipped} {
		if r.RecordWithHistory(now,ProbeResult{old,"backup","tcp",now,outcome},h,hk(1),hk(2)) { t.Fatal("nonmeasurement accepted") }
	}
	if !r.RecordWithHistory(now,ProbeResult{old,"backup","tcp",now,ProbePassed},h,hk(1),hk(2)) { t.Fatal("valid probe rejected") }
	if r.RecordWithHistory(now,ProbeResult{old,"backup","tcp",now,ProbePassed},h,hk(1),hk(2)) { t.Fatal("duplicate accepted") }
	if _,err:=r.ResetNetwork(now,"primary");err!=nil { t.Fatal(err) }
	if r.RecordWithHistory(now,ProbeResult{old,"backup","tcp",now,ProbePassed},h,hk(3),hk(2)) { t.Fatal("old generation accepted") }
	if h.Summary(now,hk(1),hk(2),"tcp").Samples!=1 || h.Summary(now,hk(3),hk(2),"tcp").Samples!=0 { t.Fatal("history incorrect") }
	d,err:=r.Tick(context.Background(),now)
	if err!=nil || d.Target!="" || s.calls!=0 { t.Fatal("history affected selection",d,err,s) }
}

func TestHistoryConcurrentAccess(t *testing.T) {
	h:=historyFixture(t,8,8)
	now:=time.Unix(10000,0)
	var wg sync.WaitGroup
	for i:=0;i<32;i++ {
		wg.Add(1)
		go func(i int){
			defer wg.Done()
			h.record(now,hk(1),hk(byte(i+1)),"tcp",now,true)
			h.Summary(now,hk(1),hk(byte(i+1)),"tcp")
			h.ProbeOrder(now,hk(1),"tcp",[]HistoryKey{hk(1),hk(2)})
		}(i)
	}
	wg.Wait()
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.series)>8 { t.Fatal("series limit exceeded") }
}

func TestHistoryOptIn(t *testing.T) {
	p:=DefaultHistoryPolicy()
	if h,err:=NewAvailabilityHistory(p,true);h!=nil||err!=nil { t.Fatal(h,err) }
	p.Enabled=true
	if h,err:=NewAvailabilityHistory(p,false);h!=nil||err!=nil { t.Fatal(h,err) }
	p.MaxSamples=0
	if _,err:=NewAvailabilityHistory(p,true);err==nil { t.Fatal("invalid policy accepted") }
}
