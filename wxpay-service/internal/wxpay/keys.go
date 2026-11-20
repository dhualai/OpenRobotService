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
		return nil, fmt.Errorf("parse private key %s: %w", path, describePEMBlock(block))
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
	if key, err := x509.ParsePKCS1PublicKey(block.Bytes); err == nil {
		return key, nil
	}
	// 走到这里说明不是任何合法的公钥编码。直接用 asn1 原始报错（如
	// "tags don't match"）几乎无法定位问题，换成可操作的提示。
	return nil, fmt.Errorf("parse public key %s: %w", path, describePEMBlock(block))
}

// describePEMBlock 把 asn1 层面的报错翻译成可操作的配置提示。
//
// 最常见的误配：把商户 API 证书 apiclient_cert.pem（PEM 类型是 CERTIFICATE）
// 填进 WXPAY_PUBLIC_KEY_PATH。证书里是商户自己的公钥，即使能解析出来，
// 也永远验不过微信支付用平台私钥签的应答与回调。
func describePEMBlock(block *pem.Block) error {
	switch block.Type {
	case "CERTIFICATE":
		detail := "证书内容无法解析"
		if cert, err := x509.ParseCertificate(block.Bytes); err == nil {
			detail = fmt.Sprintf("subject=%q, serial=%s", cert.Subject.String(), cert.SerialNumber.Text(16))
		}
		return fmt.Errorf("PEM 类型是 CERTIFICATE（%s），不是公/私钥文件："+
			"若它是配给 WXPAY_PUBLIC_KEY_PATH 的，请改指向微信支付公钥 wxp_pub.pem / pub_key.pem；"+
			"apiclient_cert.pem 是商户自己的 API 证书（只能配合 apiclient_key.pem 使用），"+
			"用它验不过微信支付用平台私钥签的应答与回调", detail)
	case "PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY":
		return fmt.Errorf("PEM 类型是 %s（私钥），不是公钥文件；"+
			"需用 openssl rsa -in <私钥文件> -pubout -out wxp_pub.pem 导出对应公钥", block.Type)
	case "ENCRYPTED PRIVATE KEY":
		return fmt.Errorf("PEM 类型是 ENCRYPTED PRIVATE KEY（带口令的加密私钥），"+
			"请先解密：openssl pkcs8 -in <文件> -out decrypted.key -nocrypt")
	default:
		return fmt.Errorf("不支持的 PEM 类型 %q（应为 PUBLIC KEY 或 RSA PUBLIC KEY）", block.Type)
	}
}
