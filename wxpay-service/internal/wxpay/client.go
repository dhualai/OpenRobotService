package wxpay

import (
	"bytes"
	"context"
	"crypto/rsa"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const defaultBaseURL = "https://api.mch.weixin.qq.com"

// responseTimestampSkew 是应答验签允许的时间偏差。
//
// 只验签不校验时间窗等于接受任意时刻的重放报文。5 分钟是微信官方 SDK 的通行取值。
// ⚠️ 前提：服务器必须开启 NTP 时钟同步，否则偏差过大会导致所有请求被拒。
const responseTimestampSkew = 5 * time.Minute

// Client 是 APIv3 的 HTTP 客户端：自动签名请求、自动验签应答。
type Client struct {
	baseURL string

	cred     Credential
	apiV3Key string

	// wechatPaySerial 是请求头 Wechatpay-Serial 的值。
	// 使用「微信支付公钥」模式时填微信支付公钥 ID（PUB_KEY_ID_xxx）。
	wechatPaySerial string

	keys KeyProvider
	http *http.Client
}

// ClientConfig 构造 Client 所需参数。
type ClientConfig struct {
	BaseURL         string
	MchID           string
	CertSerialNo    string
	PrivateKey      *rsa.PrivateKey
	APIv3Key        string
	WechatPaySerial string
	Keys            KeyProvider
	Timeout         time.Duration
}

// NewClient 构造 APIv3 客户端。
func NewClient(cfg ClientConfig) (*Client, error) {
	if cfg.PrivateKey == nil {
		return nil, errors.New("wxpay: merchant private key is required")
	}
	if cfg.Keys == nil {
		return nil, errors.New("wxpay: KeyProvider is required")
	}
	base := strings.TrimRight(defaultString(cfg.BaseURL, defaultBaseURL), "/")
	timeout := cfg.Timeout
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	return &Client{
		baseURL: base,
		cred: Credential{
			MchID:      cfg.MchID,
			SerialNo:   cfg.CertSerialNo,
			PrivateKey: cfg.PrivateKey,
		},
		apiV3Key:        cfg.APIv3Key,
		wechatPaySerial: cfg.WechatPaySerial,
		keys:            cfg.Keys,
		http:            &http.Client{Timeout: timeout},
	}, nil
}

// APIError 表示微信支付返回的非 2xx 应答。
type APIError struct {
	StatusCode int
	Code       string      // 错误码，如 SYSTEM_ERROR / PARAM_ERROR
	Message    string      // 错误描述
	Detail     interface{} // detail 字段原文
	RequestID  string      // Request-ID，排障必用
	Body       string      // 原始应答体（已脱敏风险由调用方控制，不落日志）
}

func (e *APIError) Error() string {
	return fmt.Sprintf("wxpay: api error status=%d code=%s message=%s request_id=%s",
		e.StatusCode, e.Code, e.Message, e.RequestID)
}

// IsSystemError 判断是否为系统错误（此类错误必须查单后再决定是否重试）。
func (e *APIError) IsSystemError() bool {
	return e.Code == "SYSTEM_ERROR" || e.StatusCode >= 500
}

// Do 发起一次 APIv3 请求。
//
// path 必须以 / 开头，可包含 query string（签名串中的 URL 需包含 query）。
// body 为 nil 时不发送请求体（GET 场景）；否则按 JSON 序列化。
// 返回解密/验签通过后的原始应答字节。
func (c *Client) Do(ctx context.Context, method, path string, body interface{}) ([]byte, error) {
	var payload []byte
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("wxpay: marshal request body: %w", err)
		}
		payload = b
	}

	authorization, err := BuildAuthorization(c.cred, method, path, payload)
	if err != nil {
		return nil, err
	}

	var reader io.Reader
	if payload != nil {
		reader = bytes.NewReader(payload)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, reader)
	if err != nil {
		return nil, fmt.Errorf("wxpay: build request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json; charset=utf-8")
	req.Header.Set("Authorization", authorization)
	req.Header.Set("User-Agent", "OpenRobotService-wxpay-service")
	if c.wechatPaySerial != "" {
		req.Header.Set("Wechatpay-Serial", c.wechatPaySerial)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("wxpay: send request: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("wxpay: read response: %w", err)
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, newAPIError(resp, respBody)
	}

	// 2xx 必须验签后才可信任应答内容。
	if err := c.verifyResponse(resp.Header, respBody); err != nil {
		return nil, err
	}
	return respBody, nil
}

func (c *Client) verifyResponse(header http.Header, body []byte) error {
	serial := header.Get("Wechatpay-Serial")
	signature := header.Get("Wechatpay-Signature")
	// 204 No Content 场景没有签名头，视为无需验签。
	if serial == "" || signature == "" {
		if len(body) == 0 {
			return nil
		}
		return fmt.Errorf("wxpay: missing signature headers on response")
	}
	pub, err := c.keys.PublicKeyBySerial(serial)
	if err != nil {
		return err
	}
	timestamp := header.Get("Wechatpay-Timestamp")
	nonce := header.Get("Wechatpay-Nonce")

	// 时间窗校验：拒绝超出偏差范围的应答（防重放）。
	ts, err := ParseTimestamp(timestamp)
	if err != nil {
		return err
	}
	if skew := time.Since(time.Unix(ts, 0)); skew > responseTimestampSkew || skew < -responseTimestampSkew {
		return fmt.Errorf("wxpay: response timestamp out of window: %q (skew %s)", timestamp, skew)
	}

	return VerifySignature(pub, timestamp, nonce, string(body), signature)
}

func newAPIError(resp *http.Response, body []byte) error {
	apiErr := &APIError{
		StatusCode: resp.StatusCode,
		RequestID:  resp.Header.Get("Request-ID"),
		Body:       string(body),
	}
	var parsed struct {
		Code    string      `json:"code"`
		Message string      `json:"message"`
		Detail  interface{} `json:"detail"`
	}
	if err := json.Unmarshal(body, &parsed); err == nil {
		apiErr.Code = parsed.Code
		apiErr.Message = parsed.Message
		apiErr.Detail = parsed.Detail
	}
	if apiErr.Code == "" {
		apiErr.Code = "UNKNOWN"
		apiErr.Message = string(body)
	}
	return apiErr
}

func defaultString(v, def string) string {
	if strings.TrimSpace(v) == "" {
		return def
	}
	return v
}
