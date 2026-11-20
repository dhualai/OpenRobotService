package store

import (
	"testing"
	"time"
)

func TestDeduper(t *testing.T) {
	d := NewDeduper(time.Hour, 100)

	if d.Seen("EV-001") {
		t.Fatal("first occurrence must not be duplicate")
	}
	if !d.Seen("EV-001") {
		t.Fatal("second occurrence must be duplicate")
	}
	if d.Seen("EV-002") {
		t.Fatal("different id must not be duplicate")
	}
}

func TestDeduperTTLExpiry(t *testing.T) {
	d := NewDeduper(10*time.Millisecond, 100)
	if d.Seen("EV-003") {
		t.Fatal("first occurrence must not be duplicate")
	}
	time.Sleep(20 * time.Millisecond)
	if d.Seen("EV-003") {
		t.Fatal("entry should expire after TTL")
	}
}

// Has 只判断不写入，Mark 才写入 —— 回调处理依赖这个语义：
// 推送下游失败时不 Mark，微信的重试才不会被误判为重复通知。
func TestDeduperHasDoesNotMark(t *testing.T) {
	d := NewDeduper(time.Hour, 100)
	if d.Has("EV-010") || d.Has("EV-010") {
		t.Fatal("Has must not mark the id")
	}
	d.Mark("EV-010")
	if !d.Has("EV-010") {
		t.Fatal("Mark must make Has return true")
	}
}

func TestDeduperMarkEmptyID(t *testing.T) {
	d := NewDeduper(time.Hour, 100)
	d.Mark("")
	if d.Has("") {
		t.Fatal("empty id must never be marked")
	}
}

func TestDeduperEmptyID(t *testing.T) {
	d := NewDeduper(time.Hour, 100)
	if d.Seen("") || d.Seen("") {
		t.Fatal("empty id should never be treated as duplicate")
	}
}
