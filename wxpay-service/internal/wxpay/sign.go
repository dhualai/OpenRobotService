package wxpay

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"fmt"
	"math/big"
	"strconv"
	"time"
)

// AuthType 是 APIv3 的 Authorization 认证类型。
const AuthType = "WECHATPAY2-SHA256-RSA2048"

// Credential 是发起请求签名所需的商户凭据。
type Credential struct {
	MchID      string          // 商户号
	SerialNo   string          // 商户 API 证书序列号
	PrivateKey *rsa.PrivateKey // 商户 API 私钥
}

const nonceLetters = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

// RandomNonce 生成 32 位随机串。
func RandomNonce() string {
	b := make([]byte, 32)
	max := big.NewInt(int64(len(nonceLetters)))
	for i := range b {
		n, err := rand.Int(rand.Reader, max)
		if err != nil {
			// crypto/rand 读取失败属于致命错误，不允许降级为可预测随机。
			panic("wxpay: crypto/rand unavailable: " + err.Error())
		}
		b[i] = nonceLetters[n.Int64()]
	}
	return string(b)
}

// BuildRequestSignMessage 构造【请求】签名串（五行，每行以 \n 结尾）：
//
//	HTTP请求方法\nURL\n请求时间戳\n请求随机串\n请求报文主体\n
func BuildRequestSignMessage(method, urlPath string, timestamp int64, nonce string, body []byte) string {
	return fmt.Sprintf("%s\n%s\n%d\n%s\n%s\n", method, urlPath, timestamp, nonce, string(body))
}

// BuildResponseSignMessage 构造【应答/回调】验签串（三行，每行以 \n 结尾）：
//
//	应答时间戳\n应答随机串\n应答报文主体\n
//
// 注意：与请求签名串格式不同，二者不可混用。
func BuildResponseSignMessage(timestamp, nonce, body string) string {
	return fmt.Sprintf("%s\n%s\n%s\n", timestamp, nonce, body)
}

// Sign 对任意消息做 SHA256withRSA 签名，返回 base64 结果。
func Sign(cred Credential, message string) (string, error) {
	if cred.PrivateKey == nil {
		return "", fmt.Errorf("wxpay: merchant private key is required")
	}
	hashed := sha256.Sum256([]byte(message))
	sig, err := rsa.SignPKCS1v15(rand.Reader, cred.PrivateKey, crypto.SHA256, hashed[:])
	if err != nil {
		return "", fmt.Errorf("wxpay: sign message: %w", err)
	}
	return base64Encode(sig), nil
}

// SignMessage 对【请求】签名串签名。
func SignMessage(cred Credential, method, urlPath string, timestamp int64, nonce string, body []byte) (string, error) {
	return Sign(cred, BuildRequestSignMessage(method, urlPath, timestamp, nonce, body))
}

// SignResponseMessage 对【应答/回调】验签串签名（主要用于单元测试与联调自检）。
func SignResponseMessage(cred Credential, timestamp, nonce, body string) (string, error) {
	return Sign(cred, BuildResponseSignMessage(timestamp, nonce, body))
}

// BuildAuthorization 生成 Authorization 请求头值。
//
//	WECHATPAY2-SHA256-RSA2048 mchid="...",nonce_str="...",signature="...",timestamp="...",serial_no="..."
func BuildAuthorization(cred Credential, method, urlPath string, body []byte) (string, error) {
	ts := time.Now().Unix()
	nonce := RandomNonce()
	sig, err := SignMessage(cred, method, urlPath, ts, nonce, body)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf(
		`%s mchid="%s",nonce_str="%s",signature="%s",timestamp="%d",serial_no="%s"`,
		AuthType, cred.MchID, nonce, sig, ts, cred.SerialNo,
	), nil
}

// VerifySignature 校验微信支付应答/回调签名。
func VerifySignature(pub *rsa.PublicKey, timestamp, nonce, body, signature string) error {
	if pub == nil {
		return fmt.Errorf("wxpay: public key for verification is nil")
	}
	if timestamp == "" || nonce == "" || signature == "" {
		return fmt.Errorf("wxpay: incomplete signature headers")
	}
	hashed := sha256.Sum256([]byte(BuildResponseSignMessage(timestamp, nonce, body)))
	sig, err := base64Decode(signature)
	if err != nil {
		return fmt.Errorf("wxpay: decode signature: %w", err)
	}
	if err := rsa.VerifyPKCS1v15(pub, crypto.SHA256, hashed[:], sig); err != nil {
		return fmt.Errorf("wxpay: signature verification failed: %w", err)
	}
	return nil
}

// ParseTimestamp 解析微信支付的时间戳头，失败返回错误而非静默取 0。
func ParseTimestamp(raw string) (int64, error) {
	v, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("wxpay: invalid timestamp %q", raw)
	}
	return v, nil
}
