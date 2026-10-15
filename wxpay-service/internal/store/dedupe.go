// Package store 提供回调通知的内存去重。
//
// 真正的幂等由 FastAPI 侧 wxpay_notify_log.notify_id 唯一索引保证；
// 本去重只是第一道防线，避免同一通知在极短间隔内被重复推送。
package store

import (
	"sync"
	"time"
)

// Deduper 基于 TTL 的内存去重器。
type Deduper struct {
	mu      sync.Mutex
	seen    map[string]time.Time
	ttl     time.Duration
	maxSize int
}

// NewDeduper 构造去重器。ttl 建议 >= 24h（覆盖微信回调的最长重试窗口）。
func NewDeduper(ttl time.Duration, maxSize int) *Deduper {
	if ttl <= 0 {
		ttl = 24 * time.Hour
	}
	if maxSize <= 0 {
		maxSize = 100000
	}
	return &Deduper{seen: make(map[string]time.Time), ttl: ttl, maxSize: maxSize}
}

// Has 仅判断 id 是否在 TTL 内出现过（只读，不写入）。
//
// 回调处理必须用 Has + Mark 组合：只有在下游推送成功之后才 Mark，
// 否则推送失败时 notify_id 已被占用，微信的重试会被判为「重复」而直接丢弃，
// 该笔转账只能等兜底查单补偿。
func (d *Deduper) Has(id string) bool {
	if id == "" {
		return false
	}
	now := time.Now()
	d.mu.Lock()
	defer d.mu.Unlock()

	t, ok := d.seen[id]
	return ok && now.Sub(t) < d.ttl
}

// Mark 记录 id（写入或刷新时间戳）。
func (d *Deduper) Mark(id string) {
	if id == "" {
		return
	}
	now := time.Now()
	d.mu.Lock()
	defer d.mu.Unlock()

	if len(d.seen) >= d.maxSize {
		d.evictLocked(now)
	}
	d.seen[id] = now
}

// Seen 一步完成「判断 + 记录」；返回 true 表示此前已出现过（重复通知）。
//
// 注意：与 Has+Mark 组合不同，Seen 会把 id 立即占用，因此**不可用于**
// 「成功后再确认」的场景（见 Has 的注释）。保留它主要为兼容既有调用与测试。
func (d *Deduper) Seen(id string) bool {
	if d.Has(id) {
		return true
	}
	d.Mark(id)
	return false
}

func (d *Deduper) evictLocked(now time.Time) {
	for id, t := range d.seen {
		if now.Sub(t) >= d.ttl {
			delete(d.seen, id)
		}
	}
	// 仍然满则清空一半（极端情况下的兜底，正常流量不会触发）。
	if len(d.seen) >= d.maxSize {
		n := 0
		for id := range d.seen {
			delete(d.seen, id)
			n++
			if n >= d.maxSize/2 {
				break
			}
		}
	}
}
