package wxpay

import (
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"os"
)

// LoadPrivateKey 从 PEM 文件加载商户 API 私钥，兼容 PKCS#1 与 PKCS#8 两种编码。
func LoadPrivateKey(path string) (*rsa.PrivateKey, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read private key file: %w", err)
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return nil, fmt.Errorf("no PEM block found in %s", path)
	}

	if key, err := x509.ParsePKCS1PrivateKey(block.Bytes); err == nil {
		return key, nil
	}
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse private key %s: %w", path, err)
	}
	key, ok := parsed.(*rsa.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("private key %s is not RSA", path)
	}
	return key, nil
}

// LoadPublicKey 从 PEM 文件加载 RSA 公钥，兼容 PKIX（PUBLIC KEY）与 PKCS#1（RSA PUBLIC KEY）。
func LoadPublicKey(path string) (*rsa.PublicKey, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read public key file: %w", err)
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return nil, fmt.Errorf("no PEM block found in %s", path)
	}

	if key, err := x509.ParsePKIXPublicKey(block.Bytes); err == nil {
		pub, ok := key.(*rsa.PublicKey)
		if !ok {
			return nil, fmt.Errorf("public key %s is not RSA", path)
		}
		return pub, nil
	}
	key, err := x509.ParsePKCS1PublicKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse public key %s: %w", path, err)
	}
	return key, nil
}
