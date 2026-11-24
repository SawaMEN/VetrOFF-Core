package adaptiveconnect

import (
	"testing"
	"time"
)

func TestControllerHistoryOrderAndIsolation(t *testing.T) {
	c,_,_,now:=controllerFixture(t)
	active:=map[string]string{"tcp":"a","udp":"b"}
	if _,err:=c.ResetNetwork(now,hk(9),active);err!=nil {t.Fatal(err)}
	ticket,err:=c.BeginProbe("tcp")
	if err!=nil {t.Fatal(err)}
	for i:=0;i<3;i++ {
		at:=now.Add(time.Duration(i)*time.Second)
		if !c.Record(at,ticket,"a",at,ProbeFailed)||!c.Record(at,ticket,"b",at,ProbePassed) {t.Fatal("record rejected")}
	}
	now=now.Add(3*time.Second)
	order,err:=c.ProbeOrder(now,"tcp",[]string{"a","b"})
	if err!=nil||order[0]!="b"||order[1]!="a" {t.Fatal(order,err)}
	if _,err=c.ProbeOrder(now,"tcp",[]string{"a","a"});err==nil {t.Fatal("duplicate order accepted")}
	if _,err=c.ProbeOrder(now,"tcp",[]string{"a"});err==nil {t.Fatal("partial order accepted")}
	if c.HistorySnapshot(now,"udp")["b"].Samples!=0 {t.Fatal("TCP history affected UDP")}
	if _,err=c.ResetNetwork(now,hk(8),active);err!=nil {t.Fatal(err)}
	order,err=c.ProbeOrder(now,"tcp",[]string{"a","b"})
	if err!=nil||order[0]!="a" {t.Fatal(order,err)}
	if c.HistorySnapshot(now,"tcp")["b"].Samples!=0 {t.Fatal("network history mixed")}
}
