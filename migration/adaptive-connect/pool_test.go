package adaptiveconnect

import (
	"errors"
	"sync"
	"sync/atomic"
	"testing"
)

func poolFixture(t *testing.T)*PoolBudget {
	t.Helper()
	p,err:=NewPoolBudget(PoolPolicy{Enabled:true,MaxTotal:4,MaxPerProfile:3,MaxPending:2},true,[]Outbound{
		{Tag:"a",Tunnel:true},{Tag:"b",Tunnel:true},{Tag:"direct"},{Tag:"hidden",Tunnel:true,Hidden:true},
	})
	if err!=nil { t.Fatal(err) }
	return p
}

func TestPoolAdmissionAndRelease(t *testing.T) {
	p:=poolFixture(t)
	g:=p.Snapshot().Generation
	if _,err:=p.Reserve(g,"direct");err==nil { t.Fatal("direct accepted") }
	if _,err:=p.Reserve(g,"hidden");err==nil { t.Fatal("hidden accepted") }
	a,err:=p.Reserve(g,"a")
	if err!=nil { t.Fatal(err) }
	b,err:=p.Reserve(g,"a")
	if err!=nil { t.Fatal(err) }
	if _,err=p.Reserve(g,"b");!errors.Is(err,ErrPoolLimit) { t.Fatal(err) }
	if err=a.Commit();err!=nil { t.Fatal(err) }
	if err=a.Commit();err!=nil { t.Fatal(err) }
	c,err:=p.Reserve(g,"a")
	if err!=nil { t.Fatal(err) }
	if err=b.Commit();err!=nil { t.Fatal(err) }
	if _,err=p.Reserve(g,"a");!errors.Is(err,ErrPoolLimit) { t.Fatal(err) }
	d,err:=p.Reserve(g,"b")
	if err!=nil { t.Fatal(err) }
	if err=d.Commit();err!=nil { t.Fatal(err) }
	if _,err=p.Reserve(g,"b");!errors.Is(err,ErrPoolLimit) { t.Fatal(err) }
	if s:=p.Snapshot();s.Total!=4||s.Active!=3||s.Pending!=1 { t.Fatal(s) }
	for _,l:=range []*Lease{a,b,c,d} { l.Release();l.Release() }
	if s:=p.Snapshot();s.Total!=0||s.Pending!=0||len(s.ByProfile)!=0 { t.Fatal(s) }
}

func TestPoolNetworkChangeAndClose(t *testing.T) {
	p:=poolFixture(t)
	g:=p.Snapshot().Generation
	a,err:=p.Reserve(g,"a")
	if err!=nil { t.Fatal(err) }
	b,err:=p.Reserve(g,"b")
	if err!=nil { t.Fatal(err) }
	if err=b.Commit();err!=nil { t.Fatal(err) }
	next,err:=p.AdvanceNetwork()
	if err!=nil||next==g { t.Fatal(next,err) }
	if err=a.Commit();!errors.Is(err,ErrPoolStale) { t.Fatal(err) }
	if _,err=p.Reserve(g,"a");!errors.Is(err,ErrPoolStale) { t.Fatal(err) }
	if s:=p.Snapshot();s.Total!=2||s.Pending!=1||s.Active!=1 { t.Fatal(s) }
	c,err:=p.Reserve(next,"a")
	if err!=nil { t.Fatal(err) }
	p.Close()
	if err=c.Commit();!errors.Is(err,ErrPoolClosed) { t.Fatal(err) }
	if _,err=p.Reserve(next,"a");!errors.Is(err,ErrPoolClosed) { t.Fatal(err) }
	a.Release();b.Release();c.Release()
	if s:=p.Snapshot();s.Total!=0||!s.Closed { t.Fatal(s) }
}

func TestPoolConcurrentReservations(t *testing.T) {
	p:=poolFixture(t)
	g:=p.Snapshot().Generation
	var wg sync.WaitGroup
	var count atomic.Int32
	start:=make(chan struct{})
	finish:=make(chan struct{})
	var attempted sync.WaitGroup
	attempted.Add(64)
	for i:=0;i<64;i++ {
		wg.Add(1)
		go func(){
			defer wg.Done()
			<-start
			l,err:=p.Reserve(g,"a")
			if err==nil {
				count.Add(1)
				if err=l.Commit();err!=nil { t.Error(err) }
			} else if !errors.Is(err,ErrPoolLimit) { t.Error(err) }
			attempted.Done()
			if l!=nil { <-finish;l.Release() }
		}()
	}
	close(start)
	attempted.Wait()
	s:=p.Snapshot()
	close(finish)
	wg.Wait()
	if count.Load()<1||count.Load()>3||s.Total!=int(count.Load())||s.Pending!=0 { t.Fatal(count.Load(),s) }
	if s=p.Snapshot();s.Total!=0 { t.Fatal(s) }
}

func TestPoolOptInAndValidation(t *testing.T) {
	p:=DefaultPoolPolicy()
	if b,err:=NewPoolBudget(p,true,nil);b!=nil||err!=nil { t.Fatal(b,err) }
	p.Enabled=true
	if b,err:=NewPoolBudget(p,false,nil);b!=nil||err!=nil { t.Fatal(b,err) }
	if _,err:=NewPoolBudget(p,true,[]Outbound{{Tag:"a",Tunnel:true},{Tag:"a",Tunnel:true}});err==nil { t.Fatal("duplicate accepted") }
	p.MaxPending=0
	if _,err:=NewPoolBudget(p,true,nil);err==nil { t.Fatal("invalid limits accepted") }
}
