// Package downstream 负责把验签解密后的回调结果推送给 FastAPI。
//
// 设计取舍：所有业务状态与持久化由 FastAPI 侧持有（MySQL + SQLAlchemy），
// 本服务不直连数据库。若推送失败，回调入口返回 5xx 让微信按策略重试，
// 同时 FastAPI 侧的兜底查单任务保证最终一致。
package downstream

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// TransferNotifyPayload 是推送给 FastAPI 的商家转账回调结果。
type TransferNotifyPayload struct {
	NotifyID         string `json:"notify_id"`
	EventType        string `json:"event_type"`
	NotifyCreateTime string `json:"notify_create_time"`
	OriginalType     string `json:"original_type"`

	MchID          string `json:"mch_id"`
	AppID          string `json:"appid"`
	OutBillNo      string `json:"out_bill_no"`
	TransferBillNo string `json:"transfer_bill_no"`
	Openid         string `json:"openid"`
	State          string `json:"state"`
	TransferAmount int64  `json:"transfer_amount"`
	TransferRemark string `json:"transfer_remark"`
	FailReason     string `json:"fail_reason"`
	CreateTime     string `json:"create_time"`
	UpdateTime     string `json:"update_time"`
}

// Client 是 FastAPI 内部接口的调用端。
type Client struct {
	baseURL string
	token   string
	path    string
	http    *http.Client
}

// NewClient 构造下游客户端。
func NewClient(baseURL, token, path string, timeout time.Duration) *Client {
	if timeout <= 0 {
		timeout = 3 * time.Second
	}
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		token:   token,
		path:    path,
		http:    &http.Client{Timeout: timeout},
	}
}

// PushTransferNotify 推送转账回调结果。非 2xx 返回错误，由调用方决定是否应答 5xx 让微信重试。
func (c *Client) PushTransferNotify(ctx context.Context, p TransferNotifyPayload) error {
	body, err := json.Marshal(p)
	if err != nil {
		return fmt.Errorf("downstream: marshal payload: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+c.path, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("downstream: build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if c.token != "" {
		req.Header.Set("X-Internal-Token", c.token)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("downstream: send request: %w", err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("downstream: unexpected status %d", resp.StatusCode)
	}
	return nil
}
