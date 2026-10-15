package wxpay

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha1"
	"encoding/base64"
	"fmt"
)

// base64Encode / base64Decode 是标准 base64 编解码包装。
func base64Encode(b []byte) string { return base64.StdEncoding.EncodeToString(b) }

func base64Decode(s string) ([]byte, error) {
	return base64.StdEncoding.DecodeString(s)
}

// DecryptResource 使用 APIv3 密钥解密回调/应答中的 AEAD_AES_256_GCM 密文。
//
// 对应 resource.ciphertext / resource.nonce / resource.associated_data 三个字段。
func DecryptResource(apiV3Key, nonce, associatedData, ciphertext string) ([]byte, error) {
	key := []byte(apiV3Key)
	if len(key) != 32 {
		return nil, fmt.Errorf("wxpay: APIv3 key must be 32 bytes, got %d", len(key))
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, fmt.Errorf("wxpay: init aes cipher: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, fmt.Errorf("wxpay: init gcm: %w", err)
	}
	raw, err := base64Decode(ciphertext)
	if err != nil {
		return nil, fmt.Errorf("wxpay: decode ciphertext: %w", err)
	}
	plain, err := gcm.Open(nil, []byte(nonce), raw, []byte(associatedData))
	if err != nil {
		// 解密失败通常意味着 APIv3 密钥配置错误或报文被篡改。
		return nil, fmt.Errorf("wxpay: aes-gcm decrypt failed: %w", err)
	}
	return plain, nil
}

// EncryptResource 使用 APIv3 密钥做 AEAD_AES_256_GCM 加密，返回 base64 密文。
//
// 与 DecryptResource 互为逆操作。生产侧不会用到（回调由微信加密后下发），
// 仅供本地 mock 服务与单元测试构造回调报文。nonce 必须是 12 字节。
func EncryptResource(apiV3Key, nonce, associatedData string, plaintext []byte) (string, error) {
	key := []byte(apiV3Key)
	if len(key) != 32 {
		return "", fmt.Errorf("wxpay: APIv3 key must be 32 bytes, got %d", len(key))
	}
	if len(nonce) != 12 {
		return "", fmt.Errorf("wxpay: aes-gcm nonce must be 12 bytes, got %d", len(nonce))
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", fmt.Errorf("wxpay: init aes cipher: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", fmt.Errorf("wxpay: init gcm: %w", err)
	}
	out := gcm.Seal(nil, []byte(nonce), plaintext, []byte(associatedData))
	return base64Encode(out), nil
}

// EncryptOAEP 使用微信支付公钥做 RSA-OAEP 加密（SHA-1），返回 base64 密文。
//
// 用于 user_name 等敏感字段。单笔转账 ≥ 2000 元时必须传加密后的收款人姓名。
func EncryptOAEP(pub *rsa.PublicKey, plaintext string) (string, error) {
	if pub == nil {
		return "", fmt.Errorf("wxpay: wechat pay public key is required for encryption")
	}
	if plaintext == "" {
		return "", fmt.Errorf("wxpay: plaintext is empty")
	}
	out, err := rsa.EncryptOAEP(sha1.New(), rand.Reader, pub, []byte(plaintext), nil)
	if err != nil {
		return "", fmt.Errorf("wxpay: rsa-oaep encrypt: %w", err)
	}
	return base64Encode(out), nil
}
